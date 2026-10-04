import base64
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from backend import writer
from backend import apply_card_skin


DEVICE_ID = "FIXTURE-FLASH-PHONE"
CARD_ID = "AbCdEfGhIjKlMnOpQrStUvWxYz0="

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def cache_result(states, *, cleanup=True):
    complete = all(state in ("removed", "absent") for _, state in states)
    return {"exitCode": 0 if complete and cleanup else 2, "targetGatePassed": True,
            "operation": {"ok": complete and cleanup, "cacheRemovalVersion": 1, "safeArguments": True,
                          "directoryListingComplete": any(state == "absent" for _, state in states),
                          "cleanupComplete": cleanup, "booksRestore": {"ok": cleanup},
                          "allTargetsInvalidated": complete,
                          "targets": [{"leaf": leaf, "state": state} for leaf, state in states]}}


class CardFlashTests(unittest.TestCase):
    def test_legacy_write_lock_rejects_competing_process_and_releases_after_exit(self) -> None:
        # Independently reproduce the old version's path to catch accidental
        # namespace changes that would let old and renamed apps write together.
        command = [sys.executable, "-u", "-c",
                   "import fcntl, hashlib, os, sys; from pathlib import Path; "
                   "directory = Path('/tmp') / f'aircard-device-locks-{os.getuid()}'; "
                   "directory.mkdir(mode=0o700, parents=True, exist_ok=True); "
                   "name = hashlib.sha256(sys.argv[1].lower().encode('utf-8')).hexdigest() + '.lock'; "
                   "stream = (directory / name).open('a+b'); "
                   "fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); "
                   "print('locked', flush=True); sys.stdin.readline(); stream.close()",
                   DEVICE_ID]
        process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[2],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            output = io.StringIO()
            with patch.object(writer, "_flash_unlocked", return_value=True) as write, redirect_stdout(output):
                self.assertFalse(writer.cmd_flash(DEVICE_ID.lower(), CARD_ID, "unused.png"))
            write.assert_not_called()
            self.assertIn("Another apple-wallet-card-skinner process", json.loads(output.getvalue())["message"])
        finally:
            process.communicate("release\n", timeout=5)
        with patch.object(writer, "_flash_unlocked", return_value=True) as write:
            self.assertTrue(writer.cmd_flash(DEVICE_ID, CARD_ID, "unused.png"))
        write.assert_called_once()

    def test_backend_rejects_unsafe_device_or_card_identifiers_before_writing(self) -> None:
        pairs = [(value, CARD_ID) for value in ("../device", "--udid", "device/name", "device;command", "", "x" * 129)]
        pairs += [(DEVICE_ID, value) for value in ("../card", "a" * 20 + "/other", "a" * 20 + ".pkpass", "short", "x" * 65)]
        for udid, card_id in pairs:
            with self.subTest(udid=udid, card_id=card_id), patch.object(writer, "_flash_unlocked") as write, redirect_stdout(io.StringIO()):
                self.assertFalse(writer.cmd_flash(udid, card_id, "unused.png"))
                write.assert_not_called()

    def test_cache_removal_moves_link_and_required_companion_payload(self) -> None:
        successful = {
            "exitCode": 0,
            "targetGatePassed": True,
            "operation": {"ok": True},
        }
        with (
            patch.object(apply_card_skin, "native", side_effect=lambda command, *_args:
                         cache_result([("FrontFace", "removed")]) if command == "finish-moved-removal" else successful),
            patch.object(apply_card_skin, "run_json", return_value={"exitCode": 0, "ok": True}) as transfer,
        ):
            result = apply_card_skin.remove_files(
                "device", "/protected/card.cache", ["FrontFace"], retries=1
            )

        self.assertTrue(result)
        command = transfer.call_args.args[0]
        self.assertEqual(len(command), 6)
        self.assertIn("/airlift-link-", command[4])
        self.assertTrue(command[5].endswith("/removed-0"))

    def test_cache_fallback_requires_every_leaf_and_retries_only_unresolved(self):
        leaves = ["FrontFace", "PlaceHolder", "Preview"]
        for final_verified, expected in [(set(), False), ({"PlaceHolder"}, True)]:
            with self.subTest(expected=expected), patch.object(apply_card_skin, "_remove_files_batch", side_effect=[
                ({"FrontFace", "Preview"}, True), (final_verified, True),
            ]) as batch:
                self.assertEqual(apply_card_skin.remove_files("phone", "/cache", leaves, retries=1), expected)
                self.assertEqual([call.args[2] for call in batch.call_args_list], [leaves, ["PlaceHolder"]])

    def test_cache_all_absent_is_success_only_with_complete_native_evidence(self):
        leaves = ["FrontFace", "PlaceHolder", "Preview"]
        result = cache_result([(leaf, "absent") for leaf in leaves])
        self.assertEqual(apply_card_skin._cache_removal_result(result, leaves), (set(leaves), True))
        result["operation"]["directoryListingComplete"] = False
        self.assertEqual(apply_card_skin._cache_removal_result(result, leaves), (set(), False))
        result["operation"]["directoryListingComplete"] = True
        result["operation"]["targets"][0]["state"] = "unverified"
        self.assertEqual(apply_card_skin._cache_removal_result(result, leaves), (set(), False))

    def test_cache_protocol_rejects_legacy_malformed_and_incomplete_cleanup(self):
        import copy
        leaves = ["FrontFace", "Preview"]
        valid = cache_result([(leaf, "removed") for leaf in leaves])
        invalid = [
            {"exitCode": 0, "targetGatePassed": True, "operation": {"ok": True, "alreadyAbsentCount": 2}},
            cache_result([(leaf, "removed") for leaf in leaves], cleanup=False),
        ]
        for mutate in [
            lambda value: value["operation"].pop("cacheRemovalVersion"),
            lambda value: value["operation"].update(safeArguments=False),
            lambda value: value["operation"].update(cacheRemovalVersion=True),
            lambda value: value["operation"]["targets"].reverse(),
            lambda value: value["operation"]["targets"].pop(),
            lambda value: value["operation"]["targets"].append({"leaf": "other", "state": "removed"}),
            lambda value: value["operation"]["targets"][1].update(leaf="FrontFace"),
            lambda value: value["operation"]["booksRestore"].update(ok=False),
            lambda value: value.update(exitCode=2),
        ]:
            value = copy.deepcopy(valid)
            mutate(value)
            invalid.append(value)
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(apply_card_skin._cache_removal_result(value, leaves), (set(), False))

    def test_cache_leaf_validation_precedes_all_device_calls(self):
        for leaves in [["FrontFace", "../Preview"], ["FrontFace", ""], ["FrontFace", None]]:
            with self.subTest(leaves=leaves), patch.object(apply_card_skin, "_remove_files_batch") as batch:
                with self.assertRaises(ValueError):
                    apply_card_skin.remove_files("phone", "/cache", leaves)
                batch.assert_not_called()

    def test_cache_atc_failure_still_verifies_partial_moves_and_restores_books(self):
        for failure in [{"exitCode": 5, "ok": False}, subprocess.TimeoutExpired("fixture-atc", 1)]:
            completed = cache_result([("FrontFace", "removed"), ("Preview", "unverified")])
            successful = {"exitCode": 0, "targetGatePassed": True, "operation": {"ok": True}}
            def native(command, *_args):
                return completed if command == "finish-moved-removal" else successful
            with self.subTest(failure=failure), patch.object(apply_card_skin, "native", side_effect=native) as call, patch.object(
                    apply_card_skin, "run_json", side_effect=failure if isinstance(failure, Exception) else None,
                    return_value=failure):
                verified, cleaned = apply_card_skin._remove_files_batch("phone", "/cache", ["FrontFace", "Preview"])
                self.assertEqual(verified, {"FrontFace"})
                self.assertTrue(cleaned)
                self.assertEqual(call.call_args.args[0], "finish-moved-removal")
                self.assertEqual(call.call_args.args[-3:], ("2", "FrontFace", "Preview"))

    def test_cache_uncertain_completion_attempts_cleanup_and_never_succeeds(self):
        successful = {"exitCode": 0, "targetGatePassed": True, "operation": {"ok": True, "cleanupComplete": True}}
        for finish in [RuntimeError("lost completion"), {"operation": {}}, cache_result([("FrontFace", "removed")], cleanup=False)]:
            def native(command, *_args):
                if command == "finish-moved-removal":
                    if isinstance(finish, Exception):
                        raise finish
                    return finish
                return successful
            with self.subTest(finish=finish), patch.object(apply_card_skin, "native", side_effect=native) as call, patch.object(
                    apply_card_skin, "run_json", return_value={"exitCode": 0, "ok": True}):
                self.assertFalse(apply_card_skin.remove_files("phone", "/cache", ["FrontFace"]))
                self.assertEqual(call.call_args.args[0], "finish-write")
                self.assertEqual(sum(item.args[0] == "stage" for item in call.call_args_list), 1)

    def test_cache_stage_failure_restores_books_when_cleanup_authorized(self):
        successful = {"exitCode": 0, "targetGatePassed": True, "operation": {"ok": True, "cleanupComplete": True}}
        for stage in [RuntimeError("unknown stage"),
                      {"exitCode": 0, "targetGatePassed": True, "operation": None},
                      {"exitCode": 2, "targetGatePassed": True,
                                                        "operation": {"ok": False, "cleanupAuthorized": True}}]:
            def native(command, *_args):
                if command == "stage":
                    if isinstance(stage, Exception):
                        raise stage
                    return stage
                return successful
            with self.subTest(stage=stage), patch.object(apply_card_skin, "native", side_effect=native) as call:
                self.assertFalse(apply_card_skin.remove_files("phone", "/cache", ["FrontFace"], retries=1))
                self.assertEqual(call.call_args.args[0], "finish-write")

    def test_flash_writes_pdf_and_removes_rendered_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            write_file = Mock(return_value=True)
            remove_files = Mock(return_value=True)

            with (
                patch.object(writer, "write_file", write_file),
                patch.object(writer, "write_files_batch", Mock(return_value=False)),
                patch.object(writer, "remove_files", remove_files),
                redirect_stdout(io.StringIO()),
            ):
                result = writer.cmd_flash(DEVICE_ID, CARD_ID, str(image_path))

        self.assertTrue(result)

        writes = [call.args for call in write_file.call_args_list]
        pass_assets = {
            leaf: payload
            for _, target, leaf, payload in writes
            if target.endswith(".pkpass")
        }
        self.assertEqual(
            set(pass_assets),
            {
                "cardBackgroundCombined@3x.png",
                "cardBackgroundCombined@2x.png",
                "cardBackgroundCombined.pdf",
            },
        )
        self.assertTrue(pass_assets["cardBackgroundCombined.pdf"].startswith(b"%PDF-"))

        removals = [call.args for call in remove_files.call_args_list]
        for extension in (".cache", ".pkcache"):
            self.assertIn((DEVICE_ID, f"/var/mobile/Library/Passes/Cards/{CARD_ID}{extension}", list(writer.CACHE_FILES)), removals)

    def test_flash_fails_when_wallet_cache_cannot_be_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            output = io.StringIO()
            with (
                patch.object(writer, "write_files_batch", return_value=True),
                patch.object(writer, "remove_files", side_effect=[True, False]),
                redirect_stdout(output),
            ):
                result = writer.cmd_flash(DEVICE_ID, CARD_ID, str(image_path))
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(result)
        self.assertEqual(messages[-1]["type"], "error")

    def test_flash_reports_failure_when_an_asset_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            write_file = Mock(
                side_effect=[True, True, False, True, True, True, True, True, True]
            )
            output = io.StringIO()

            with (
                patch.object(writer, "write_files_batch", Mock(return_value=False)),
                patch.object(writer, "write_file", write_file),
                patch.object(writer, "remove_files", Mock(return_value=True)),
                redirect_stdout(output),
            ):
                result = writer.cmd_flash(DEVICE_ID, CARD_ID, str(image_path))

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(result)
        self.assertEqual(messages[-1]["type"], "error")
        self.assertFalse(any(message["type"] == "success" for message in messages))

    def test_flash_streams_per_file_progress_and_finishes_at_total(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            output = io.StringIO()

            def batch(_udid, _target, files, retries=3, progress_callback=None):
                self.assertEqual(retries, 3)
                self.assertIsNotNone(progress_callback)
                for index, (leaf, _payload) in enumerate(files, 1):
                    progress_callback({
                        "type": "atc_progress",
                        "index": index,
                        "total": len(files),
                        "leaf": leaf,
                    })
                return True

            with (
                patch.object(writer, "write_files_batch", side_effect=batch),
                patch.object(writer, "remove_files", return_value=True),
                redirect_stdout(output),
            ):
                result = writer.cmd_flash(DEVICE_ID, CARD_ID, str(image_path))

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        written = [
            message for message in messages
            if message.get("message", "").startswith("Writing cardBackground")
        ]
        self.assertTrue(result)
        self.assertEqual([message["step"] for message in written], [1, 2, 3])
        self.assertTrue(all(message["total"] == 5 for message in written))
        self.assertEqual(messages[-1]["type"], "success")
        self.assertEqual(messages[-1]["step"], messages[-1]["total"])

    def test_flash_fallback_emits_per_file_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            output = io.StringIO()
            with (
                patch.object(writer, "write_files_batch", return_value=False),
                patch.object(writer, "write_file", return_value=True),
                patch.object(writer, "remove_files", return_value=True),
                redirect_stdout(output),
            ):
                result = writer.cmd_flash(DEVICE_ID, CARD_ID, str(image_path))

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        fallback = [
            message for message in messages
            if message.get("message", "").startswith("[Fallback] Writing")
        ]
        self.assertTrue(result)
        self.assertEqual(len(fallback), 3)
        self.assertEqual([message["step"] for message in fallback], [1, 2, 3])
        self.assertTrue(all(message["total"] == 5 for message in fallback))
        self.assertEqual(messages[-1]["step"], 5)
        self.assertEqual(messages[-1]["total"], 5)

    def test_flash_reports_failure_when_pdf_conversion_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "card.png"
            image_path.write_bytes(PNG_1X1)
            write_file = Mock(return_value=True)
            output = io.StringIO()

            with (
                patch.object(writer, "write_file", write_file),
                patch.object(
                    writer,
                    "build_card_assets",
                    side_effect=subprocess.CalledProcessError(1, ["sips"]),
                ),
                redirect_stdout(output),
            ):
                result = writer.cmd_flash(DEVICE_ID, CARD_ID, str(image_path))

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(result)
        self.assertEqual(messages[-1]["type"], "error")
        write_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
