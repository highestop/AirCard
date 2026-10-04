import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == "darwin", "Native cache verification requires macOS")
class CacheRemovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.executable = Path(cls.temporary.name) / "test_cache_removal"
        source = Path(__file__).with_name("test_cache_removal.m")
        subprocess.run(
            ["xcrun", "clang", "-fobjc-arc", "-Wall", "-Wextra", "-Werror",
             "-framework", "Foundation", str(source), "-o", str(cls.executable)],
            check=True, capture_output=True, text=True,
        )

    def test_cache_removal_evidence_and_directory_errors(self):
        result = subprocess.run(
            [str(self.executable)], check=True, capture_output=True, text=True,
        )
        self.assertIn("Cache removal evidence and complete directory listing checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
