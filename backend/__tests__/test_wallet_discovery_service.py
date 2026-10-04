"""Exercise upstream log variants through the active device scanner."""

import tempfile
import unittest
from pathlib import Path

from backend import APP_NAME
from backend.wallet_service import WalletService, empty_catalog
from __tests__.fixtures import A, B, C, FIRST, Process, wait_for


class WalletDiscoveryServiceTests(unittest.TestCase):
    def test_structured_events_verify_only_current_scan_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            scanner = Process()
            activation = "A00000000310100100000020"
            catalog = dict(empty_catalog(), payments=[{"id": B, "activationID": activation, "name": "Fixture bank"}])

            def popen(command, **kwargs):
                self.assertEqual(command, ["/synthetic/helper", "syslog", FIRST["udid"]])
                return scanner

            service = WalletService(Path(directory) / "data", legacy_home=Path(directory),
                                    discover_devices=lambda: [FIRST], helper_finder=lambda: "/synthetic/helper",
                                    catalog_reader=lambda ids, product: catalog, popen=popen)
            try:
                wait_for(lambda: not service.snapshot()["checking"] and not service.snapshot()["reading_cache"])
                service.dispatch("cards.save_ids", {"udid": FIRST["udid"], "text": C})
                self.assertEqual(service.snapshot()["cards"], [])
                service.dispatch("scan.start", {"udid": FIRST["udid"]})
                scanner.feed(f"passd: unusual event {C} without explicit path\n")
                scanner.feed(f'nfcd: passIDs[global]: {{("{C}")}}\n')
                scanner.feed(f"Browser: Dashboard loading (...): for {C}\n")
                scanner.feed(f'nfcd: unrelated event {{"identifier":"{activation}"}}\n')
                scanner.feed(f"{APP_NAME} scanner: Rejected candidates processed\n")
                wait_for(lambda: service.snapshot()["scanner_message"] == "Rejected candidates processed")
                self.assertEqual(service.snapshot()["cards"], [])
                with self.assertRaisesRegex(ValueError, "Scan this card"):
                    service.assign_artwork(FIRST["udid"], [C], b"image")
                scanner.feed(f"Wallet: Dashboard loading (...): for {A}, pass feature unknown\n")
                wait_for(lambda: [row["id"] for row in service.snapshot()["cards"]] == [A])
                scanner.feed('nfcd: setActivePaymentApplet: requestedApplet: {"identifier":"'
                             + activation.lower() + '","family":0}\n')
                wait_for(lambda: [row["id"] for row in service.snapshot()["cards"]] == [A, B])
                self.assertEqual(service.snapshot()["hidden_count"], 1)
                with self.assertRaisesRegex(ValueError, "Scan this card"):
                    service.assign_artwork(FIRST["udid"], [C], b"image")
            finally:
                service.close()
            self.assertTrue(scanner.terminated)
