import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ai_meter.collectors.claude_api_usage import ClaudeApiUsageCollector, ParsedUsage

_SAMPLE = {
    "five_hour": {"utilization": 42.0, "resets_at": None},
    "seven_day": {"utilization": 7.0, "resets_at": None},
}


class ParsedUsageTests(unittest.TestCase):
    def test_from_api_parses_percentages(self) -> None:
        parsed = ParsedUsage.from_api(_SAMPLE)
        self.assertEqual(parsed.five_hour_pct, 42.0)
        self.assertEqual(parsed.seven_day_pct, 7.0)
        self.assertIsNotNone(parsed.fetched_epoch)

    def test_age_seconds_uses_fetched_epoch(self) -> None:
        past = datetime.now(timezone.utc).timestamp() - 600
        parsed = ParsedUsage.from_api(_SAMPLE, fetched_epoch=past)
        self.assertGreaterEqual(parsed.age_seconds, 600)


class CacheTests(unittest.TestCase):
    def test_loads_stale_value_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "usage.json"
            cache.write_text(
                json.dumps(
                    {
                        "fetched_epoch": datetime.now(timezone.utc).timestamp() - 300,
                        "data": _SAMPLE,
                    }
                ),
                encoding="utf-8",
            )
            collector = ClaudeApiUsageCollector("Bearer x", cache_path=cache)
            current = collector.current()
            self.assertIsNotNone(current)
            self.assertEqual(current.five_hour_pct, 42.0)
            self.assertTrue(collector.is_stale)

    def test_no_cache_file_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = ClaudeApiUsageCollector("Bearer x", cache_path=Path(tmp) / "missing.json")
            self.assertIsNone(collector.current())
            self.assertFalse(collector.is_stale)


if __name__ == "__main__":
    unittest.main()
