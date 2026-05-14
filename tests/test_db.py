import tempfile
import unittest
from pathlib import Path

from ai_meter.storage.db import Database


class DbTests(unittest.TestCase):
    def test_schema_and_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "a.db")
            db.set_offset("file:test", 123)
            self.assertEqual(db.get_offset("file:test"), 123)
            rows = db.provider_status_rows()
            self.assertIsInstance(rows, list)


if __name__ == "__main__":
    unittest.main()
