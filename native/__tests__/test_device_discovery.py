import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == "darwin", "The native discovery options require macOS")
class DeviceDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.executable = Path(cls.temporary.name) / "test_device_discovery"
        source = Path(__file__).with_name("test_device_discovery.m")
        subprocess.run(
            ["xcrun", "clang", "-fobjc-arc", "-Wall", "-Wextra", "-Werror",
             "-framework", "Foundation", str(source), "-o", str(cls.executable)],
            check=True, capture_output=True, text=True,
        )

    def test_device_options_request_usb_mux_only(self):
        result = subprocess.run(
            [str(self.executable)], check=True, capture_output=True, text=True,
        )
        self.assertIn("USB mux-only device discovery options passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
