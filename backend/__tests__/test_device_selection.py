import unittest
import plistlib
import struct
import subprocess
import sys
from unittest.mock import patch

from backend.devices import (MAX_DEVICE_DIAGNOSTIC_CHARS, MAX_DEVICE_OUTPUT_BYTES, MAX_DEVICE_STDERR_BYTES,
                             _run_device_helper, format_device, get_all_connected_devices, list_devices, read_device_presence)


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
            for output in ("invalid JSON", "{}", "[42]", '[{"name":"Missing ID"}]'):
                with self.subTest(output=output), patch("backend.devices._run_device_helper", return_value=(0, output, "")):
                    with self.assertRaisesRegex(RuntimeError, "无效的设备列表"):
                        list_devices()
            with patch("backend.devices._run_device_helper", return_value=(0, "[]", "harmless diagnostic")):
                self.assertEqual(list_devices(), [])

    def test_failed_native_exit_preserves_bounded_stderr_even_with_empty_device_json(self):
        diagnostic = "native permission denied: " + "x" * 1000
        with patch("backend.devices.find_device_helper", return_value="/fake/helper"), \
             patch("backend.devices._run_device_helper", return_value=(7, "[]", diagnostic)):
            with self.assertRaises(RuntimeError) as failure:
                list_devices()
        message = str(failure.exception)
        self.assertIn("退出码 7", message)
        self.assertIn("native permission denied", message)
        self.assertEqual(len(message.split("工具输出：", 1)[1]), MAX_DEVICE_DIAGNOSTIC_CHARS + 1)
        self.assertNotIn("x" * 500, message)

    def test_developer_tools_and_license_failures_offer_distinct_guidance(self):
        cases = [("xcrun: invalid active developer path /Library/Developer/CommandLineTools", "xcode-select --install"),
                 ("You have not agreed to the Xcode license agreements", "打开 Xcode 阅读并接受许可协议")]
        for diagnostic, advice in cases:
            with self.subTest(diagnostic=diagnostic), patch("backend.devices.find_device_helper", return_value="/fake/helper"), \
                 patch("backend.devices._run_device_helper", return_value=(1, "", diagnostic)):
                with self.assertRaises(RuntimeError) as failure:
                    list_devices()
                self.assertIn(advice, str(failure.exception))
                self.assertIn(diagnostic, str(failure.exception))

    def test_timeout_launch_and_malformed_output_retain_diagnostic_details(self):
        with patch("backend.devices.find_device_helper", return_value="/fake/helper"):
            timeout = subprocess.TimeoutExpired("helper", 30, output=b"partial output", stderr=b"native session stalled")
            with patch("backend.devices._run_device_helper", side_effect=timeout):
                with self.assertRaisesRegex(RuntimeError, "检测超时.*native session stalled"):
                    list_devices()
            with patch("backend.devices._run_device_helper", side_effect=PermissionError("permission denied")):
                with self.assertRaisesRegex(RuntimeError, "无法运行.*permission denied"):
                    list_devices()
            with patch("backend.devices._run_device_helper", return_value=(0, "not JSON", "native invalid response")):
                with self.assertRaisesRegex(RuntimeError, "无效的设备列表.*native invalid response"):
                    list_devices()
            with patch("backend.devices._run_device_helper", return_value=(0, "not JSON", "")):
                with self.assertRaisesRegex(RuntimeError, "无效的设备列表.*not JSON"):
                    list_devices()

    def test_capture_drains_large_stderr_without_blocking_or_retaining_it_all(self):
        command = [sys.executable, "-c", "import sys; sys.stderr.write('diagnostic ' * 20000); print('[]'); sys.exit(3)"]
        code, output, diagnostic = _run_device_helper(command)
        self.assertEqual(code, 3)
        self.assertEqual(output.strip(), "[]")
        self.assertEqual(len(diagnostic), MAX_DEVICE_STDERR_BYTES)

    def test_capture_timeout_keeps_partial_stderr_and_oversized_stdout_is_rejected(self):
        command = [sys.executable, "-c", "import sys,time; sys.stderr.write('waiting for session\\n'); sys.stderr.flush(); time.sleep(10)"]
        with self.assertRaises(subprocess.TimeoutExpired) as failure:
            _run_device_helper(command, timeout=0.5)
        self.assertIn(b"waiting for session", failure.exception.stderr)
        command = [sys.executable, "-c", f"import sys; sys.stdout.write('x' * {MAX_DEVICE_OUTPUT_BYTES + 1})"]
        with self.assertRaisesRegex(RuntimeError, "返回的数据过大"):
            _run_device_helper(command)


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
