from importlib.resources import files
import json
import unittest


class InstalledDependencyPinTests(unittest.TestCase):
    """The installed asciiunits runtime carries the SDK 5206 Parsanol build."""

    def test_asciiunits_native_build(self):
        info = json.loads(files("asciiunits").joinpath("_native", "build-info.json").read_bytes())
        self.assertEqual(info["native_source_commit"], "1f771caef878b9d383a5f29b02f434b8b614b780")
        self.assertEqual(info["parsanol"], "5206a33b5417b4ecd3a601930461ed2967c871e7")

    def test_unitsml_native_build(self):
        info = json.loads(files("unitsml").joinpath("_native", "build-info.json").read_bytes())
        self.assertEqual(info["native_source_commit"], "14c17076b7199940acaf9ce1f0272f3808b389bd")
        notices = json.loads(files("unitsml").joinpath("_native", "licenses", "manifest.json").read_bytes())
        self.assertNotIn("parsanol", [row["name"] for row in notices])


if __name__ == "__main__":
    unittest.main()
