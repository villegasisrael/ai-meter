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
    table.add_row("external_calls", "disabled")
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

    system_batch = SystemCollector().collect(include_temp=True, include_top=False)
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
    table.add_row("sensor probe", json.dumps(temp_probe, ensure_ascii=False))
    if temp_c is not None:
        cpu_temp_str = f"{temp_c} C ({temp_source})"
    elif needs_admin and from_service:
        cpu_temp_str = f"unknown ({temp_source}) — driver blocked (HVCI?); run install-service as admin"
    elif needs_admin:
        cpu_temp_str = f"unknown ({temp_source}) — run install-service as Administrator"
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
    engine = MonitorEngine(config=config, paths=paths, offset_store=offset_store, db=persist_db)
    app = AiMeterTui(engine)
    app.run()


@cli.command("install-service")
def install_service() -> None:
    """Install background sensor task for CPU temperature (requires admin, run ONCE)."""
    if os.name != "nt":
        console.print("[red]Only supported on Windows.[/]")
        raise typer.Exit(code=1)

    if not ctypes.windll.shell32.IsUserAnAdmin():
        console.print("[red]This command requires Administrator.[/]")
        console.print("Right-click PowerShell → 'Run as Administrator', then run again:")
        console.print("  [bold]python -m ai_meter.main install-service[/]")
        raise typer.Exit(code=1)

    from ai_meter.collectors.system import _find_bundled_tool
    probe_exe = _find_bundled_tool("ai-meter-sensor-probe.exe")
    if probe_exe is None:
        console.print("[red]ai-meter-sensor-probe.exe not found. Run scripts/build_native.ps1 first.[/]")
        raise typer.Exit(code=1)

    out_dir = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ai-meter"
    out_file = out_dir / "sensor.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    task_name = "ai-meter-sensor"
    probe_str = str(probe_exe).replace("'", "''")  # escape single quotes for PS
    out_str = str(out_file).replace("'", "''")

    # Write to a temp .ps1 file to avoid all inline quoting/escaping issues
    import tempfile
    ps_lines = [
        "$ErrorActionPreference = 'Stop'",
        # Use the current interactive user with RunLevel Highest (S4U = no password stored).
        # Running as SYSTEM (session 0) blocks LHM's WinRing0x64.sys driver init on Ryzen.
        "$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
        f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue",
        f"$action    = New-ScheduledTaskAction -Execute '{probe_str}' -Argument '--loop-to \"{out_str}\" 5000'",
        "$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $currentUser",
        "$settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 3650) -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable",
        # Interactive = runs in the user's own session (same context as manually running as admin).
        # S4U runs in session 0 (isolated) which blocks WinRing0x64.sys init on Ryzen/HVCI systems.
        "$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest",
        f"Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null",
        f"Start-ScheduledTask -TaskName '{task_name}'",
        "Write-Host 'OK'",
    ]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write("\n".join(ps_lines))
        tmp_path = tmp.name

    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp_path],
        capture_output=True, text=True,
    )
    try:
        Path(tmp_path).unlink()
    except Exception:
        pass
    if result.returncode != 0 or "OK" not in result.stdout:
        console.print(f"[red]Failed:[/] {result.stderr.strip() or result.stdout.strip()}")
        raise typer.Exit(code=1)

    # Persist the WinRing0 kernel driver as an auto-start system service.
    # Task Scheduler's elevated token (even RunLevel Highest) lacks SE_LOAD_DRIVER_PRIVILEGE,
    # but THIS process (running under the user's full UAC-elevated admin token) can load it.
    # Once installed as auto-start, future probe runs (any privilege) just open the device handle.
    console.print("Installing kernel driver for CPU temperature...")
    driver_result = subprocess.run(
        [str(probe_exe), "--persist-driver"],
        capture_output=True, text=True, timeout=20,
    )
    driver_out = (driver_result.stdout or "").strip()
    driver_ok = driver_out.startswith("persist:ok")
    if driver_ok:
        console.print("[green]Kernel driver installed.[/] CPU temperature available after reboot.")
    else:
        detail = driver_out or (driver_result.stderr or "no output").strip()
        console.print(f"[yellow]Driver persistence failed:[/] {detail}")
        if "INVALID_IMAGE_HASH" in detail or "lhm_open_failed" in detail:
            console.print("[yellow]Hint:[/] HVCI (Memory Integrity) is likely blocking WinRing0x64.sys.")
            console.print("  → Windows Security → Device Security → Core isolation → Memory Integrity → Off → Reboot")
            console.print("  → Then run this command again as Administrator.")
        elif "driver_not_in_registry" in detail:
            console.print("[yellow]Hint:[/] LHM opened but driver wasn't registered. Try rebooting and re-running.")
        console.print("CPU temp will remain unavailable until the driver is installed.")

    console.print(f"[green]Installed![/] Sensor task registered (runs at logon).")
    console.print(f"Output file: [cyan]{out_file}[/]")
    console.print("Start the monitor normally: [bold]python -m ai_meter.main run[/]")


@cli.command("uninstall-service")
def uninstall_service() -> None:
    """Remove background sensor task."""
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
Write-Host 'OK'
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True, text=True,
    )
    if "OK" in result.stdout:
        console.print("[green]Uninstalled.[/] Sensor task and kernel driver removed.")
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
