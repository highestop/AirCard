import hashlib
import json
import plistlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.wallet_service import WalletService, empty_catalog
from backend.wallet_store import WalletStore
from __tests__.fixtures import A, B, C, FIRST, SECOND, Process, wait_for


class WalletStoreTests(unittest.TestCase):
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
            with self.assertRaisesRegex(RuntimeError, "Another AirCard"):
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
                self.assertEqual(command[:5], [sys.executable, "-u", "-m", "backend.aircard_backend", "--flash"])
                self.assertEqual(Path(kwargs["cwd"]), Path(__file__).resolve().parents[2])
                self.assertTrue(kwargs.get("start_new_session"), "Ctrl+C must not interrupt native write cleanup")
                process = self.flash_results.pop(0) if self.flash_results else Process(json.dumps({"type": "success", "step": 8, "total": 8, "message": "Updated"}) + "\n")
            self.processes.append(process)
            return process

        self.service = WalletService(Path(self.temp.name) / "data", discover_devices=lambda: self.devices,
                                     helper_finder=lambda: "/fake/device_helper", catalog_reader=lambda ids, product: self.catalog,
                                     popen=popen, image_preparer=lambda data: b"normalized:" + data,
                                     legacy_home=Path(self.temp.name), connect_on_launch=False)
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
