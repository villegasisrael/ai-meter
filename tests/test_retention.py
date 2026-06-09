import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_meter.models import EventRecord, UsageRecord
from ai_meter.storage.db import Database


def _event(ts: datetime) -> EventRecord:
    return EventRecord(
        provider="claude",
        timestamp=ts,
        event_type="test",
        title="t",
        message="m",
    )


def _usage(ts: datetime) -> UsageRecord:
    return UsageRecord(provider="claude", timestamp=ts, total_tokens=1)


class RetentionTests(unittest.TestCase):
    def test_purge_removes_only_old_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "a.db")
            now = datetime.now(timezone.utc)
            old = now - timedelta(days=120)
            recent = now - timedelta(days=1)
            db.ingest_batch(None, [_event(old), _event(recent)], [_usage(old), _usage(recent)])

            deleted = db.purge_old(90)

            self.assertEqual(deleted["events"], 1)
            self.assertEqual(deleted["usage_samples"], 1)
            self.assertEqual(len(db.recent_events(limit=100)), 1)
            self.assertEqual(len(db.latest_usage_by_provider("claude", limit=100)), 1)

    def test_purge_disabled_when_non_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "a.db")
            old = datetime.now(timezone.utc) - timedelta(days=400)
            db.ingest_batch(None, [_event(old)], [])
            deleted = db.purge_old(0)
            self.assertEqual(deleted["events"], 0)
            self.assertEqual(len(db.recent_events(limit=100)), 1)


if __name__ == "__main__":
    unittest.main()
