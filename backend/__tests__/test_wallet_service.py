import hashlib
import json
import plistlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from backend import APP_DATA_DIR_NAME, APP_ID, APP_NAME
from backend.wallet_service import WalletService, empty_catalog
from backend.wallet_store import WalletStore
from __tests__.fixtures import A, B, C, FIRST, SECOND, Process, wait_for


class WalletStoreTests(unittest.TestCase):
    def test_new_install_uses_renamed_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch("backend.wallet_store.Path.home", return_value=home):
                store = WalletStore()
            self.addCleanup(store.close)
            self.assertEqual(store.root, home / "Library/Application Support" / APP_DATA_DIR_NAME)
            self.assertTrue(store.path.is_file())
            self.assertFalse((home / "Library/Application Support/AirCard").exists())
            self.assertFalse((home / "Library/Application Support" / APP_ID).exists())

    def test_existing_aircard_store_is_reused_without_moving_artwork(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            legacy = home / "Library/Application Support/AirCard"
            first = WalletStore(legacy, home)
            artwork = first.write_artwork(b"existing artwork")
            first.data["selected_udid"] = "saved-phone"
            first.records("saved-phone").append({"id": A, "selected": False, "imagePath": str(artwork)})
            first.save()
            first.close()
            with patch("backend.wallet_store.Path.home", return_value=home):
                restored = WalletStore()
            self.addCleanup(restored.close)
            self.assertEqual(restored.root, legacy)
            self.assertEqual(restored.data["selected_udid"], "saved-phone")
            self.assertEqual(restored.records("saved-phone")[0]["imagePath"], str(artwork))
            self.assertEqual(artwork.read_bytes(), b"existing artwork")
            self.assertFalse((legacy.parent / APP_DATA_DIR_NAME).exists())

    def test_slug_store_precedes_aircard_and_keeps_artwork_and_lock_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            support = home / "Library/Application Support"
            old = WalletStore(support / "AirCard", home)
            old.data["selected_udid"] = "older-phone"
            old.save()
            old.close()
            previous = WalletStore(support / APP_ID, home)
            artwork = previous.write_artwork(b"existing artwork")
            previous.data["selected_udid"] = "saved-phone"
            previous.records("saved-phone").append({"id": A, "confirmed": True, "selected": False, "imagePath": str(artwork)})
            previous.save()
            with patch("backend.wallet_store.Path.home", return_value=home):
                # The renamed default must contend on the same legacy lock;
                # creating a separate store would lose data and permit overlap.
                with self.assertRaisesRegex(RuntimeError, f"Another {APP_NAME}"):
                    WalletStore()
            self.assertFalse((support / APP_DATA_DIR_NAME).exists())
            previous.close()
            with patch("backend.wallet_store.Path.home", return_value=home):
                restored = WalletStore()
            self.addCleanup(restored.close)
            self.assertEqual(restored.root, support / APP_ID)
            self.assertEqual(restored.data["selected_udid"], "saved-phone")
            self.assertEqual(restored.records("saved-phone")[0]["imagePath"], str(artwork))
            self.assertEqual(artwork.read_bytes(), b"existing artwork")
            self.assertEqual(json.loads(old.path.read_text())["selected_udid"], "older-phone")
            self.assertFalse((support / APP_DATA_DIR_NAME).exists())

    def test_renamed_data_directory_takes_precedence_even_when_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            support = home / "Library/Application Support"
            legacy = WalletStore(support / "AirCard", home)
            legacy.data["selected_udid"] = "old-phone"
            legacy.save()
            legacy.close()
            slug = WalletStore(support / APP_ID, home)
            slug.data["selected_udid"] = "previous-phone"
            slug.save()
            slug.close()
            (support / APP_DATA_DIR_NAME).mkdir()
            with patch("backend.wallet_store.Path.home", return_value=home):
                current = WalletStore()
            self.addCleanup(current.close)
            self.assertEqual(current.root, support / APP_DATA_DIR_NAME)
            self.assertIsNone(current.data["selected_udid"])
            self.assertEqual(json.loads(legacy.path.read_text())["selected_udid"], "old-phone")
            self.assertEqual(json.loads(slug.path.read_text())["selected_udid"], "previous-phone")

    def test_explicit_data_directory_overrides_all_default_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            support = home / "Library/Application Support"
            for name in ("AirCard", APP_ID, APP_DATA_DIR_NAME):
                (support / name).mkdir(parents=True)
            override = home / "custom-data"
            with patch("backend.wallet_store.Path.home", return_value=home):
                current = WalletStore(override)
            self.addCleanup(current.close)
            self.assertEqual(current.root, override)
            self.assertTrue(current.path.is_file())
            self.assertFalse((support / APP_DATA_DIR_NAME / "state.json").exists())
            self.assertFalse((support / "AirCard" / "state.json").exists())
            self.assertFalse((support / APP_ID / "state.json").exists())

    def test_imports_device_images_selections_hashes_and_clears_once(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            prefs = home / "Library/Preferences"
            prefs.mkdir(parents=True)
            artwork = home / "image.png"
            artwork.write_bytes(b"legacy artwork")
            digest = hashlib.sha256(artwork.read_bytes()).hexdigest()
            (prefs / "com.mak5er.aircard.plist").write_bytes(plistlib.dumps({
                "mak5er.aircard.wallet.v2.first-phone": json.dumps([{"id": A, "confirmed": True, "selected": False, "imagePath": str(artwork)}]).encode(),
                "mak5er.aircard.wallet.v2.second-phone": json.dumps([]).encode(),
                "mak5er.aircard.savedCards": [B],
                "mak5er.aircard.selectedUDID": "first-phone",
                "mak5er.aircard.flashedSkins": {f"first-phone|{A}": digest},
            }))
            store = WalletStore(Path(directory) / "data", home)
            record = store.records("first-phone")[0]
            self.assertFalse(record["selected"])
            self.assertTrue(record["confirmed"])
            self.assertEqual(Path(record["imagePath"]).read_bytes(), b"legacy artwork")
            self.assertNotEqual(record["imagePath"], str(artwork))
            self.assertEqual(store.records("second-phone"), [])
            self.assertFalse(store.records("new-phone")[0]["confirmed"])
            self.assertEqual(store.data["flashed"][f"first-phone|{A}"], digest)
            store.records("first-phone").clear()
            store.save()
            artwork.unlink()
            store.close()
            restored = WalletStore(store.root, home)
            self.assertEqual(restored.records("first-phone"), [])
            self.assertEqual(restored.data["selected_udid"], "first-phone")

    def test_data_directory_has_a_lifetime_process_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = WalletStore(root / "data", root)
            with self.assertRaisesRegex(RuntimeError, f"Another {APP_NAME}"):
                WalletStore(root / "data", root)
            first.close()
            second = WalletStore(root / "data", root)
            second.close()

    def test_bad_store_does_not_leave_lock_held(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.json").write_text("invalid json")
            with self.assertRaises(ValueError):
                WalletStore(root, root)
            (root / "state.json").unlink()
            WalletStore(root, root).close()

    def test_empty_current_legacy_store_does_not_fall_back_to_old_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".aircard_cards.json").write_text("[]")
            (home / ".lumicards_cards.json").write_text(json.dumps([A]))
            self.assertEqual(WalletStore(home / "data", home).records("first-phone"), [])


class WalletServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.processes = []
        self.commands = []
        self.catalog = empty_catalog()
        self.devices = [FIRST, SECOND]
        self.flash_results = []

        def popen(command, **kwargs):
            self.commands.append(command)
            if "syslog" in command:
                process = Process()
            else:
                self.assertEqual(command[:5], [sys.executable, "-u", "-m", "backend.writer", "--flash"])
                self.assertEqual(Path(kwargs["cwd"]), Path(__file__).resolve().parents[2])
                self.assertTrue(kwargs.get("start_new_session"), "Ctrl+C must not interrupt native write cleanup")
                process = self.flash_results.pop(0) if self.flash_results else Process(json.dumps({"type": "success", "step": 8, "total": 8, "message": "Updated"}) + "\n")
            self.processes.append(process)
            return process

        self.service = WalletService(Path(self.temp.name) / "data", discover_devices=lambda: self.devices,
                                     helper_finder=lambda: "/fake/device_helper", catalog_reader=lambda ids, product: self.catalog,
                                     popen=popen, image_preparer=lambda data: b"normalized:" + data,
                                     legacy_home=Path(self.temp.name), connect_on_launch=False,
                                     preview_finder=lambda card_id: None)
        self.service.dispatch("devices.refresh", {"udid": "first-phone"})
        wait_for(lambda: not self.service.snapshot()["checking"])
        wait_for(lambda: not self.service.snapshot()["reading_cache"])

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.end()
        self.service.close()
        self.temp.cleanup()

    def scan(self, *ids):
        count = len(self.processes)
        self.service.dispatch("scan.start", {"udid": "first-phone"})
        wait_for(lambda: len(self.processes) > count)
        process = self.processes[-1]
        for card in ids:
            process.feed(f"Wallet /Cards/{card}.pkpass/card.png\n")
        if ids:
            wait_for(lambda: all(card in [row["id"] for row in self.service.snapshot()["cards"]] for card in ids))
        return process

    def stop(self):
        self.service.dispatch("scan.stop", {"udid": "first-phone"})
        wait_for(lambda: not self.service.snapshot()["scanning"])
        wait_for(lambda: not self.service.snapshot()["reading_cache"])

    def test_current_and_legacy_native_scanner_status_prefixes_are_understood(self):
        process = self.scan()
        for prefix, message in ((f"{APP_NAME} scanner: ", "Current helper ready"),
                                (f"{APP_DATA_DIR_NAME} scanner: ", "Previous compact helper ready"),
                                (f"{APP_ID} scanner: ", "Previous slug helper ready"),
                                ("AirCard scanner: ", "Previously built helper ready")):
            with self.subTest(prefix=prefix):
                process.feed(prefix + message + "\n")
                wait_for(lambda: self.service.snapshot()["scanner_message"] == message)
                self.assertTrue(self.service.snapshot()["logs"][-1].endswith(f"{APP_NAME} scanner: {message}"))
        self.stop()

    def test_manual_and_saved_cards_hidden_until_current_scan(self):
        self.service.dispatch("cards.save_ids", {"udid": "first-phone", "text": f"{B}, {A}; ../../bad"})
        self.assertEqual(self.service.snapshot()["cards"], [])
        self.assertEqual(self.service.snapshot()["hidden_count"], 2)
        with self.assertRaises(ValueError):
            self.service.assign_artwork("first-phone", [A], b"image")
        process = self.scan(A)
        process.feed(f"passd identifier {B}\n")
        self.stop()
        self.assertEqual([row["id"] for row in self.service.snapshot()["cards"]], [A])
        self.service.assign_artwork("first-phone", [A], b"image")
        self.service.dispatch("devices.select", {"udid": "second-phone"})
        wait_for(lambda: not self.service.snapshot()["checking"])
        self.assertEqual(self.service.snapshot()["cards"], [])
        with self.assertRaises(ValueError):
            self.service.assign_artwork("first-phone", [A], b"wrong-device")
        self.service.dispatch("devices.select", {"udid": "first-phone"})
        wait_for(lambda: not self.service.snapshot()["checking"])
        self.assertEqual(self.service.snapshot()["cards"], [])
        self.scan(A)
        self.stop()
        self.assertEqual(self.service.artwork_path("first-phone", A).read_bytes(), b"image")
        self.assertNotIn("imagePath", json.dumps(self.service.snapshot()))

    def test_cached_preview_requires_current_card_and_never_becomes_replacement(self):
        cached = Path(self.temp.name) / "cached.png"
        cached.write_bytes(b"cached reference")
        lookups = []
        def find(card_id):
            lookups.append(card_id)
            return cached
        self.service._find_preview = find
        with self.assertRaises(ValueError):
            self.service.preview_path("first-phone", A)
        self.assertEqual(lookups, [])
        self.scan(A)
        self.stop()
        card = self.service.snapshot()["cards"][0]
        self.assertEqual(card["preview_source"], "mac_cache")
        self.assertEqual(card["preview_revision"], hashlib.sha256(cached.read_bytes()).hexdigest())
        self.assertFalse(card["has_image"])
        self.assertFalse(card["is_flashed"])
        self.assertIsNone(self.service.artwork_path("first-phone", A))
        self.assertEqual(self.service.preview_path("first-phone", A), cached)
        with self.assertRaisesRegex(ValueError, "Assign artwork"):
            self.service.dispatch("flash.start", {"udid": "first-phone"})
        with self.assertRaises(ValueError):
            self.service.preview_path("second-phone", A)
        self.assertNotIn(str(cached), json.dumps(self.service.snapshot()))
        self.assertIsNone(self.service._store.records("first-phone")[0]["imagePath"])

    def test_slow_preview_lookup_does_not_block_state_or_survive_device_switch(self):
        cached = Path(self.temp.name) / "cached.png"
        cached.write_bytes(b"old device preview")
        self.scan(A)
        self.stop()
        entered = threading.Event()
        release = threading.Event()
        def find(card_id):
            entered.set()
            release.wait(3)
            return cached
        self.service._find_preview = find
        self.service.dispatch("catalog.refresh", {"udid": "first-phone"})
        self.assertTrue(entered.wait(2))
        done = threading.Event()
        worker = threading.Thread(target=lambda: (self.service.snapshot(), done.set()))
        worker.start()
        try:
            self.assertTrue(done.wait(0.5), "Preview decode must not hold the controller lock")
            self.service.dispatch("devices.select", {"udid": "second-phone"})
            wait_for(lambda: not self.service.snapshot()["checking"])
        finally:
            release.set()
            worker.join(2)
        wait_for(lambda: not self.service.snapshot()["reading_cache"])
        self.assertEqual(self.service.snapshot()["cards"], [])
        self.assertEqual(self.service._previews, {})

    def test_replacement_preview_takes_priority_and_missing_file_is_not_masked(self):
        cached = Path(self.temp.name) / "cached.png"
        cached.write_bytes(b"cached reference")
        self.service._find_preview = lambda card_id: cached
        self.scan(A)
        self.stop()
        self.service.assign_artwork("first-phone", [A], b"chosen artwork")
        row = self.service.snapshot()["cards"][0]
        self.assertEqual(row["preview_source"], "local")
        self.assertEqual(row["preview_revision"], row["image_revision"])
        self.assertEqual(self.service.preview_path("first-phone", A).read_bytes(), b"chosen artwork")
        self.service.artwork_path("first-phone", A).unlink()
        row = self.service.snapshot()["cards"][0]
        self.assertTrue(row["image_missing"])
        self.assertIsNone(row["preview_source"])
        self.service.dispatch("cards.clear_image", {"udid": "first-phone", "id": A})
        self.assertEqual(self.service.snapshot()["cards"][0]["preview_source"], "mac_cache")
        self.assertEqual(cached.read_bytes(), b"cached reference")
        self.service.dispatch("devices.select", {"udid": "second-phone"})
        wait_for(lambda: not self.service.snapshot()["checking"])
        self.assertEqual(self.service.snapshot()["cards"], [])
        with self.assertRaises(ValueError):
            self.service.preview_path("second-phone", A)

    def test_cache_requires_fresh_evidence_then_preloads_matching_payment_cards(self):
        self.catalog = {**empty_catalog(), "paymentStatus": "matched", "payments": [{"id": A, "name": "A", "source": "payment"}, {"id": B, "name": "B", "source": "payment"}], "memberships": [{"id": C, "name": "C", "source": "membership"}]}
        self.service.dispatch("catalog.refresh", {"udid": "first-phone"})
        wait_for(lambda: not self.service.snapshot()["reading_cache"])
        self.assertEqual(self.service.snapshot()["cards"], [])
        self.scan(A)
        wait_for(lambda: len(self.service.snapshot()["cards"]) == 2)
        self.assertEqual([row["id"] for row in self.service.snapshot()["cards"]], [A, B])
        self.stop()

    def test_pending_activation_resolves_after_cache_refresh_including_multiline(self):
        process = self.scan()
        activation = "A00000000310100100000020"
        process.feed("nfcd: setActivePaymentApplet: requestedApplet:\n")
        process.feed(f"<NFApplet> {{ identifier = {activation} family=0x0 }}\n")
        wait_for(lambda: "activated" in self.service.snapshot()["scanner_message"])
        self.catalog = {**empty_catalog(), "paymentStatus": "matched", "payments": [{"id": A, "name": "Bank A", "activationID": activation}]}
        self.service.dispatch("catalog.refresh", {"udid": "first-phone"})
        wait_for(lambda: bool(self.service.snapshot()["cards"]))
        self.assertEqual(self.service.snapshot()["cards"][0]["name"], "Bank A")
        self.stop()

    def test_explicit_missing_device_never_falls_back(self):
        self.service.dispatch("devices.select", {"udid": "absent"})
        wait_for(lambda: not self.service.snapshot()["checking"])
        self.assertIsNone(self.service.snapshot()["device"])
        self.assertEqual(len(self.service.snapshot()["devices"]), 2)
        self.assertIn("not connected", self.service.snapshot()["error"])

    def test_stale_catalog_result_cannot_cross_device_boundary(self):
        entered = threading.Event()
        release = threading.Event()
        def read(ids, product):
            if not entered.is_set():
                entered.set()
                release.wait(3)
                return {**empty_catalog(), "payments": [{"id": A, "name": "Wrong phone"}]}
            return empty_catalog()
        self.service._read_catalog = read
        self.service.dispatch("catalog.refresh", {"udid": "first-phone"})
        self.assertTrue(entered.wait(2))
        self.service.dispatch("devices.select", {"udid": "second-phone"})
        wait_for(lambda: not self.service.snapshot()["checking"])
        release.set()
        wait_for(lambda: not self.service.snapshot()["reading_cache"])
        self.assertEqual(self.service.snapshot()["catalog"]["payments"], [])

    def test_scanner_ignoring_terminate_is_killed_without_waiting_for_stdout(self):
        process = self.scan(A)
        process.terminate = lambda: None
        process.kill = lambda: process.end(-9)
        with patch("backend.wallet_service.SCANNER_STOP_TIMEOUT", 0.02):
            self.stop()
        self.assertEqual(process.returncode, -9)
        self.assertFalse(self.service.snapshot()["scanning"])

    def test_scan_switch_reaps_helper_and_disallows_old_lines(self):
        process = self.scan(A)
        self.service.dispatch("devices.select", {"udid": "second-phone"})
        wait_for(lambda: not self.service.snapshot()["checking"])
        self.assertTrue(process.terminated)
        self.assertFalse(self.service.snapshot()["scanning"])
        self.assertEqual(self.service.snapshot()["cards"], [])

    def test_flash_guards_changed_artwork_and_repeat_write(self):
        self.scan(A, B)
        self.service.assign_artwork("first-phone", [A, B], b"image")
        with self.assertRaisesRegex(ValueError, "Stop scanning"):
            self.service.dispatch("flash.start", {"udid": "first-phone"})
        self.stop()
        self.service.dispatch("flash.start", {"udid": "first-phone"})
        wait_for(lambda: not self.service.snapshot()["flashing"])
        self.assertTrue(all(row["is_flashed"] for row in self.service.snapshot()["cards"]))
        self.assertEqual(self.service.snapshot()["progress"], 1)
        self.service.assign_artwork("first-phone", [B], b"new artwork")
        before = len(self.commands)
        self.service.dispatch("flash.start", {"udid": "first-phone"})
        wait_for(lambda: not self.service.snapshot()["flashing"])
        self.assertEqual(len(self.commands) - before, 1)
        self.assertIn(B, self.commands[-1])
        before = len(self.commands)
        self.service.dispatch("flash.start", {"udid": "first-phone"})
        wait_for(lambda: not self.service.snapshot()["flashing"])
        self.assertEqual(len(self.commands) - before, 2)

    def test_flashing_blocks_all_card_mutations_and_device_switch(self):
        self.scan(A)
        self.stop()
        self.service.assign_artwork("first-phone", [A], b"image")
        process = Process()
        self.flash_results = [process]
        self.service.dispatch("flash.start", {"udid": "first-phone"})
        wait_for(lambda: process in self.processes)
        for action, payload in (("devices.select", {"udid": "second-phone"}), ("cards.clear", {"udid": "first-phone"}), ("scan.start", {"udid": "first-phone"})):
            with self.assertRaises(ValueError):
                self.service.dispatch(action, payload)
        with self.assertRaises(ValueError):
            self.service.assign_artwork("first-phone", [A], b"replacement")
        process.feed(json.dumps({"type": "success"}))
        process.end()
        wait_for(lambda: not self.service.snapshot()["flashing"])
        self.assertTrue(self.service.snapshot()["cards"][0]["is_flashed"])

    def test_error_event_cannot_be_reported_success_even_with_zero_exit(self):
        self.scan(A)
        self.stop()
        self.service.assign_artwork("first-phone", [A], b"image")
        self.flash_results = [Process(json.dumps({"type": "error", "message": "Cache failed"}) + "\n" + json.dumps({"type": "success"}))]
        self.service.dispatch("flash.start", {"udid": "first-phone"})
        wait_for(lambda: not self.service.snapshot()["flashing"])
        self.assertFalse(self.service.snapshot()["cards"][0]["is_flashed"])
        self.assertIsNone(self.service.snapshot()["success"])
        self.assertIn("failed", self.service.snapshot()["error"])

    def test_shutdown_waits_for_current_write_cleanup_and_skips_next_card(self):
        self.scan(A, B)
        self.stop()
        self.service.assign_artwork("first-phone", [A, B], b"image")
        process = Process()
        self.flash_results = [process]
        self.service.dispatch("flash.start", {"udid": "first-phone"})
        wait_for(lambda: process in self.processes)
        commands = len(self.commands)
        closed = threading.Event()
        thread = threading.Thread(target=lambda: (self.service.close(), closed.set()))
        thread.start()
        self.assertFalse(closed.wait(0.05))
        self.assertFalse(process.terminated)
        process.feed(json.dumps({"type": "success"}))
        process.end()
        self.assertTrue(closed.wait(2))
        thread.join()
        self.assertEqual(len(self.commands), commands)
        self.assertIn("before all", self.service.snapshot()["error"])

    def test_missing_image_does_not_launch_write(self):
        self.scan(A)
        self.stop()
        self.service.assign_artwork("first-phone", [A], b"image")
        self.service.artwork_path("first-phone", A).unlink()
        self.assertTrue(self.service.snapshot()["cards"][0]["image_missing"])
        with self.assertRaisesRegex(ValueError, "missing"):
            self.service.dispatch("flash.start", {"udid": "first-phone"})

    def test_clear_and_relaunch_retains_empty_per_device_state(self):
        self.scan(A)
        self.stop()
        self.service.dispatch("cards.clear", {"udid": "first-phone"})
        self.assertEqual(self.service.snapshot()["cards"], [])
        self.service.close()
        store = WalletStore(Path(self.temp.name) / "data", Path(self.temp.name))
        self.assertEqual(store.records("first-phone"), [])


class DeviceConnectionStateTests(unittest.TestCase):
    """Exercise connection boundaries with the standard synthetic controller."""

    setUp = WalletServiceTests.setUp
    tearDown = WalletServiceTests.tearDown
    scan = WalletServiceTests.scan
    stop = WalletServiceTests.stop

    def test_network_devices_are_visible_but_cannot_scan_write_or_mutate(self):
        self.devices = [dict(FIRST, transport="network", connected=True)]
        self.service.dispatch("devices.refresh", {})
        wait_for(lambda: not self.service.snapshot()["checking"])
        state = self.service.snapshot()
        self.assertEqual(state["device"]["transport"], "network")
        self.assertFalse(state["device"]["connected"])
        for action in ("scan.start", "flash.start", "cards.clear", "catalog.refresh"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "ready USB"):
                self.service.dispatch(action, {"udid": FIRST["udid"]})
        with self.assertRaisesRegex(ValueError, "ready USB"):
            self.service.assign_artwork(FIRST["udid"], [A], b"image")
        self.assertEqual(self.commands, [])

    def test_unchanged_presence_preserves_running_scan_and_verified_cards(self):
        process = self.scan(A)
        before = self.service.snapshot()
        for _ in range(3):
            self.service._apply_presence([dict(FIRST, udid=FIRST["udid"].upper()), SECOND])
        after = self.service.snapshot()
        self.assertTrue(after["scanning"])
        self.assertFalse(process.terminated)
        self.assertEqual(after["cards"], before["cards"])
        self.assertEqual(after["device"], before["device"])
        self.assertFalse(after["checking"])
        self.stop()

    def test_selected_disconnect_stops_scan_hides_cards_and_does_not_switch(self):
        process = self.scan(A)
        self.service._apply_presence([SECOND])
        wait_for(lambda: not self.service.snapshot()["scanning"])
        state = self.service.snapshot()
        self.assertEqual(state["device"]["udid"], FIRST["udid"])
        self.assertIs(state["device"]["present"], False)
        self.assertEqual(state["cards"], [])
        self.assertEqual(state["hidden_count"], 1)
        self.assertTrue(process.terminated)
        process.feed(f"Wallet /Cards/{B}.pkpass/card.png\n")
        self.assertEqual(self.service.snapshot()["cards"], [])
        self.service._apply_presence([FIRST, SECOND])
        self.assertFalse(self.service.snapshot()["device"]["connected"])
        self.service.dispatch("devices.refresh", {})
        wait_for(lambda: not self.service.snapshot()["checking"])
        self.assertTrue(self.service.snapshot()["device"]["connected"])
        self.assertEqual(self.service.snapshot()["cards"], [])
        self.scan(A)
        self.stop()

    def test_usb_to_network_invalidates_current_scan_evidence(self):
        self.scan(A)
        self.stop()
        self.service._apply_presence([dict(FIRST, transport="network"), SECOND])
        state = self.service.snapshot()
        self.assertEqual(state["device"]["transport"], "network")
        self.assertFalse(state["device"]["connected"])
        self.assertEqual(state["cards"], [])

    def test_presence_failure_is_unknown_and_recovery_requires_reverification(self):
        self.scan(A)
        self.stop()
        self.service._apply_presence(None)
        self.assertIsNone(self.service.snapshot()["device"]["present"])
        self.assertFalse(self.service.snapshot()["device"]["connected"])
        self.assertEqual(self.service.snapshot()["cards"], [])
        self.service._apply_presence([FIRST, SECOND])
        self.service.dispatch("devices.refresh", {})
        wait_for(lambda: not self.service.snapshot()["checking"])
        self.assertTrue(self.service.snapshot()["device"]["connected"])
        self.assertEqual(self.service.snapshot()["cards"], [])

    def test_stale_metadata_cannot_restore_usb_after_passive_disconnect(self):
        entered = threading.Event()
        release = threading.Event()
        def discover():
            entered.set()
            release.wait(2)
            return [FIRST, SECOND]
        self.service._discover_devices = discover
        self.service.dispatch("devices.refresh", {})
        self.assertTrue(entered.wait(2))
        self.service._apply_presence([SECOND])
        release.set()
        wait_for(lambda: not self.service.snapshot()["checking"])
        self.assertIs(self.service.snapshot()["device"]["present"], False)
        self.assertFalse(self.service.snapshot()["device"]["connected"])

    def test_native_refresh_omitting_present_usb_does_not_reuse_cached_readiness(self):
        self.service._apply_presence([FIRST, SECOND])
        self.devices = [SECOND]
        self.service.dispatch("devices.refresh", {})
        wait_for(lambda: not self.service.snapshot()["checking"])
        device = self.service.snapshot()["device"]
        self.assertEqual(device["name"], FIRST["name"])
        self.assertEqual(device["transport"], "usb")
        self.assertEqual(device["session_state"], "unavailable")
        self.assertFalse(device["connected"])

    def test_metadata_failure_preserves_unknown_selected_row(self):
        def discover():
            raise RuntimeError("Device metadata unavailable")
        self.service._discover_devices = discover
        self.service.dispatch("devices.refresh", {})
        wait_for(lambda: not self.service.snapshot()["checking"])
        device = self.service.snapshot()["device"]
        self.assertEqual(device["udid"], FIRST["udid"])
        self.assertIsNone(device["present"])
        self.assertFalse(device["connected"])

    def test_disconnect_and_reconnect_during_write_finishes_cleanup_but_stops_batch(self):
        self.scan(A, B)
        self.stop()
        self.service.assign_artwork(FIRST["udid"], [A, B], b"image")
        process = Process()
        self.flash_results = [process]
        self.service.dispatch("flash.start", {"udid": FIRST["udid"]})
        wait_for(lambda: process in self.processes)
        before = len(self.commands)
        self.service._apply_presence([])
        self.assertFalse(process.terminated)
        self.assertTrue(self.service.snapshot()["flashing"])
        self.service._apply_presence([FIRST])
        # Model even a later successful session probe during the same write.
        with self.service._lock:
            self.service._devices = [dict(FIRST)]
            self.service._activate_locked(dict(FIRST))
        process.feed(json.dumps({"type": "success"}))
        process.end()
        wait_for(lambda: not self.service.snapshot()["flashing"])
        self.assertEqual(len(self.commands), before)
        self.assertEqual(self.service.snapshot()["cards"], [])
        self.assertIn("connection changed", self.service.snapshot()["error"])
        self.assertIsNone(self.service.snapshot()["success"])


class PresenceMonitorLifecycleTests(unittest.TestCase):
    def test_injected_monitor_recovers_and_shutdown_wakes_long_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            state = {"result": [FIRST], "reads": 0, "probes": 0}
            def presence():
                state["reads"] += 1
                result = state["result"]
                if isinstance(result, Exception):
                    raise result
                return result
            def metadata():
                state["probes"] += 1
                return [FIRST]
            service = WalletService(Path(directory) / "data", legacy_home=Path(directory),
                                    discover_devices=metadata, presence_reader=presence, presence_interval=0.01,
                                    helper_finder=lambda: "/fake/helper", catalog_reader=lambda ids, product: empty_catalog())
            try:
                wait_for(lambda: service.snapshot()["device"] and service.snapshot()["device"]["connected"])
                wait_for(lambda: state["reads"] >= 2)
                probes = state["probes"]
                count = state["reads"]
                wait_for(lambda: state["reads"] >= count + 3)
                self.assertEqual(state["probes"], probes)
                state["result"] = OSError("temporary usbmuxd failure")
                wait_for(lambda: service.snapshot()["device"]["present"] is None)
                state["result"] = [FIRST]
                wait_for(lambda: service.snapshot()["device"]["connected"])
                self.assertGreater(state["probes"], probes)
                service._presence_interval = 60
                count = state["reads"]
                wait_for(lambda: state["reads"] > count)
                closed = threading.Event()
                thread = threading.Thread(target=lambda: (service.close(), closed.set()))
                thread.start()
                self.assertTrue(closed.wait(1), "shutdown must wake the passive monitor immediately")
                thread.join()
                self.assertFalse(service._presence_thread.is_alive())
            finally:
                service.close()
