from __future__ import annotations

import csv
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ai_meter.app import MonitorEngine
from ai_meter.collectors.system import SystemCollector
from ai_meter.config import AppConfig, ConfigManager
from ai_meter.paths import resolve_app_paths
from ai_meter.runtime_store import MemoryOffsetStore
from ai_meter.storage.db import Database
from ai_meter.tui.app import AiMeterTui

cli = typer.Typer(help="ai-meter local-only token usage monitor")
console = Console()


def _bootstrap() -> tuple[AppConfig, Database]:
    paths = resolve_app_paths("ai-meter")
    config_mgr = ConfigManager(paths.config_file)
    config = config_mgr.load()
    db = Database(paths.db_file)
    return config, db


@cli.command()
def doctor() -> None:
    """Show local diagnostics and confirm network-disabled mode."""
    paths = resolve_app_paths("ai-meter")
    config_mgr = ConfigManager(paths.config_file)
    config = config_mgr.load()
    db = Database(paths.db_file)

    table = Table(title="AI Meter Doctor")
    table.add_column("Check")
    table.add_column("Value")

    table.add_row("network_disabled", str(config.privacy.network_disabled).lower())
    table.add_row("model_calls", "disabled")
    table.add_row("providers.codex.enabled", str(config.providers.codex.enabled).lower())
    table.add_row("providers.claude.enabled", str(config.providers.claude.enabled).lower())
    table.add_row(
        "claude usage_api",
        "enabled"
        if config.providers.claude.enabled and config.providers.claude.usage_api_enabled
        else "disabled",
    )
    table.add_row("config_file", str(paths.config_file))
    table.add_row("db_file", str(paths.db_file))

    codex_home = paths.codex_home
    table.add_row("codex_home", str(codex_home))
    table.add_row("codex config.toml", str((codex_home / "config.toml").exists()).lower())
    table.add_row("codex session_index.jsonl", str((codex_home / "session_index.jsonl").exists()).lower())
    table.add_row("codex sessions dir", str((codex_home / "sessions").exists()).lower())
    table.add_row("codex logs_2.sqlite", str((codex_home / "logs_2.sqlite").exists()).lower())
    table.add_row("codex auth.json", "present (not read)" if (codex_home / "auth.json").exists() else "not found")

    claude_home = paths.claude_home
    table.add_row("claude_home", str(claude_home))
    table.add_row("claude projects", str((claude_home / "projects").exists()).lower())
    table.add_row("claude history.jsonl", str((claude_home / "history.jsonl").exists()).lower())
    table.add_row("claude stats-cache.json", str((claude_home / "stats-cache.json").exists()).lower())

    system_batch = SystemCollector(
        winprobe_interval_ms=config.app.winprobe_interval_ms,
        sensor_probe_enabled=config.app.sensor_probe_enabled,
    ).collect(include_temp=True, include_top=False)
    system_meta = system_batch.provider_status.metadata if system_batch.provider_status else {}
    native_status = system_meta.get("native_winprobe", {}) if isinstance(system_meta, dict) else {}
    temp_c = system_meta.get("cpu_temp_c") if isinstance(system_meta, dict) else None
    temp_source = system_meta.get("cpu_temp_source") if isinstance(system_meta, dict) else "unknown"
    temp_probe = system_meta.get("cpu_temp_probe", {}) if isinstance(system_meta, dict) else {}
    gpu_temp_c = system_meta.get("gpu_temp_c") if isinstance(system_meta, dict) else None
    gpu_name = system_meta.get("gpu_name") if isinstance(system_meta, dict) else None
    core_count = len(system_meta.get("core_loads") or []) if isinstance(system_meta, dict) else 0
    needs_admin = bool(system_meta.get("needs_admin")) if isinstance(system_meta, dict) else False
    from_service = bool(temp_probe.get("from_service")) if isinstance(temp_probe, dict) else False
    table.add_row("system metrics_source", str(system_meta.get("metrics_source", "unknown")))
    table.add_row("native winprobe", json.dumps(native_status, ensure_ascii=False))
    table.add_row("sensor probe enabled", str(config.app.sensor_probe_enabled).lower())
    table.add_row("sensor probe", json.dumps(temp_probe, ensure_ascii=False))
    if temp_c is not None:
        cpu_temp_str = f"{temp_c} C ({temp_source})"
    elif needs_admin and from_service:
        cpu_temp_str = f"unknown ({temp_source}) - driver blocked"
    elif needs_admin:
        cpu_temp_str = f"unknown ({temp_source}) - sensor probe disabled"
    else:
        cpu_temp_str = f"unknown ({temp_source})"
    table.add_row("cpu temp", cpu_temp_str)
    table.add_row("gpu temp", f"{gpu_temp_c} C ({gpu_name})" if gpu_temp_c is not None else "unknown")
    table.add_row("cpu cores tracked", str(core_count))

    table.add_row("sqlite initialized", str(paths.db_file.exists()).lower())
    table.add_row("provider rows", str(len(db.provider_status_rows())))

    console.print(table)


