import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from rich.text import Text

from ai_meter.app import MonitorEngine
from ai_meter.config import AppConfig
from ai_meter.hardware.cpu_identity import CpuIdentity, classify_cpu_vendor
from ai_meter.hardware.factory import create_hardware_providers
from ai_meter.hardware.models import HardwareMetric, HardwareProviderStatus
from ai_meter.hardware.providers.amd_ryzen import AmdRyzenMasterProvider
from ai_meter.hardware.providers.base import HardwareProvider
from ai_meter.hardware.providers.platform import PlatformSensorProvider
from ai_meter.hardware.registry import HardwareRegistry
from ai_meter.paths import AppPaths
from ai_meter.runtime_store import MemoryOffsetStore
from ai_meter.tui.app import AiMeterTui
from ai_meter.tui.hardware import HardwarePanelRenderer, HardwarePanelSpec


class _FakeProvider(HardwareProvider):
    def __init__(self, name: str, metrics: list[HardwareMetric]) -> None:
        self.name = name
        self.metrics = metrics

    def probe(self) -> HardwareProviderStatus:
        return HardwareProviderStatus(
            name=self.name,
            available=True,
            source=self.name,
            metric_count=len(self.metrics),
        )

    def read(self) -> list[HardwareMetric]:
        return self.metrics


def _temperature(provider: str, source: str, value: float) -> HardwareMetric:
    return HardwareMetric(
        id="cpu:0.temperature",
        device_id="cpu:0",
        device_type="cpu",
        kind="temperature",
        label="CPU",
        value=value,
        unit="C",
        source=source,
        provider=provider,
        timestamp=datetime.now(timezone.utc),
    )


class HardwareRegistryTests(unittest.TestCase):
    def test_cpu_vendor_classification_uses_stable_vendor_identifiers(self) -> None:
        self.assertEqual(classify_cpu_vendor("AuthenticAMD"), "amd")
        self.assertEqual(classify_cpu_vendor("GenuineIntel"), "intel")
        self.assertEqual(classify_cpu_vendor("Apple"), "unknown")

    def test_factory_only_loads_provider_for_detected_cpu_vendor(self) -> None:
        system = SimpleNamespace()
        amd = create_hardware_providers(
            system,
            cpu_identity=CpuIdentity("amd", "AuthenticAMD", "AMD Ryzen"),
        )
        intel = create_hardware_providers(
            system,
            cpu_identity=CpuIdentity("intel", "GenuineIntel", "Intel Core"),
        )

        self.assertEqual(
            [provider.name for provider in amd],
            ["amd_ryzen_master", "platform"],
        )
        self.assertEqual([provider.name for provider in intel], ["platform"])

    def test_registry_selects_configured_source_priority(self) -> None:
        fallback = _FakeProvider(
            "platform",
            [_temperature("platform", "wmi:acpi", 55.0)],
        )
        amd = _FakeProvider(
            "amd_ryzen_master",
            [_temperature("amd_ryzen_master", "amd_ryzen_master_sdk", 61.0)],
        )
        registry = HardwareRegistry(
            [fallback, amd],
            source_priority=["amd_ryzen_master", "wmi"],
        )

        metrics = registry.collect()

        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0].value, 61.0)
        self.assertEqual(metrics[0].provider, "amd_ryzen_master")

    def test_registry_discards_stale_metrics(self) -> None:
        stale = _temperature("platform", "wmi:acpi", 55.0)
        stale.timestamp = datetime.now(timezone.utc) - timedelta(seconds=10)
        stale.stale_after_s = 1.0
        registry = HardwareRegistry([_FakeProvider("platform", [stale])])

        self.assertEqual(registry.collect(), [])

    def test_amd_provider_parses_flat_read_only_payload(self) -> None:
        provider = AmdRyzenMasterProvider()

        metrics = provider._parse_payload(
            {
                "cpu_temp_c": 62.5,
                "cpu_power_w": 88.0,
                "ppt_percent": 54.0,
            }
        )

        by_kind = {metric.kind: metric for metric in metrics}
        self.assertEqual(by_kind["temperature"].value, 62.5)
        self.assertEqual(by_kind["power"].unit, "W")
        self.assertFalse(any(metric.writable for metric in metrics))

    def test_amd_provider_preserves_probe_error(self) -> None:
        provider = AmdRyzenMasterProvider()
        provider._executable = Path("amd-probe.exe")
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"available":false,"error":"driver_not_running"}',
            stderr="",
        )

        with (
            patch(
                "ai_meter.hardware.providers.amd_ryzen._is_elevated",
                return_value=True,
            ),
            patch(
                "ai_meter.hardware.providers.amd_ryzen.subprocess.run",
                return_value=completed,
            ),
        ):
            self.assertEqual(provider.read(), [])

        self.assertEqual(provider.probe().last_error, "driver_not_running")

    def test_platform_provider_normalizes_cpu_and_gpu(self) -> None:
        system = SimpleNamespace()
        system.read_temperature_snapshot = lambda: {
            "cpu_temp_c": 58.0,
            "cpu_source": "linux:k10temp:Tctl",
            "gpu_temp_c": 51.0,
            "gpu_name": "NVIDIA GPU",
            "gpu_source": "nvidia-smi",
            "gpu_readings": [
                {
                    "name": "NVIDIA GPU",
                    "temp_c": 51.0,
                    "source": "nvidia-smi",
                }
            ],
        }
        provider = PlatformSensorProvider(system)

        metrics = provider.read()

        self.assertEqual(
            {(metric.device_type, metric.kind) for metric in metrics},
            {("cpu", "temperature"), ("gpu", "temperature")},
        )
        self.assertEqual(provider.probe().detail, "active")


