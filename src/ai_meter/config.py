from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


class AppSection(BaseModel):
    refresh_interval_ms: int = 1000
    collector_light_interval_ms: int = 250
    collector_heavy_interval_s: int = 10
    winprobe_interval_ms: int = 250
    retention_days: int = 90
    theme: str = "btop_dark"
    start_paused: bool = False
    persist_history: bool = False


class PrivacySection(BaseModel):
    network_disabled: bool = True
    local_only: bool = True
    redact_secrets: bool = True
    store_raw_events: bool = False


class ProviderSection(BaseModel):
    enabled: bool = True
    source_priority: list[str] = Field(default_factory=list)


class ClaudeProviderSection(ProviderSection):
    usage_api_enabled: bool = False
    usage_api_interval_s: int = 900


class ProvidersSection(BaseModel):
    codex: ProviderSection = Field(
        default_factory=lambda: ProviderSection(source_priority=["sessions_jsonl", "logs_sqlite"])  # noqa: E501
    )
    claude: ClaudeProviderSection = Field(
        default_factory=lambda: ClaudeProviderSection(source_priority=["projects_jsonl", "stats_cache"])  # noqa: E501
    )
    system: ProviderSection = Field(
        default_factory=lambda: ProviderSection(source_priority=["psutil"])
    )


class UISection(BaseModel):
    sparkline_points: int = 60
    max_events: int = 200
    cpu_render_interval_ms: int = 250
    system_render_interval_ms: int = 500
    ai_render_interval_ms: int = 1000
    events_render_interval_ms: int = 500


class AppConfig(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    privacy: PrivacySection = Field(default_factory=PrivacySection)
    providers: ProvidersSection = Field(default_factory=ProvidersSection)
    ui: UISection = Field(default_factory=UISection)


def _toml_dump_simple(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def write_section(section: dict[str, Any], prefix: str = "") -> None:
        for key, value in section.items():
            if isinstance(value, dict):
                header = f"{prefix}.{key}" if prefix else key
                lines.append(f"[{header}]")
                write_section(value, header)
            else:
                lines.append(f"{key} = {_format_value(value)}")

    def write_top(top: dict[str, Any]) -> None:
        for key, value in top.items():
            if isinstance(value, dict):
                lines.append(f"[{key}]")
                write_section(value, key)
            else:
                lines.append(f"{key} = {_format_value(value)}")

    write_top(data)
    # Remove duplicate headings caused by recursive flow.
    cleaned: list[str] = []
    seen_consecutive = None
    for ln in lines:
        if ln.startswith("[") and ln == seen_consecutive:
            continue
        cleaned.append(ln)
        seen_consecutive = ln if ln.startswith("[") else None
    return "\n".join(cleaned) + "\n"


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(v) for v in value) + "]"
    return '""'


@dataclass
class ConfigManager:
    path: Path

    def load(self) -> AppConfig:
        if not self.path.exists():
            config = AppConfig()
            self.save(config)
            return config
        raw = tomllib.loads(self.path.read_text(encoding="utf-8"))
        return AppConfig.model_validate(raw)

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = config.model_dump(mode="json")
        self.path.write_text(_toml_dump_simple(payload), encoding="utf-8")
