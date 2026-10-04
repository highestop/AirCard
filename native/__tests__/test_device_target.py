import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == "darwin", "The native target callback requires macOS")
class DeviceTargetTests(unittest.TestCase):
    def test_production_callback_matches_mixed_case_usb_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "test_device_target"
            subprocess.run(
                ["xcrun", "clang", "-fobjc-arc", "-Wall", "-Wextra", "-Werror",
                 "-framework", "Foundation", "-framework", "CoreFoundation",
                 "/System/Library/PrivateFrameworks/MobileDevice.framework/MobileDevice",
                 str(Path(__file__).with_suffix(".m")), "-o", str(executable)],
                check=True, capture_output=True, text=True,
            )
            result = subprocess.run(
                [str(executable)], check=True, capture_output=True, text=True,
            )
        self.assertIn("Production USB target callback matches case variants", result.stdout)


if __name__ == "__main__":
    unittest.main()
