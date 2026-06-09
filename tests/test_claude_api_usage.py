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


class CredentialsTests(unittest.TestCase):
    def _write_creds(self, home: Path, token: str, refresh: str = "r-1") -> Path:
        home.mkdir(parents=True, exist_ok=True)
        path = home / ".credentials.json"
        path.write_text(
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": token,
                        "refreshToken": refresh,
                        "expiresAt": int(datetime.now(timezone.utc).timestamp() * 1000) + 3_600_000,
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_from_credentials_loads_refresh_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".claude"
            self._write_creds(home, "tok-1", refresh="refresh-abc")
            collector = ClaudeApiUsageCollector.from_credentials(home)
            self.assertIsNotNone(collector)
            self.assertEqual(collector._refresh_token, "refresh-abc")
            self.assertEqual(collector._token, "Bearer tok-1")

    def test_reload_picks_up_new_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".claude"
            self._write_creds(home, "tok-1")
            collector = ClaudeApiUsageCollector.from_credentials(home)
            # Simulate a re-login writing a new token with a newer mtime.
            path = self._write_creds(home, "tok-2")
            import os
            os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
            collector._maybe_reload_credentials()
            self.assertEqual(collector._token, "Bearer tok-2")


if __name__ == "__main__":
    unittest.main()
