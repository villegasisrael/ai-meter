from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_meter.tui.widgets import render_bar, render_temp_bar


@dataclass(frozen=True)
class HardwarePanelSpec:
    id: str
    title: str
    kinds: tuple[str, ...]
    max_items: int = 8


class HardwarePanelRenderer:
    """Render normalized metrics without knowing their provider."""

    def render(
        self,
        spec: HardwarePanelSpec,
        metrics: list[dict[str, Any]],
        provider_status: dict[str, dict[str, Any]],
        width: int,
    ) -> str:
        kind_rank = {kind: index for index, kind in enumerate(spec.kinds)}
        visible = sorted(
            (
                metric
                for metric in metrics
                if str(metric.get("kind") or "") in spec.kinds
            ),
            key=lambda metric: (
                kind_rank.get(str(metric.get("kind") or ""), len(kind_rank)),
                str(metric.get("device_type") or ""),
                str(metric.get("device_id") or ""),
            ),
        )[: spec.max_items]
        lines = [f"[bold orange1]{spec.title}[/]"]
        lines.extend(self._render_metric(metric, width) for metric in visible)

        temperature_devices = {
            str(metric.get("device_type") or "")
            for metric in visible
            if metric.get("kind") == "temperature"
        }
        if "temperature" in spec.kinds and "cpu" not in temperature_devices:
            lines.append(self._unknown_cpu_line(provider_status))
        if "temperature" in spec.kinds and "gpu" not in temperature_devices:
            lines.append("[grey60]GPU temp[/] [grey40]unknown[/]")
        if len(lines) == 1:
            lines.append("[grey40]no hardware metrics[/]")
        return "\n".join(lines)

    def _render_metric(self, metric: dict[str, Any], width: int) -> str:
        kind = str(metric.get("kind") or "metric")
        label = str(metric.get("label") or kind)[:28]
        value = _number(metric.get("value"))
        unit = str(metric.get("unit") or "")
        if value is None:
            return f"[grey60]{label}[/] [grey40]unknown[/]"

        label_width = min(20, max(10, width // 3))
        compact_label = label[:label_width]
        bar_width = max(10, min(80, width - label_width - 24))
        if kind == "temperature":
            visual = render_temp_bar(value, max_temp=100.0, width=bar_width)
        elif kind == "utilization" and unit == "%":
            visual = render_bar(value, bar_width)
        else:
            visual = f"[white]{value:.1f} {unit}[/]"
        writable = " [yellow]control[/]" if bool(metric.get("writable")) else ""
        return f"[grey70]{compact_label:<{label_width}}[/] {visual}{writable}"

    @staticmethod
    def _unknown_cpu_line(provider_status: dict[str, dict[str, Any]]) -> str:
        platform = provider_status.get("platform") or {}
        detail = str(platform.get("detail") or "unknown")
        metadata = platform.get("metadata") or {}
        vendor = "unknown"
        if isinstance(metadata, dict):
            detail = str(metadata.get("cpu_source") or detail)
            vendor = str(metadata.get("cpu_vendor") or vendor)
        amd = provider_status.get("amd_ryzen_master") or {}
        if vendor == "amd" and not bool(amd.get("available")):
            amd_detail = str(amd.get("detail") or "unavailable")
            return f"[grey60]CPU temp[/] [grey40]unknown ({detail}; AMD {amd_detail})[/]"
        if vendor == "amd":
            error = str(amd.get("last_error") or detail)
            return f"[grey60]CPU temp[/] [grey40]unknown ({error})[/]"
        if vendor == "intel":
            return f"[grey60]CPU temp[/] [grey40]unknown ({detail}; Intel unsupported)[/]"
        return f"[grey60]CPU temp[/] [grey40]unknown ({detail})[/]"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
