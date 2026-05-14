from __future__ import annotations

from datetime import datetime
from threading import Thread
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Footer, Header, Static

from ai_meter.app import MonitorEngine
from ai_meter.models import RuntimeState
from ai_meter.tui.widgets import (
    latest_field,
    latest_total,
    render_bar,
    render_core_grid,
    render_sparkline,
    usage_values,
)


class AiMeterTui(App[None]):
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("p", "pause", "Pause"),
        Binding("+", "slower", "More ms"),
        Binding("=", "slower", "More ms"),
        Binding("-", "faster", "Less ms"),
        Binding("h", "help", "Help"),
        Binding("1", "view('1')", "Overview"),
        Binding("2", "view('2')", "Claude"),
        Binding("3", "view('3')", "Codex"),
        Binding("4", "view('4')", "Projects"),
        Binding("5", "view('5')", "Activity"),
        Binding("6", "view('6')", "Storage"),
        Binding("7", "view('7')", "Settings"),
    ]

    CSS = """
    Screen { background: #0a0e14; color: #cdd6f4; }
    #main { height: 1fr; }
    #left { width: 45%; height: auto; }
    #right { width: 55%; height: auto; }
    #events, #system { height: auto; }
    .panel { border: round #2a3f52; padding: 1; margin: 0 1 1 1; }
    #status_line { height: auto; padding: 0 1; color: #6e9ab0; background: #0d1117; }
    """

    def __init__(self, engine: MonitorEngine) -> None:
        super().__init__()
        self.engine = engine
        self.state = RuntimeState(paused=False)
        self.current_view = "1"
        self.refresh_ms = max(100, int(self.engine.config.app.refresh_interval_ms))
        self._render_timer: Timer | None = None
        self._light_collect_timer: Timer | None = None
        self._heavy_collect_timer: Timer | None = None
        self._api_collect_timer: Timer | None = None
        self._light_collecting = False
        self._heavy_collecting = False
        self._api_collecting = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status_line")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("", id="claude_panel", classes="panel")
                yield Static("", id="codex_panel", classes="panel")
            with Vertical(id="right"):
                yield Static("", id="system", classes="panel")
                yield Static("", id="events", classes="panel")
        yield Footer()

    def on_mount(self) -> None:
        self._start_timers()
        self._render_snapshot()
        self.set_timer(0.05, self._collect_light)
        self.set_timer(0.15, self._collect_heavy)
        # Fetch Claude API usage immediately on start, then every 60s
        if self.engine.claude_api is not None:
            self.set_timer(0.5, self._collect_claude_api)
            self._api_collect_timer = self.set_interval(60.0, self._collect_claude_api)

    def _start_timers(self) -> None:
        if self._render_timer is not None:
            self._render_timer.stop()
        self._render_timer = self.set_interval(self.refresh_ms / 1000.0, self._render_snapshot)
        light_interval_s = max(0.1, min(1.0, self.refresh_ms / 1000.0))
        if self._light_collect_timer is not None:
            self._light_collect_timer.stop()
        self._light_collect_timer = self.set_interval(light_interval_s, self._collect_light)
        if self._heavy_collect_timer is None:
            heavy_s = max(2.0, float(self.engine.config.app.collector_heavy_interval_s))
            self._heavy_collect_timer = self.set_interval(heavy_s, self._collect_heavy)

    def action_refresh_now(self) -> None:
        self._run_collection("heavy", self.engine.run_full_collection)
        self._render_snapshot()

    def action_pause(self) -> None:
        self.state.paused = not self.state.paused
        self._render_snapshot()

    def action_help(self) -> None:
        self.notify("keys: q r p -/+ h 1-7  |  run as admin for CPU temp")

    def action_faster(self) -> None:
        self.refresh_ms = max(100, self.refresh_ms - 100)
        self._start_timers()
        self._render_snapshot()

    def action_slower(self) -> None:
        self.refresh_ms = min(5000, self.refresh_ms + 100)
        self._start_timers()
        self._render_snapshot()

    def action_view(self, num: str) -> None:
        self.current_view = num
        self._render_snapshot()

    def _collect_light(self) -> None:
        if self.state.paused:
            return
        self._run_collection("light", self.engine.run_light_collection)

    def _collect_heavy(self) -> None:
        if self.state.paused:
            return
        self._run_collection("heavy", self.engine.run_heavy_collection)

    def _collect_claude_api(self) -> None:
        self._run_collection("api", self.engine.run_claude_api_collection)

    def _run_collection(self, kind: str, target: Any) -> None:
        attr = f"_{kind}_collecting"
        if bool(getattr(self, attr)):
            return
        setattr(self, attr, True)

        def runner() -> None:
            try:
                target()
            finally:
                setattr(self, attr, False)

        Thread(target=runner, daemon=True).start()

    def _render_snapshot(self) -> None:
        snap = self.engine.snapshot()
        paused_tag = "[yellow]PAUSED[/] | " if self.state.paused else ""
        api_tag = (
            "[green]usage-api=on[/]"
            if self.engine.claude_api is not None
            else "[grey50]usage-api=off[/]"
        )
        self.query_one("#status_line", Static).update(
            f"[bold cyan]view={self.current_view}[/] | {paused_tag}"
            f"model-calls=[red]off[/] | {api_tag} | "
            f"refresh=[white]{self.refresh_ms}ms[/] | "
            f"{datetime.now().strftime('%H:%M:%S')} | [green]+[/]/[red]-[/]"
        )

        claude_total = latest_total(snap.claude_usage)
        codex_total = latest_total(snap.codex_usage)
        claude_vals = usage_values(snap.claude_usage)
        codex_vals = usage_values(snap.codex_usage)
        claude_src = latest_field(snap.claude_usage, "source")
        codex_src = latest_field(snap.codex_usage, "source")
        claude_acc = latest_field(snap.claude_usage, "accuracy")
        codex_acc = latest_field(snap.codex_usage, "accuracy")
        claude_meta = snap.provider_meta.get("claude", {})
        codex_meta = snap.provider_meta.get("codex", {})
        claude_account = claude_meta.get("account", {}) if isinstance(claude_meta, dict) else {}
        codex_limits = codex_meta.get("rate_limits", {}) if isinstance(codex_meta, dict) else {}

        api = snap.claude_api_usage
        if api is not None:
            five_h_line = self._render_api_limit(api.five_hour_pct, api.five_hour_reset_secs)
            seven_d_line = self._render_api_limit(api.seven_day_pct, api.seven_day_reset_secs)
            api_tag_line = f"[grey40]api fetched {api.fetched_at}[/]"
        else:
            five_h_line = "[yellow]unknown[/] [grey40](no token)[/]"
            seven_d_line = "[yellow]unknown[/] [grey40](no token)[/]"
            api_tag_line = "[grey40]set TOKEN in .env for live limits[/]"

        claude_block = (
            "[bold magenta]CLAUDE[/]\n"
            f"plan: [white]{claude_account.get('organization_type', 'unknown')}[/]\n"
            f"5h     {five_h_line}\n"
            f"weekly {seven_d_line}\n"
            f"{api_tag_line}\n"
            f"tokens (last): [white]{claude_total if claude_total is not None else 'unknown'}[/]\n"
            f"source: [cyan]{claude_src}[/] | acc: [cyan]{claude_acc}[/]\n"
            f"activity: {render_sparkline(claude_vals, width=46)}\n"
            f"samples: {len(snap.claude_usage)}"
        )
        codex_block = (
            "[bold dodger_blue1]CODEX[/]\n"
            f"plan: [white]{codex_limits.get('plan_type', 'unknown')}[/]\n"
            f"5h     {self._render_limit_line(codex_limits, 'primary')}\n"
            f"weekly {self._render_limit_line(codex_limits, 'secondary')}\n"
            f"tokens (last): [white]{codex_total if codex_total is not None else 'unknown'}[/]\n"
            f"source: [cyan]{codex_src}[/] | acc: [cyan]{codex_acc}[/]\n"
            f"activity: {render_sparkline(codex_vals, width=46)}\n"
            f"samples: {len(snap.codex_usage)}"
        )
        self.query_one("#claude_panel", Static).update(claude_block)
        self.query_one("#codex_panel", Static).update(codex_block)

        system_block = self._render_system(snap.system_meta)
        self.query_one("#system", Static).update(system_block)

        events_text = ["[bold wheat1]LIVE ACTIVITY[/]"]
        for row in snap.recent_events[:14]:
            ts = str(row.get("timestamp", ""))[11:19]
            provider = str(row.get("provider", "?"))
            etype = str(row.get("event_type", "?"))
            msg = str(row.get("message") or row.get("title") or "")
            color = "dodger_blue1" if provider == "codex" else "magenta"
            events_text.append(
                f"[grey50]{ts}[/] [{color}]{provider:<6}[/] "
                f"[grey80]{etype:<18}[/] [grey60]{msg[:48]}[/]"
            )
        if len(events_text) == 1:
            events_text.append("[grey40]waiting for local Claude/Codex activity...[/]")
        self.query_one("#events", Static).update("\n".join(events_text))

    def _render_system(self, meta: dict[str, Any]) -> str:
        if not meta:
            return "[bold green]SYSTEM[/]\n[grey50]no data yet[/]"

        cpu = float(meta.get("cpu_percent", 0.0))
        ram = float(meta.get("ram_percent", 0.0))
        disk = float(meta.get("disk_percent", 0.0))
        cpu_temp = meta.get("cpu_temp_c")
        temp_source = str(meta.get("cpu_temp_source", "unknown"))
        gpu_temp = meta.get("gpu_temp_c")
        gpu_name = str(meta.get("gpu_name") or "GPU")
        needs_admin = bool(meta.get("needs_admin"))
        net_up = meta.get("net_sent_mb", 0.0)
        net_dn = meta.get("net_recv_mb", 0.0)
        top = meta.get("top_processes", [])
        metrics_source = str(meta.get("metrics_source", "unknown"))
        core_loads = meta.get("core_loads") or []

        # CPU temperature line
        if isinstance(cpu_temp, (int, float)):
            temp_color = "spring_green2" if cpu_temp < 70 else ("yellow1" if cpu_temp < 90 else "red1")
            cpu_temp_str = f"[{temp_color}]{cpu_temp:.1f}°C[/] [grey50]({temp_source})[/]"
        elif needs_admin:
            cpu_temp_str = "[yellow]unknown[/] [grey40](run as admin for CPU temp)[/]"
        else:
            cpu_temp_str = f"[grey50]unknown ({temp_source})[/]"

        # GPU temperature line
        if isinstance(gpu_temp, (int, float)):
            gpu_color = "spring_green2" if gpu_temp < 70 else ("yellow1" if gpu_temp < 90 else "red1")
            gpu_label = gpu_name.split()[-1] if gpu_name else "GPU"
            gpu_str = f"[{gpu_color}]{gpu_temp:.1f}°C[/] [grey50]({gpu_label})[/]"
        else:
            gpu_str = "[grey50]N/A[/]"

        lines = [
            f"[bold green]SYSTEM[/] [grey40]{metrics_source}[/]",
            f"cpu  {render_bar(cpu, 20)}",
        ]

        # Per-core grid (2-column btop style)
        if core_loads:
            core_grid = render_core_grid(core_loads, cols=2)
            if core_grid:
                lines.append(core_grid)

        lines += [
            f"ram  {render_bar(ram, 20)}",
            f"disk {render_bar(disk, 20)}",
            f"temp {cpu_temp_str}",
            f"gpu  {gpu_str}",
            f"net  [cyan]↑[/]{net_up:.1f}MB  [cyan]↓[/]{net_dn:.1f}MB",
            "processes:",
        ]
        for p in top[:5]:
            name = str(p.get("name", "?"))[:22]
            lines.append(
                f"  [grey70]{name:<22}[/] mem=[yellow]{p.get('mem_mb', 0):>7.1f}MB[/]"
            )
        return "\n".join(lines)

    def _render_api_limit(self, pct: float | None, reset_secs: int | None) -> str:
        """Render a Claude API usage limit bar with reset countdown."""
        if pct is None:
            return "[yellow]unknown[/]"
        reset_text = self._format_reset_seconds(reset_secs)
        return f"{render_bar(pct, 14)} [grey60]reset {reset_text}[/]"

    def _render_limit_line(self, rate_limits: dict[str, Any], key: str) -> str:
        if not isinstance(rate_limits, dict):
            return "[yellow]unknown[/]"
        window = rate_limits.get(key)
        if not isinstance(window, dict):
            return "[yellow]unknown[/]"
        used = window.get("used_percent")
        reset_seconds = window.get("reset_after_seconds")
        if used is None:
            return "[yellow]unknown[/]"
        reset_text = self._format_reset_seconds(reset_seconds)
        return f"{render_bar(float(used), 14)} [grey60]reset {reset_text}[/]"

    def _format_reset_seconds(self, value: Any) -> str:
        try:
            total = int(value)
        except (TypeError, ValueError):
            return "unknown"
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        if days > 0:
            return f"{days}d {hours}h"
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
