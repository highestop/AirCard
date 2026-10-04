import unittest
import plistlib
import struct
import subprocess
from unittest.mock import patch

from backend.devices import format_device, get_all_connected_devices, list_devices, read_device_presence


MOCK_RAW_DEVICES = [
    {
        "udid": "fixture-iphone-a",
        "name": "Fixture iPhone A",
        "version": "18.7.10",
        "product": "iPhone11,2", "transport": "usb", "session_state": "ready",
    },
    {
        "udid": "fixture-iphone-b",
        "name": "Fixture iPhone B",
        "version": "26.6.2",
        "product": "iPhone18,1", "transport": "usb", "session_state": "ready",
    },
    {
        "udid": "fixture-ipad",
        "name": "Fixture iPad",
        "version": "18.1",
        "product": "iPad13,4", "transport": "usb", "session_state": "ready",
    },
    {
        "udid": "fixture-unpaired", "transport": "usb", "session_state": "unpaired",
    },
]


class DeviceSelectionTests(unittest.TestCase):
    def test_format_device(self):
        dev = format_device(MOCK_RAW_DEVICES[0])
        self.assertEqual(dev["udid"], "fixture-iphone-a")
        self.assertEqual(dev["name"], "Fixture iPhone A")
        self.assertEqual(dev["product"], "iPhone11,2")
        self.assertTrue(dev["connected"])

        # Unpaired device formatting
        unpaired = format_device(MOCK_RAW_DEVICES[3])
        self.assertEqual(unpaired["udid"], "fixture-unpaired")
        self.assertEqual(unpaired["name"], "未知 Apple 设备")
        self.assertEqual(unpaired["product"], "")

    @patch("backend.devices.list_devices", return_value=MOCK_RAW_DEVICES)
    def test_get_all_connected_devices(self, mock_list):
        devices = get_all_connected_devices()
        self.assertEqual(len(devices), 4)
        # Verify iPhones are ordered first, followed by iPad, then unpaired
        self.assertTrue(devices[0]["product"].startswith("iPhone"))
        self.assertTrue(devices[1]["product"].startswith("iPhone"))
        self.assertTrue(devices[2]["product"].startswith("iPad"))
        self.assertEqual(devices[3]["udid"], "fixture-unpaired")

    def test_only_present_ready_usb_can_be_connected(self):
        for transport, session, present, expected in (
            ("usb", "ready", True, True), ("network", "ready", True, False),
            ("usb", "unpaired", True, False), ("usb", "unavailable", True, False),
            ("unknown", "ready", True, False), ("usb", "ready", False, False),
            ("usb", "ready", None, False),
        ):
            with self.subTest(transport=transport, session=session, present=present):
                row = format_device(dict(MOCK_RAW_DEVICES[0], transport=transport, session_state=session, present=present))
                self.assertEqual(row["connected"], expected)
        self.assertFalse(format_device({"udid": "legacy", "connected": True})["connected"])

    def test_duplicate_transport_prefers_usb_without_guessing_session_state(self):
        network = dict(MOCK_RAW_DEVICES[0], transport="network")
        usb = dict(MOCK_RAW_DEVICES[0], udid=network["udid"].upper(), session_state="unpaired")
        with patch("backend.devices.list_devices", return_value=[network, usb]):
            rows = get_all_connected_devices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["transport"], "usb")
        self.assertFalse(rows[0]["connected"])

    def test_metadata_failure_is_distinct_from_no_devices(self):
        with patch("backend.devices.find_device_helper", return_value="/fake/helper"):
            with patch("backend.devices.subprocess.check_output", side_effect=subprocess.TimeoutExpired("helper", 30)):
                with self.assertRaisesRegex(RuntimeError, "detection failed"):
                    list_devices()
            with patch("backend.devices.subprocess.check_output", return_value="invalid JSON"):
                with self.assertRaisesRegex(RuntimeError, "invalid device list"):
                    list_devices()
            with patch("backend.devices.subprocess.check_output", return_value="[]"):
                self.assertEqual(list_devices(), [])


class FakeMuxSocket:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = b""
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def settimeout(self, value):
        self.timeout = value

    def connect(self, path):
        self.path = path

    def sendall(self, data):
        self.sent += data

    def recv(self, size):
        # Exercise fragmented packet framing instead of assuming one recv.
        size = min(size, 7)
        result = bytes(self.response[:size])
        del self.response[:size]
        return result


class DevicePresenceTests(unittest.TestCase):
    def socket(self, payload, header=None):
        payload = plistlib.dumps(payload)
        return FakeMuxSocket((header or struct.pack("<IIII", len(payload) + 16, 1, 8, 1)) + payload)

    def test_list_only_protocol_deduplicates_case_and_prefers_usb(self):
        response = self.socket({"DeviceList": [
            {"Properties": {"SerialNumber": "Phone-A", "ConnectionType": "Network"}},
            {"Properties": {"SerialNumber": "PHONE-A", "ConnectionType": "USB"}},
            {"Properties": {"SerialNumber": "phone-b", "ConnectionType": "Network"}},
        ]})
        with patch("backend.devices.socket.socket", return_value=response):
            devices = read_device_presence()
        self.assertEqual(devices, [{"udid": "PHONE-A", "transport": "usb"}, {"udid": "phone-b", "transport": "network"}])
        length, version, message, tag = struct.unpack("<IIII", response.sent[:16])
        self.assertEqual((length, version, message, tag), (len(response.sent), 1, 8, 1))
        self.assertEqual(plistlib.loads(response.sent[16:])["MessageType"], "ListDevices")
        self.assertTrue(response.closed)

    def test_bad_headers_truncated_payload_and_bad_list_fail_closed(self):
        cases = [FakeMuxSocket(b"short"),
                 self.socket({"DeviceList": []}, struct.pack("<IIII", 2 ** 30, 1, 8, 1)),
                 self.socket({"DeviceList": []}, struct.pack("<IIII", 16, 1, 8, 9)),
                 self.socket({"Number": 1}),
                 self.socket({"DeviceList": [{"Properties": {"ConnectionType": "USB"}}]})]
        for response in cases:
            with self.subTest(response=response), patch("backend.devices.socket.socket", return_value=response):
                with self.assertRaises((ValueError, OSError)):
                    read_device_presence()
                self.assertTrue(response.closed)

    def test_deadline_bounds_fragmented_reads(self):
        response = self.socket({"DeviceList": []})
        with patch("backend.devices.socket.socket", return_value=response), patch("backend.devices.time.monotonic", side_effect=[0.0, 2.0]):
            with self.assertRaises(TimeoutError):
                read_device_presence(timeout=1)


if __name__ == "__main__":
    unittest.main()
