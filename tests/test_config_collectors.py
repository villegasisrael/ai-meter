import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_meter.collectors.claude import ClaudeCollector
from ai_meter.collectors.system import SystemCollector
from ai_meter.config import AppConfig, ConfigManager
from ai_meter.paths import AppPaths
from ai_meter.runtime_store import MemoryOffsetStore


def _paths(root: Path) -> AppPaths:
    return AppPaths(
        config_dir=root / "config",
        data_dir=root / "data",
        config_file=root / "config" / "config.toml",
        db_file=root / "data" / "ai-meter.db",
        export_dir=root / "data" / "exports",
        codex_home=root / ".codex",
        claude_home=root / ".claude",
    )


class ConfigCollectorTests(unittest.TestCase):
    def test_claude_usage_api_is_opt_in(self) -> None:
        config = AppConfig()
        self.assertTrue(config.providers.claude.enabled)
        self.assertFalse(config.providers.claude.usage_api_enabled)
        self.assertGreaterEqual(config.providers.claude.usage_api_interval_s, 60)
        self.assertFalse(config.app.sensor_probe_enabled)

    def test_config_round_trip_keeps_provider_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            manager = ConfigManager(path)
            config = AppConfig()
            config.providers.codex.enabled = False
            config.providers.claude.usage_api_enabled = True
            config.providers.claude.usage_api_interval_s = 1200
            manager.save(config)

            loaded = manager.load()
            self.assertFalse(loaded.providers.codex.enabled)
            self.assertTrue(loaded.providers.claude.usage_api_enabled)
            self.assertEqual(loaded.providers.claude.usage_api_interval_s, 1200)
            self.assertFalse(loaded.app.sensor_probe_enabled)

    def test_system_collector_does_not_run_sensor_probe_by_default(self) -> None:
        collector = SystemCollector(sensor_probe_enabled=False)

        def fail_probe() -> dict[str, object]:
            raise AssertionError("sensor probe should not run by default")

        collector._run_sensor_probe = fail_probe  # type: ignore[method-assign]
        with patch.object(collector, "_read_wmi_temp_fallback", return_value=(None, "wmi:no_data")):
            collector._refresh_sensor_probe()

        self.assertEqual(
            collector._last_temp_probe_meta.get("error"),
            "sensor_probe_disabled",
        )

    def test_claude_collector_reads_initial_tail_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            project_dir = paths.claude_home / "projects" / "repo"
            project_dir.mkdir(parents=True)
            session_file = project_dir / "session.jsonl"
            row = {
                "timestamp": "2026-05-15T12:00:00+00:00",
                "type": "assistant",
                "message": {
                    "model": "claude",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_creation_input_tokens": 2,
                        "cache_read_input_tokens": 3,
                    },
                },
            }
            session_file.write_text(json.dumps(row) + "\n", encoding="utf-8")

            collector = ClaudeCollector(paths, MemoryOffsetStore())
            first = collector.collect()
            second = collector.collect()

            self.assertEqual(len(first.usage_samples), 1)
            self.assertEqual(first.usage_samples[0].total_tokens, 20)
            self.assertEqual(second.usage_samples, [])


if __name__ == "__main__":
    unittest.main()
