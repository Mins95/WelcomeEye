import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class Crc32cPackagingTests(unittest.TestCase):
    def test_manifest_pins_native_crc32c_dependency(self):
        manifest = json.loads(
            (ROOT / "custom_components/welcomeeye_local/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("google-crc32c==1.8.0", manifest["requirements"])

    def test_integration_does_not_suppress_crc_warnings(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "custom_components/welcomeeye_local").glob("*.py")
        )
        self.assertNotIn("filterwarnings", source)
        self.assertNotIn("warnings.filter", source)


if __name__ == "__main__":
    unittest.main()