@cli.command()
def paths() -> None:
    """Print resolved local paths."""
    p = resolve_app_paths("ai-meter")
    table = Table(title="AI Meter Paths")
    table.add_column("Name")
    table.add_column("Path")
    for name, value in [
        ("config_dir", p.config_dir),
        ("config_file", p.config_file),
        ("data_dir", p.data_dir),
        ("db_file", p.db_file),
        ("export_dir", p.export_dir),
        ("codex_home", p.codex_home),
        ("claude_home", p.claude_home),
    ]:
        table.add_row(name, str(value))
    console.print(table)


@cli.command()
def export(
    format: str = typer.Option("json", "--format", help="json or csv"),
    out: Path | None = typer.Option(None, "--out", help="Output file path"),
) -> None:
    """Export local sqlite data."""
    _, db = _bootstrap()
    payload = db.export_rows()

    fmt = format.lower().strip()
    if out is None:
        default_name = f"ai-meter-export.{fmt}"
        out = resolve_app_paths("ai-meter").export_dir / default_name

    if fmt == "json":
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt == "csv":
        # Flatten by writing one csv per logical table, suffix naming.
        base = out.with_suffix("")
        for table, rows in payload.items():
            table_path = Path(f"{base}_{table}.csv")
            if not rows:
                table_path.write_text("", encoding="utf-8")
                continue
            keys = list(rows[0].keys())
            with table_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(rows)
        console.print(f"CSV export generated with prefix: {base}")
        return
    else:
        raise typer.BadParameter("--format must be json or csv")

    console.print(f"Export created: {out}")


@cli.command()
def run() -> None:
    """Run Textual TUI monitor."""
    paths = resolve_app_paths("ai-meter")
    config_mgr = ConfigManager(paths.config_file)
    config = config_mgr.load()

    if not config.privacy.network_disabled:
        console.print("Config invalid for strict mode: network_disabled must be true")
        raise typer.Exit(code=2)

    persist_db = Database(paths.db_file) if config.app.persist_history else None
    offset_store = persist_db if persist_db is not None else MemoryOffsetStore()
    import os as _os
    _os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")
    engine = MonitorEngine(config=config, paths=paths, offset_store=offset_store, db=persist_db)
    app = AiMeterTui(engine)
    app.run()


@cli.command("install-service")
def install_service() -> None:
    """Deprecated: legacy sensor task used a vulnerable kernel driver."""
    if os.name != "nt":
        console.print("[red]Only supported on Windows.[/]")
        raise typer.Exit(code=1)

    console.print("[red]Disabled.[/] The legacy sensor service used WinRing0/LibreHardwareMonitor.")
    console.print("Windows/EDR can block that kernel driver as vulnerable; ai-meter will not install it.")
    console.print("Use [bold]python -m ai_meter.main uninstall-service[/] as Administrator to remove old installs.")
    console.print("CPU/GPU temperature may show [bold]unknown[/]; CPU/RAM/disk/net still use safe user-mode probes.")
    raise typer.Exit(code=2)


@cli.command("uninstall-service")
def uninstall_service() -> None:
    """Remove legacy background sensor task and driver files."""
    if os.name != "nt":
        console.print("[red]Only supported on Windows.[/]")
        raise typer.Exit(code=1)

    if not ctypes.windll.shell32.IsUserAnAdmin():
        console.print("[red]This command requires Administrator.[/]")
        console.print("Right-click PowerShell → 'Run as Administrator', then run again.")
        raise typer.Exit(code=1)

    ps_script = """
Stop-ScheduledTask  -TaskName 'ai-meter-sensor' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'ai-meter-sensor' -Confirm:$false -ErrorAction SilentlyContinue
sc.exe stop WinRing0_1_2_0 | Out-Null
sc.exe delete WinRing0_1_2_0 | Out-Null
Remove-Item "$env:ProgramData\ai-meter\sensor.json" -Force -ErrorAction SilentlyContinue
Remove-Item "$env:ProgramData\ai-meter\WinRing0x64.sys" -Force -ErrorAction SilentlyContinue
Write-Host 'OK'
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True, text=True,
    )
    if "OK" in result.stdout:
        from ai_meter.collectors.system import _find_bundled_tool
        probe_exe = _find_bundled_tool("ai-meter-sensor-probe.exe")
        if probe_exe is not None:
            try:
                probe_exe.with_suffix(".sys").unlink(missing_ok=True)
            except Exception:
                pass
        console.print("[green]Uninstalled.[/] Legacy sensor task, driver and output files removed.")
    else:
        console.print(f"[yellow]Nothing to remove or error:[/] {result.stderr.strip()}")


@cli.command("init")
def init_config() -> None:
    """Create config and database if missing."""
    paths = resolve_app_paths("ai-meter")
    ConfigManager(paths.config_file).save(AppConfig())
    Database(paths.db_file)
    console.print(f"Initialized config: {paths.config_file}")
    console.print(f"Initialized database: {paths.db_file}")


if __name__ == "__main__":
    cli()