class HardwarePanelTests(unittest.TestCase):
    def test_panel_renders_new_metric_kinds_without_layout_changes(self) -> None:
        renderer = HardwarePanelRenderer()
        spec = HardwarePanelSpec(
            id="hardware",
            title="HARDWARE",
            kinds=("temperature", "power"),
        )
        metrics = [
            _temperature("amd_ryzen_master", "amd_ryzen_master_sdk", 60.0).model_dump(
                mode="json"
            ),
            HardwareMetric(
                id="cpu:0.power",
                device_id="cpu:0",
                device_type="cpu",
                kind="power",
                label="CPU Power",
                value=75.0,
                unit="W",
                source="amd_ryzen_master_sdk",
                provider="amd_ryzen_master",
                timestamp=datetime.now(timezone.utc),
            ).model_dump(mode="json"),
        ]

        rendered = renderer.render(spec, metrics, {}, width=80)

        self.assertIn("CPU Power", rendered)
        self.assertIn("75.0 W", rendered)
        self.assertIn("GPU temp", rendered)
        self.assertNotIn("amd_ryzen_master_sdk", rendered)

    def test_panel_hides_sources_and_keeps_compact_lines(self) -> None:
        renderer = HardwarePanelRenderer()
        spec = HardwarePanelSpec(
            id="hardware",
            title="HARDWARE",
            kinds=("temperature",),
        )
        metrics = [
            _temperature(
                "amd_ryzen_master",
                "amd_ryzen_master_sdk",
                46.5,
            ).model_dump(mode="json"),
            HardwareMetric(
                id="gpu:0.temperature",
                device_id="gpu:0",
                device_type="gpu",
                kind="temperature",
                label="NVIDIA GeForce RTX 5060",
                value=48.0,
                unit="C",
                source="nvidia-smi",
                provider="platform",
                timestamp=datetime.now(timezone.utc),
            ).model_dump(mode="json"),
        ]

        rendered = renderer.render(spec, metrics, {}, width=64)

        self.assertNotIn("amd_ryzen_master_sdk", rendered)
        self.assertNotIn("nvidia-smi", rendered)
        for line in rendered.splitlines():
            self.assertLessEqual(len(Text.from_markup(line).plain), 64)

    def test_panel_explains_missing_amd_probe(self) -> None:
        rendered = HardwarePanelRenderer().render(
            HardwarePanelSpec(
                id="hardware",
                title="HARDWARE",
                kinds=("temperature",),
            ),
            [],
            {
                "platform": {
                    "detail": "cpu_wmi_admin_required",
                    "metadata": {"cpu_vendor": "amd"},
                },
                "amd_ryzen_master": {
                    "available": False,
                    "detail": "probe_not_installed",
                },
            },
            width=80,
        )

        self.assertIn("AMD probe_not_installed", rendered)

    def test_panel_does_not_report_amd_provider_for_intel_cpu(self) -> None:
        rendered = HardwarePanelRenderer().render(
            HardwarePanelSpec(
                id="hardware",
                title="HARDWARE",
                kinds=("temperature",),
            ),
            [],
            {
                "platform": {
                    "detail": "no_supported_sensor_data",
                    "metadata": {
                        "cpu_source": "wmi:no_data",
                        "cpu_vendor": "intel",
                    },
                },
            },
            width=80,
        )

        self.assertIn("Intel unsupported", rendered)
        self.assertNotIn("AMD", rendered)


class HardwareTuiSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_tui_mounts_extensible_hardware_panel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AppPaths(
                config_dir=root / "config",
                data_dir=root / "data",
                config_file=root / "config" / "config.toml",
                db_file=root / "data" / "ai-meter.db",
                export_dir=root / "data" / "exports",
                codex_home=root / ".codex",
                claude_home=root / ".claude",
            )
            config = AppConfig()
            config.providers.codex.enabled = False
            config.providers.claude.enabled = False
            engine = MonitorEngine(config, paths, MemoryOffsetStore())
            engine.run_light_collection = lambda: None  # type: ignore[method-assign]
            engine.run_heavy_collection = lambda: None  # type: ignore[method-assign]
            engine.run_hardware_collection = lambda: None  # type: ignore[method-assign]
            app = AiMeterTui(engine)

            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                self.assertEqual(app.query_one("#hardware").id, "hardware")
                self.assertEqual(
                    app._hardware_overview_panel.kinds,
                    ("temperature",),
                )
                self.assertIn("power", app._hardware_advanced_panel.kinds)

                await pilot.press("8")
                await pilot.pause()
                self.assertEqual(app.current_view, "8")
                self.assertFalse(app.query_one("#top_row").display)
                self.assertFalse(app.query_one("#system").display)
                self.assertFalse(app.query_one("#events").display)
                self.assertTrue(app.query_one("#hardware").display)

                await pilot.press("1")
                await pilot.pause()
                self.assertEqual(app.current_view, "1")
                self.assertTrue(app.query_one("#top_row").display)
                self.assertTrue(app.query_one("#system").display)
                self.assertTrue(app.query_one("#events").display)


if __name__ == "__main__":
    unittest.main()
