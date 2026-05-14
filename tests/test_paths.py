import unittest

from ai_meter.paths import resolve_app_paths


class PathsTests(unittest.TestCase):
    def test_resolve_paths(self) -> None:
        paths = resolve_app_paths("ai-meter-test")
        self.assertTrue(paths.config_dir.exists())
        self.assertTrue(paths.data_dir.exists())


if __name__ == "__main__":
    unittest.main()
