"""Run the actual HTTP/controller/image pipeline with synthetic USB processes."""
import hashlib
import http.client
import json
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

from aircard_server import AirCardServer
from wallet_catalog import build_catalog
from wallet_service import WalletService
from test_image_processing import png_image
from test_wallet_service import A, FIRST, Process, wait_for


@unittest.skipUnless(Path("/usr/bin/sips").is_file(), "Requires the macOS native image decoder")
class WebWorkflowTests(unittest.TestCase):
    def test_http_scan_upload_preview_write_and_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            data_dir = home / "data"
            scanners = []
            writes = []

            def native_process(command, **kwargs):
                if "syslog" in command:
                    self.assertEqual(command, ["/synthetic/device_helper", "syslog", FIRST["udid"]])
                    process = Process()
                    scanners.append(process)
                    return process
                # The real controller prepared the file and constructed the
                # backend command; only execution against a phone is replaced.
                self.assertIn("--flash", command)
                self.assertEqual(command[-3:-1], [FIRST["udid"], A])
                self.assertTrue(kwargs.get("start_new_session"))
                prepared = Path(command[-1]).read_bytes()
                self.assertEqual(struct.unpack_from(">II", prepared, 16), (1536, 969))
                writes.append(command)
                return Process("\n".join(json.dumps(event) for event in [
                    {"type": "progress", "step": 4, "total": 8, "message": "Writing artwork"},
                    {"type": "success", "step": 8, "total": 8, "message": "Artwork updated"},
                ]))

            def new_service():
                return WalletService(data_dir, discover_devices=lambda: [FIRST],
                                     helper_finder=lambda: "/synthetic/device_helper",
                                     catalog_reader=lambda ids, product: build_catalog(home / "Library/Passes", ids, product),
                                     popen=native_process, legacy_home=home, connect_on_launch=False)

            service = new_service()
            server = AirCardServer(service, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(method, path, body=None, content_type=None):
                headers = {"X-AirCard-Token": server.token}
                if content_type:
                    headers["Content-Type"] = content_type
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
                try:
                    connection.request(method, path, body, headers)
                    response = connection.getresponse()
                    result = response.read()
                    self.assertEqual(response.status, 200, result.decode("utf-8", errors="replace"))
                    return result
                finally:
                    connection.close()

            def action(name, **payload):
                request("POST", "/api/action", json.dumps({"action": name, "payload": payload}), "application/json")

            def state():
                return json.loads(request("GET", "/api/state"))

            def discover_card():
                previous = len(scanners)
                action("scan.start", udid=FIRST["udid"])
                wait_for(lambda: len(scanners) > previous)
                scanners[-1].feed(f"Wallet /Cards/{A}.pkpass/cardBackgroundCombined@3x.png\n")
                wait_for(lambda: len(state()["cards"]) == 1)
                action("scan.stop", udid=FIRST["udid"])
                wait_for(lambda: not state()["scanning"])

            try:
                action("devices.refresh", udid=FIRST["udid"])
                wait_for(lambda: not state()["checking"])
                discover_card()
                source = png_image(120, 80, lambda x, y: (x * 2, y * 3, 100))
                artwork_url = "/api/artwork?" + urlencode({"udid": FIRST["udid"], "card_id": A})
                request("POST", artwork_url, source, "image/png")
                assigned = state()["cards"][0]
                self.assertTrue(assigned["has_image"])
                self.assertFalse(assigned["image_missing"])
                self.assertFalse(assigned["is_flashed"])
                copied = service.artwork_path(FIRST["udid"], A)
                self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(), assigned["image_revision"])
                self.assertEqual(struct.unpack_from(">II", copied.read_bytes(), 16), (1536, 969))
                preview = request("GET", artwork_url)
                self.assertEqual(struct.unpack_from(">II", preview, 16), (1536, 969))

                action("flash.start", udid=FIRST["udid"])
                wait_for(lambda: not state()["flashing"], timeout=15)
                completed = state()
                self.assertEqual(len(writes), 1)
                self.assertEqual(completed["progress"], 1.0)
                self.assertTrue(completed["success"])
                self.assertIsNone(completed["error"])
                self.assertTrue(completed["cards"][0]["is_flashed"])
                self.assertEqual(completed["cards"][0]["image_revision"], assigned["image_revision"])

                # Recreate the persistent controller, keeping the real HTTP
                # transport. Previous verification is intentionally not restored.
                service.close()
                service = new_service()
                server.service = service
                action("devices.refresh", udid=FIRST["udid"])
                wait_for(lambda: not state()["checking"])
                restored = state()
                self.assertEqual(restored["cards"], [])
                self.assertEqual(restored["hidden_count"], 1)
                self.assertTrue(copied.is_file())
                discover_card()
                restored_card = state()["cards"][0]
                self.assertTrue(restored_card["has_image"])
                self.assertTrue(restored_card["is_flashed"])
                self.assertEqual(restored_card["image_revision"], assigned["image_revision"])
                self.assertEqual(struct.unpack_from(">II", request("GET", artwork_url), 16), (1536, 969))
                self.assertEqual(len(writes), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)
                service.close()
