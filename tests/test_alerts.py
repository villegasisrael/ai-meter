import tempfile
import unittest
from pathlib import Path

from ai_meter.app import MonitorEngine
from ai_meter.config import AppConfig
from ai_meter.paths import AppPaths
from ai_meter.runtime_store import MemoryOffsetStore


def _engine(tmp: str, threshold: float = 80.0, enabled: bool = True) -> MonitorEngine:
    config = AppConfig()
    config.app.alert_threshold_pct = threshold
    config.app.alert_enabled = enabled
    # Disable file-backed providers so no collectors touch the filesystem.
    config.providers.claude.enabled = False
    config.providers.codex.enabled = False
    base = Path(tmp)
    paths = AppPaths(
        config_dir=base,
        data_dir=base,
        config_file=base / "config.toml",
        db_file=base / "a.db",
        export_dir=base,
        codex_home=base / ".codex",
        claude_home=base / ".claude",
    )
    return MonitorEngine(config=config, paths=paths, offset_store=MemoryOffsetStore(), db=None)


class AlertTests(unittest.TestCase):
    def test_alert_fires_once_per_crossing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(tmp)
            engine._evaluate_threshold("codex", "5h", 92.0, 80.0)
            engine._evaluate_threshold("codex", "5h", 95.0, 80.0)  # still high, no dup
            events = [e for e in engine.snapshot().recent_events if e["event_type"] == "usage_alert"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["severity"], "warning")

    def test_alert_rearms_after_dropping_below(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(tmp)
            engine._evaluate_threshold("codex", "5h", 90.0, 80.0)
            engine._evaluate_threshold("codex", "5h", 10.0, 80.0)  # re-arm
            engine._evaluate_threshold("codex", "5h", 90.0, 80.0)  # fires again
            events = [e for e in engine.snapshot().recent_events if e["event_type"] == "usage_alert"]
            self.assertEqual(len(events), 2)

    def test_no_alert_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(tmp)
            engine._evaluate_threshold("claude", "weekly", 50.0, 80.0)
            events = [e for e in engine.snapshot().recent_events if e["event_type"] == "usage_alert"]
            self.assertEqual(len(events), 0)

    def test_disabled_alerts_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(tmp, enabled=False)
            engine._check_codex_alerts({"rate_limits": {"primary": {"used_percent": 99.0}}})
            events = [e for e in engine.snapshot().recent_events if e["event_type"] == "usage_alert"]
            self.assertEqual(len(events), 0)


if __name__ == "__main__":
    unittest.main()
