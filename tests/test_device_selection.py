import unittest
from unittest.mock import patch

from backend.aircard import format_device, get_all_connected_devices


MOCK_RAW_DEVICES = [
    {
        "udid": "fixture-iphone-a",
        "name": "Fixture iPhone A",
        "version": "18.7.10",
        "product": "iPhone11,2",
    },
    {
        "udid": "fixture-iphone-b",
        "name": "Fixture iPhone B",
        "version": "26.6.2",
        "product": "iPhone18,1",
    },
    {
        "udid": "fixture-ipad",
        "name": "Fixture iPad",
        "version": "18.1",
        "product": "iPad13,4",
    },
    {
        "udid": "fixture-unpaired",
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
        self.assertEqual(unpaired["name"], "Locked / Unpaired Device")
        self.assertEqual(unpaired["product"], "")

    @patch("backend.aircard.list_devices", return_value=MOCK_RAW_DEVICES)
    def test_get_all_connected_devices(self, mock_list):
        devices = get_all_connected_devices()
        self.assertEqual(len(devices), 4)
        # Verify iPhones are ordered first, followed by iPad, then unpaired
        self.assertTrue(devices[0]["product"].startswith("iPhone"))
        self.assertTrue(devices[1]["product"].startswith("iPhone"))
        self.assertTrue(devices[2]["product"].startswith("iPad"))
        self.assertEqual(devices[3]["udid"], "fixture-unpaired")


if __name__ == "__main__":
    unittest.main()
