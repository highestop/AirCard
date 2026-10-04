"""Exercise the real loopback HTTP boundary without touching a USB device."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from backend.aircard_server import AirCardServer, MAX_IMAGE_BYTES


class FakeService:
    def __init__(self):
        self.actions = []
        self.uploads = []

    def snapshot(self):
        return {"cards": [], "status": "Ready", "device": None}

    def dispatch(self, action, payload):
        if action == "flash.start":
            raise ValueError("Scan a card on this iPhone first.")
        self.actions.append((action, payload))

    def assign_artwork(self, udid, card_ids, data):
        self.uploads.append((udid, card_ids, data))

    def artwork_path(self, udid, card_id):
        return None


class WebServerTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.server = AirCardServer(self.service, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method, path, body=None, headers=None, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        request_headers = dict(headers or {})
        if token:
            request_headers["X-AirCard-Token"] = self.server.token
        connection.request(method, path, body, request_headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_same_origin_session_and_state(self):
        status, headers, data = self.request("GET", "/api/session", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["token"], self.server.token)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request("GET", "/api/state")[0], 200)
        self.assertEqual(self.request("GET", "/api/state", token=False)[0], 403)

    def test_foreign_page_and_rebinding_cannot_acquire_session(self):
        for headers in ({"Origin": "https://example.com"}, {"Origin": "null"},
                        {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("GET", "/api/session", headers=headers, token=False)[0], 403)
        self.assertEqual(self.request("OPTIONS", "/api/action", token=False)[0], 403)

    def test_write_requires_session_and_json(self):
        body = json.dumps({"action": "devices.refresh", "payload": {}})
        headers = {"Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/action", body, headers, token=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/action", body)[0], 400)
        self.assertEqual(self.request("POST", "/api/action", body, headers)[0], 200)
        self.assertEqual(self.service.actions, [("devices.refresh", {})])

    def test_non_ascii_token_is_rejected_without_crashing_handler(self):
        status, _, _ = self.request("GET", "/api/state", headers={"X-AirCard-Token": "\u00e9"}, token=False)
        self.assertEqual(status, 403)

    def test_server_preserves_controller_card_verification(self):
        body = json.dumps({"action": "flash.start", "payload": {"udid": "phone"}})
        status, _, data = self.request("POST", "/api/action", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn("Scan a card", json.loads(data)["error"])

    def test_malformed_action_does_not_reach_service(self):
        for body in (b"not json", b"[]", b'{"action":"devices.refresh","payload":[]}'):
            self.assertEqual(self.request("POST", "/api/action", body, {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.service.actions, [])

    def test_upload_preserves_opaque_ids_and_targets(self):
        with patch("backend.image_processing.prepare_image", return_value=b"normalized") as prepare:
            status, _, _ = self.request("POST", "/api/artwork?udid=phone&card_id=ABC%3D&card_id=DEF%3D", b"image")
        self.assertEqual(status, 200)
        prepare.assert_called_once_with(b"image")
        self.assertEqual(self.service.uploads, [("phone", ["ABC=", "DEF="], b"normalized")])

    def test_oversized_upload_rejected_before_reading(self):
        status, _, _ = self.request("POST", "/api/artwork?udid=phone&card_id=card", b"",
                                    {"Content-Length": str(MAX_IMAGE_BYTES + 1)})
        self.assertEqual(status, 400)
        self.assertFalse(self.service.uploads)

    def test_no_filesystem_or_device_artwork_exposure(self):
        for path in ("/../AGENTS.md", "/%2e%2e/AGENTS.md", "/aircard_server.py", "/api/artwork?udid=x&card_id=y"):
            self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("GET", "/api/artwork?udid=x&card_id=y", token=False)[0], 403)

    def test_static_ui_and_offline_editor_are_served(self):
        for path in ("/", "/app.js", "/artwork-bridge.js", "/style.css", "/artwork/"):
            status, headers, data = self.request("GET", path, token=False)
            self.assertEqual(status, 200, path)
            self.assertTrue(data)
            csp = headers["Content-Security-Policy"]
            if path == "/artwork/":
                self.assertIn("frame-ancestors 'self'", csp)
                self.assertIn("connect-src 'none'", csp)
            else:
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertIn("frame-src 'self'", csp)

    def test_external_link_can_open_ui_but_cannot_get_session(self):
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        self.assertEqual(self.request("GET", "/", headers=headers, token=False)[0], 200)
        self.assertEqual(self.request("GET", "/api/session", headers=headers, token=False)[0], 403)


if __name__ == "__main__":
    unittest.main()
