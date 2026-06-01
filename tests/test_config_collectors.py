import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_meter.collectors import system as system_mod
from ai_meter.collectors.claude_api_usage import ClaudeApiUsageCollector, ParsedUsage
from ai_meter.collectors.claude import ClaudeCollector
from ai_meter.collectors.codex import CodexCollector
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
    def test_claude_usage_api_enabled_by_default(self) -> None:
        # The OAuth usage endpoint consumes no model tokens and reuses the local
        # Claude Code login, so it is on by default to surface real 5h/7d limits.
        config = AppConfig()
        self.assertTrue(config.providers.claude.enabled)
        self.assertTrue(config.providers.claude.usage_api_enabled)
        self.assertGreaterEqual(config.providers.claude.usage_api_interval_s, 60)
        self.assertFalse(config.app.sensor_probe_enabled)

    def test_usage_collector_loads_oauth_token_from_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".credentials.json").write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "sk-ant-oat01--abc",
                            "expiresAt": 9_999_999_999_000,
                        }
                    }
                ),
                encoding="utf-8",
            )
            collector = ClaudeApiUsageCollector.from_credentials(home)
            self.assertIsNotNone(collector)
            assert collector is not None
            self.assertEqual(collector.token_source, "credentials")
            self.assertTrue(collector._token.startswith("Bearer sk-ant-oat01--"))
            self.assertFalse(collector._is_token_expired())

    def test_usage_collector_detects_expired_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".credentials.json").write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "Bearer sk-ant-oat01--old",
                            "expiresAt": 1_000,
                        }
                    }
                ),
                encoding="utf-8",
            )
            collector = ClaudeApiUsageCollector.from_credentials(home)
            assert collector is not None
            self.assertTrue(collector._is_token_expired())

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

    def test_system_collector_reads_linux_temperature_sensors(self) -> None:
        if system_mod.psutil is None:
            self.skipTest("psutil unavailable")
        collector = SystemCollector(sensor_probe_enabled=False)
        sensors = {
            "nvme": [SimpleNamespace(label="Composite", current=84.0)],
            "k10temp": [SimpleNamespace(label="Tctl", current=61.5)],
        }

        with patch.object(
            system_mod.psutil,
            "sensors_temperatures",
            return_value=sensors,
            create=True,
        ):
            temp, source = collector._read_linux_temp_fallback()

        self.assertEqual(temp, 61.5)
        self.assertEqual(source, "linux:k10temp:Tctl")

    def test_system_collector_does_not_use_nvme_as_cpu_temperature(self) -> None:
        if system_mod.psutil is None:
            self.skipTest("psutil unavailable")
        collector = SystemCollector(sensor_probe_enabled=False)
        sensors = {"nvme": [SimpleNamespace(label="Composite", current=84.0)]}

        with patch.object(system_mod.psutil, "sensors_temperatures", return_value=sensors, create=True):
            with patch.object(system_mod, "_linux_hwmon_temperature_readings", return_value=[]):
                temp, source = collector._read_linux_temp_fallback()

        self.assertIsNone(temp)
        self.assertEqual(source, "linux:no_data")

    def test_system_collector_reads_nvidia_smi_gpu_temperature(self) -> None:
        collector = SystemCollector(sensor_probe_enabled=False)
        completed = SimpleNamespace(stdout="NVIDIA GeForce RTX 4090, 51\n")

        with patch.object(system_mod, "_find_command", return_value=Path("nvidia-smi")):
            with patch.object(system_mod.subprocess, "run", return_value=completed):
                readings = collector._read_nvidia_smi_gpu_temps()

        self.assertEqual(readings[0]["name"], "NVIDIA GeForce RTX 4090")
        self.assertEqual(readings[0]["temp_c"], 51.0)
        self.assertEqual(readings[0]["source"], "nvidia-smi")

    def test_gpu_fallback_does_not_probe_amd_when_nvidia_is_available(self) -> None:
        collector = SystemCollector(sensor_probe_enabled=False)
        nvidia = [{"name": "NVIDIA GPU", "temp_c": 52.0, "source": "nvidia-smi"}]

        with patch.object(collector, "_read_nvidia_smi_gpu_temps", return_value=nvidia):
            with patch.object(collector, "_read_amd_smi_gpu_temps") as amd:
                temp, name, source, readings = collector._read_platform_gpu_temp_fallback()

        self.assertEqual(temp, 52.0)
        self.assertEqual(name, "NVIDIA GPU")
        self.assertEqual(source, "nvidia-smi")
        self.assertEqual(readings, nvidia)
        amd.assert_not_called()

    def test_windows_wmi_temperature_does_not_prompt_without_admin(self) -> None:
        collector = SystemCollector(sensor_probe_enabled=False)

        with patch.object(system_mod.os, "name", "nt"):
            with patch.object(system_mod, "_is_windows_admin", return_value=False):
                with patch.object(system_mod.subprocess, "run") as run:
                    temp, source = collector._read_wmi_temp_fallback()
                    gpu_readings = collector._read_wmi_gpu_temp_fallback()

        self.assertIsNone(temp)
        self.assertEqual(source, "wmi:admin_required")
        self.assertEqual(gpu_readings, [])
        run.assert_not_called()

    def test_windows_gpu_fallback_reports_admin_required_without_tools(self) -> None:
        collector = SystemCollector(sensor_probe_enabled=False)

        with patch.object(system_mod.os, "name", "nt"):
            with patch.object(system_mod, "_is_windows_admin", return_value=False):
                with patch.object(system_mod, "_find_command", return_value=None):
                    temp, name, source, readings = collector._read_platform_gpu_temp_fallback()

        self.assertIsNone(temp)
        self.assertIsNone(name)
        self.assertEqual(source, "wmi:admin_required")
        self.assertEqual(readings, [])

    def test_system_collector_parses_amd_smi_temperature_table(self) -> None:
        text = "GPU  XCP    POWER    GPU_T    MEM_T\n  0    0    110 W    47 \u00b0C    39 \u00b0C"

        readings = system_mod._parse_gpu_tool_output(text, source="amd-smi")

        self.assertEqual(readings[0]["name"], "AMD GPU 0")
        self.assertEqual(readings[0]["temp_c"], 47.0)
        self.assertEqual(readings[0]["source"], "amd-smi")

    def test_amd_smi_windows_candidates_do_not_scan_program_files(self) -> None:
        with patch.object(system_mod.os, "name", "nt"):
            with patch.dict(
                system_mod.os.environ,
                {
                    "AI_METER_AMD_SMI": r"D:\tools\amd-smi.exe",
                    "ProgramFiles": r"C:\Program Files",
                    "ProgramW6432": r"C:\Program Files",
                },
            ):
                candidates = system_mod._amd_smi_candidate_paths()

        self.assertIn(Path(r"D:\tools\amd-smi.exe"), candidates)
        self.assertIn(Path(r"C:\Program Files\AMD\ROCm\bin\amd-smi.exe"), candidates)
        self.assertFalse(any("*" in str(path) for path in candidates))

    def test_find_command_can_skip_path_lookup(self) -> None:
        system_mod._COMMAND_CACHE.clear()
        with patch.object(system_mod.shutil, "which", return_value=r"C:\Windows\system32\amd-smi.exe") as which:
            found = system_mod._find_command("amd-smi", extra_paths=[], include_path=False)

        self.assertIsNone(found)
        which.assert_not_called()

    def test_system_collector_reads_linux_amdgpu_temperature(self) -> None:
        if system_mod.psutil is None:
            self.skipTest("psutil unavailable")
        collector = SystemCollector(sensor_probe_enabled=False)
        sensors = {"amdgpu": [SimpleNamespace(label="edge", current=58.2)]}

        with patch.object(system_mod.psutil, "sensors_temperatures", return_value=sensors, create=True):
            with patch.object(system_mod, "_linux_hwmon_temperature_readings", return_value=[]):
                readings = collector._read_linux_gpu_temp_fallback()

        self.assertEqual(readings[0]["name"], "AMD GPU")
        self.assertEqual(readings[0]["temp_c"], 58.2)
        self.assertEqual(readings[0]["source"], "linux:amdgpu:edge")

    def test_winprobe_is_unavailable_outside_windows(self) -> None:
        sampler = system_mod.NativeWinProbeSampler()
        with patch.object(system_mod.os, "name", "posix"):
            self.assertFalse(sampler.available)
            self.assertFalse(sampler.status()["available"])
            self.assertIsNone(sampler.snapshot())

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

    def test_claude_collector_dedupes_streaming_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            project_dir = paths.claude_home / "projects" / "repo"
            project_dir.mkdir(parents=True)
            session_file = project_dir / "session.jsonl"
            rows = [
                {
                    "timestamp": "2026-05-15T12:00:00+00:00",
                    "type": "assistant",
                    "requestId": "req_stream",
                    "message": {
                        "id": "msg_1",
                        "model": "claude",
                        "usage": {"input_tokens": 10, "output_tokens": 2},
                    },
                },
                {
                    "timestamp": "2026-05-15T12:00:01+00:00",
                    "type": "assistant",
                    "requestId": "req_stream",
                    "message": {
                        "id": "msg_1",
                        "model": "claude",
                        "usage": {"input_tokens": 10, "output_tokens": 7},
                    },
                },
            ]
            session_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            collector = ClaudeCollector(paths, MemoryOffsetStore())
            first = collector.collect()

            self.assertEqual(len(first.usage_samples), 1)
            self.assertEqual(first.usage_samples[0].output_tokens, 7)
            self.assertEqual(first.usage_samples[0].total_tokens, 17)

    def test_codex_collector_reads_archived_sessions_and_normalizes_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            archived_dir = paths.codex_home / "archived_sessions"
            archived_dir.mkdir(parents=True)
            session_file = archived_dir / "archived.jsonl"
            row = {
                "timestamp": "2026-05-15T12:00:00+00:00",
                "payload": {
                    "rate_limits": {
                        "plan_type": "pro",
                        "primary": {
                            "used_percent": 40,
                            "window_minutes": 10080,
                            "resets_at": 4102444800,
                        },
                        "secondary": {
                            "used_percent": 12,
                            "window_minutes": 300,
                            "resets_at": 4102444800,
                        },
                    }
                },
            }
            session_file.write_text(json.dumps(row) + "\n", encoding="utf-8")

            collector = CodexCollector(paths, MemoryOffsetStore())
            batch = collector.collect()
            limits = batch.provider_status.metadata["rate_limits"]

            self.assertTrue(batch.provider_status.metadata["archived_sessions_dir"])
            self.assertEqual(limits["plan_type"], "pro")
            self.assertEqual(limits["primary"]["limit_window_seconds"], 18000)
            self.assertEqual(limits["secondary"]["limit_window_seconds"], 604800)
            self.assertGreater(limits["primary"]["reset_after_seconds"], 0)

    def test_codex_usage_uses_turn_context_model_and_cached_read_tokens(self) -> None:
        collector = CodexCollector(_paths(Path(tempfile.gettempdir())), MemoryOffsetStore())
        rows = [
            {
                "timestamp": "2026-05-15T12:00:00+00:00",
                "type": "turn_context",
                "payload": {"model": "gpt-5.5"},
            },
            {
                "timestamp": "2026-05-15T12:00:01+00:00",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 4,
                            "output_tokens": 3,
                        }
                    },
                },
            },
        ]

        usage = collector._collect_sessions_usage([(Path("session.jsonl"), rows)])

        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].model, "gpt-5.5")
        self.assertEqual(usage[0].cache_read_tokens, 4)
        self.assertEqual(usage[0].total_tokens, 17)

    def test_claude_api_usage_parses_model_and_extra_windows(self) -> None:
        parsed = ParsedUsage.from_api(
            {
                "five_hour": {"utilization": 10, "resets_at": "2026-05-21T12:00:00.000Z"},
                "seven_day_opus": {"utilization": 42, "resets_at": "2026-05-22T12:00:00.000Z"},
                "seven_day_cowork": {"utilization": 9, "resets_at": "2026-05-23T12:00:00.000Z"},
                "extra_usage": {
                    "is_enabled": True,
                    "utilization": 25,
                    "monthly_limit": 2000,
                    "used_credits": 500,
                    "currency": "USD",
                },
            }
        )

        names = [limit["name"] for limit in parsed.limits]
        self.assertIn("5h", names)
        self.assertIn("opus", names)
        self.assertIn("cowork", names)
        self.assertIn("extra", names)
        self.assertIsNotNone(parsed.five_hour_reset_secs)


if __name__ == "__main__":
    unittest.main()
