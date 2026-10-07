"""The native pipe transport preserves verification and safe shutdown behavior."""
import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend.desktop import DesktopTransport, serve
from backend.wallet_service import WalletService, empty_catalog
from __tests__.fixtures import A, FIRST, Process, png_image, wait_for


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = WalletService(
            Path(self.directory.name) / "data", legacy_home=Path(self.directory.name),
            discover_devices=lambda: [FIRST], helper_finder=lambda: "/fake/device_helper",
            catalog_reader=lambda *args: empty_catalog(), preview_finder=lambda *args: None,
            connect_on_launch=False,
        )
        self.addCleanup(self.service.close)
        self.service.dispatch("devices.refresh", {"udid": FIRST["udid"]})
        wait_for(lambda: not self.service.snapshot()["checking"])
        wait_for(lambda: not self.service.snapshot()["reading_cache"])
        self.service.dispatch("cards.save_ids", {"udid": FIRST["udid"], "text": A})
        self.transport = DesktopTransport(self.service)
        self.image = png_image(8, 8, lambda x, y: (40, 90, 180))

    def request(self, method, **payload):
        return self.transport.handle({"id": 1, "method": method, "payload": payload})

    def test_historical_id_and_changed_device_cannot_receive_artwork(self):
        with patch("backend.desktop.prepare_image", return_value=self.image):
            with self.assertRaisesRegex(ValueError, "Scan this card"):
                self.request("artwork.assign", udid=FIRST["udid"], ids=[A],
                             data=base64.b64encode(self.image).decode())
            self.service._verified.add(A)
            with self.assertRaisesRegex(ValueError, "changed or disconnected"):
                self.request("artwork.assign", udid="other-phone", ids=[A],
                             data=base64.b64encode(self.image).decode())
        self.assertFalse(self.service.snapshot()["cards"][0]["has_image"])

    def test_assignment_readback_and_write_authorization_use_service(self):
        self.service._verified.add(A)
        with patch("backend.desktop.prepare_image", return_value=self.image):
            result = self.request("artwork.assign", udid=FIRST["udid"], ids=[A],
                                  data=base64.b64encode(self.image).decode())
            self.assertTrue(result["cards"][0]["has_image"])
            reply = self.request("artwork.read", udid=FIRST["udid"], id=A, kind="artwork")
            self.assertEqual(base64.b64decode(reply["data"]), self.image)
        with self.assertRaisesRegex(ValueError, "changed or disconnected"):
            self.request("action", action="flash.start", arguments={"udid": "other-phone"})
        self.assertFalse(self.service.snapshot()["flashing"])

    def test_malformed_input_recovers_and_eof_releases_store_lock(self):
        incoming = io.BytesIO(b"invalid\n" + json.dumps({"id": 2, "method": "state"}).encode() + b"\n")
        outgoing = io.BytesIO()
        serve(self.service, incoming, outgoing)
        replies = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(replies[0]["event"], "ready")
        self.assertIn("error", replies[1])
        self.assertEqual(replies[2]["id"], 2)
        self.assertIn("cards", replies[2]["result"])
        self.assertTrue(self.service._closed)
        self.assertIsNone(self.service._store._lock_stream)

    def test_invalid_envelope_and_base64_do_not_mutate_store(self):
        for request in ([], {"id": True, "method": "state"}, {"id": 1, "method": "state", "payload": []}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.transport.handle(request)
        with self.assertRaisesRegex(ValueError, "Invalid image data"):
            self.request("artwork.assign", udid=FIRST["udid"], ids=[A], data="%%%")
        self.assertFalse(list(self.service._store.images.iterdir()))

    def test_oversized_request_is_drained_before_next_message(self):
        outgoing = io.BytesIO()
        incoming = io.BytesIO(b"x" * 110 + b"\n" + b'{"id":2,"method":"state"}\n')
        with patch("backend.desktop.MAX_REQUEST_BYTES", 100):
            serve(self.service, incoming, outgoing)
        replies = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn("too large", replies[1]["error"])
        self.assertEqual(replies[2]["id"], 2)

    def test_shutdown_reply_waits_for_current_write_cleanup(self):
        self.service._verified.add(A)
        self.service.assign_artwork(FIRST["udid"], [A], self.image)
        writing = Process()
        started = threading.Event()

        def popen(*args, **kwargs):
            started.set()
            return writing

        self.service._popen = popen
        self.service._image_preparer = lambda data: data
        self.service.dispatch("flash.start", {"udid": FIRST["udid"]})
        self.assertTrue(started.wait(2))
        outgoing = io.BytesIO()
        thread = threading.Thread(target=serve, args=(
            self.service, io.BytesIO(b'{"id":1,"method":"shutdown"}\n'), outgoing))
        thread.start()
        wait_for(lambda: self.service._closed)
        self.assertTrue(thread.is_alive(), "Shutdown must wait for cleanup")
        self.assertNotIn(b'"stopping"', outgoing.getvalue())
        writing.feed('{"type":"success","step":8,"total":8}\n')
        writing.end()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(writing.terminated)
        self.assertEqual(json.loads(outgoing.getvalue().splitlines()[-1])["result"], {"stopping": True})
