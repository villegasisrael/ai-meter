from __future__ import annotations

from datetime import datetime, timezone
from threading import Thread
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Footer, Static

from ai_meter.app import MonitorEngine
from ai_meter.models import RuntimeState
from ai_meter.tui.widgets import (
    cpu_gradient_color,
    latest_field,
    latest_total,
    render_bar,
    render_core_grid,
    render_cpu_history_graph,
    render_sparkline,
    render_temp_bar,
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

    #status_line { height: 1; padding: 0 1; color: #6e9ab0; background: #0d1117; }

    /* Top row: CPU history graph (left) + CPU details (right) */
    #top_row { height: auto; }
    #cpu_graph { width: 50%; height: auto; border: round #1e3a50; padding: 0 1; margin: 0 1 1 0; }
    #cpu_details { width: 50%; height: auto; border: round #1e3a50; padding: 0 1; margin: 0 0 1 0; }

    /* Bottom row: AI panels (left) + system+events (right) */
    #bottom_row { height: 1fr; }
    #ai_panels { width: 50%; height: 1fr; }
    #right_panel { width: 50%; height: 1fr; }

    /* Individual panels */
    .panel { border: round #2a3f52; padding: 0 1; margin: 0 1 1 0; }
    #claude_panel { height: 1fr; margin: 0 1 1 0; border: round #2a3f52; padding: 0 1; }
    #codex_panel  { height: 1fr; margin: 0 1 1 0; border: round #2a3f52; padding: 0 1; }
    #system       { height: auto; margin: 0 0 1 0; border: round #2a3f52; padding: 0 1; }
    #events       { height: 1fr; margin: 0 0 1 0; border: round #2a3f52; padding: 0 1; }
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
        yield Static("", id="status_line")
        with Horizontal(id="top_row"):
            yield Static("", id="cpu_graph")
            yield Static("", id="cpu_details")
        with Horizontal(id="bottom_row"):
            with Vertical(id="ai_panels"):
                yield Static("", id="claude_panel")
                yield Static("", id="codex_panel")
            with Vertical(id="right_panel"):
                yield Static("", id="system")
                yield Static("", id="events")
        yield Footer()

    def on_mount(self) -> None:
        self._start_timers()
        self._render_snapshot()
        self.set_timer(0.05, self._collect_light)
        self.set_timer(0.15, self._collect_heavy)
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

        # --- CPU graph (top-left) ---
        cpu_graph_widget = self.query_one("#cpu_graph", Static)
        panel_w = cpu_graph_widget.size.width
        graph_w = max(20, panel_w - 4) if panel_w > 4 else max(20, (self.size.width // 2) - 6)
        cpu = float(snap.system_meta.get("cpu_percent", 0.0)) if snap.system_meta else 0.0
        cpu_color = cpu_gradient_color(cpu)
        graph_header = (
            f"[bold cyan]CPU[/] [grey40]history ({len(snap.cpu_history)} samples)[/]  "
            f"[{cpu_color}]{cpu:.1f}%[/]"
        )
        graph = render_cpu_history_graph(snap.cpu_history, graph_w, height=8)
        cpu_graph_widget.update(f"{graph_header}\n{graph}")

        # --- CPU details (top-right) ---
        self.query_one("#cpu_details", Static).update(
            self._render_cpu_details(snap.system_meta)
        )

        # --- Claude panel (bottom-left top) ---
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
            err = self.engine.claude_api.last_error if self.engine.claude_api is not None else None
            if self.engine.claude_api is None:
                five_h_line = "[yellow]unknown[/] [grey40](no token)[/]"
                seven_d_line = "[yellow]unknown[/] [grey40](no token)[/]"
                api_tag_line = "[grey40]set TOKEN in .env for live limits[/]"
            elif err:
                five_h_line = "[red]unknown[/] [grey40](auth/fetch error)[/]"
                seven_d_line = "[red]unknown[/] [grey40](auth/fetch error)[/]"
                retry_s = self.engine.claude_api.retry_after_seconds
                if "429" in err and retry_s > 0:
                    api_tag_line = f"[red]HTTP 429: retry in {retry_s}s[/]"
                else:
                    api_tag_line = f"[red]{err}[/]"
            else:
                five_h_line = "[yellow]unknown[/] [grey40](loading...)[/]"
                seven_d_line = "[yellow]unknown[/] [grey40](loading...)[/]"
                api_tag_line = "[grey40]token loaded; waiting first API fetch[/]"

        self.query_one("#claude_panel", Static).update(
            "[bold magenta]CLAUDE[/]\n"
            f"plan: [white]{claude_account.get('organization_type', 'unknown')}[/]\n"
            f"5h     {five_h_line}\n"
            f"weekly {seven_d_line}\n"
            f"{api_tag_line}\n"
            f"tokens (last): [white]{claude_total if claude_total is not None else 'unknown'}[/]\n"
            f"source: [cyan]{claude_src}[/] | acc: [cyan]{claude_acc}[/]\n"
            f"activity: {render_sparkline(claude_vals, width=40)}\n"
            f"samples: {len(snap.claude_usage)}"
        )

        self.query_one("#codex_panel", Static).update(
            "[bold dodger_blue1]CODEX[/]\n"
            f"plan: [white]{codex_limits.get('plan_type', 'unknown')}[/]\n"
            f"5h     {self._render_limit_line(codex_limits, 'primary')}\n"
            f"weekly {self._render_limit_line(codex_limits, 'secondary')}\n"
            f"tokens (last): [white]{codex_total if codex_total is not None else 'unknown'}[/]\n"
            f"source: [cyan]{codex_src}[/] | acc: [cyan]{codex_acc}[/]\n"
            f"activity: {render_sparkline(codex_vals, width=40)}\n"
            f"samples: {len(snap.codex_usage)}"
        )

        # --- System stats (bottom-right top) ---
        self.query_one("#system", Static).update(
            self._render_system_stats(snap.system_meta)
        )

        # --- Live activity (bottom-right bottom) ---
        events_text = ["[bold wheat1]LIVE ACTIVITY[/]"]
        for row in snap.recent_events[:14]:
            ts = self._format_event_time(row.get("timestamp"))
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

    def _render_cpu_details(self, meta: dict[str, Any]) -> str:
        if not meta:
            return "[bold cyan]CPU[/]\n[grey50]no data yet[/]"

        cpu = float(meta.get("cpu_percent", 0.0))
        cpu_name = str(meta.get("cpu_name", "CPU"))
        cpu_freq_mhz = meta.get("cpu_freq_mhz")
        core_loads = meta.get("core_loads") or []
        metrics_source = str(meta.get("metrics_source", ""))

        cpu_color = cpu_gradient_color(cpu)
        freq_str = (
            f"[grey60]{cpu_freq_mhz / 1000:.1f} GHz[/]  " if cpu_freq_mhz else ""
        )

        lines = [
            f"[bold white]{cpu_name}[/]  {freq_str}[{cpu_color}]{cpu:.1f}%[/]  [grey40]{metrics_source}[/]",
            f"CPU  {render_bar(cpu, 22)}",
        ]
        if core_loads:
            lines.append(render_core_grid(core_loads, cols=2))
        return "\n".join(lines)

    def _render_system_stats(self, meta: dict[str, Any]) -> str:
        if not meta:
            return "[bold green]SYSTEM[/]\n[grey50]no data yet[/]"

        ram = float(meta.get("ram_percent", 0.0))
        disk = float(meta.get("disk_percent", 0.0))
        cpu_temp = meta.get("cpu_temp_c")
        temp_source = str(meta.get("cpu_temp_source", "unknown"))
        gpu_temp = meta.get("gpu_temp_c")
        gpu_name = str(meta.get("gpu_name") or "GPU")
        needs_admin = bool(meta.get("needs_admin"))
        net_up = float(meta.get("net_sent_mb", 0.0))
        net_dn = float(meta.get("net_recv_mb", 0.0))
        top = meta.get("top_processes", [])

        # CPU temperature bar (100°C = critical)
        if isinstance(cpu_temp, (int, float)):
            cpu_temp_str = render_temp_bar(cpu_temp, max_temp=100.0, width=16) + f" [grey50]({temp_source})[/]"
        elif needs_admin:
            cpu_temp_str = "[grey50]── needs admin ──[/]"
        else:
            cpu_temp_str = f"[grey50]unknown ({temp_source})[/]"

        # GPU temperature bar
        gpu_label = gpu_name.split()[-1] if gpu_name else "GPU"
        if isinstance(gpu_temp, (int, float)):
            gpu_temp_str = render_temp_bar(gpu_temp, max_temp=100.0, width=16) + f" [grey50]({gpu_label})[/]"
        else:
            gpu_temp_str = "[grey50]N/A[/]"

        # Network — pick unit automatically
        def _fmt_net(mb: float) -> str:
            return f"{mb / 1024:.1f}GB" if mb >= 1024 else f"{mb:.1f}MB"

        lines = [
            "[bold green]SYSTEM[/]",
            f"ram  {render_bar(ram, 20)}",
            f"disk {render_bar(disk, 20)}",
            f"temp {cpu_temp_str}",
            f"gpu  {gpu_temp_str}",
            f"net  [#00ddcc]↑[/]{_fmt_net(net_up)}  [#ff8800]↓[/]{_fmt_net(net_dn)}",
            "processes:",
        ]
        for p in top[:5]:
            name = str(p.get("name", "?"))[:22]
            mem = float(p.get("mem_mb", 0))
            mem_color = cpu_gradient_color(min(100, mem / 2))  # 200 MB ≈ warm
            lines.append(
                f"  [grey70]{name:<22}[/] mem=[{mem_color}]{mem:>7.1f}MB[/]"
            )
        return "\n".join(lines)

    def _format_event_time(self, raw: Any) -> str:
        text = str(raw or "").strip()
        if not text:
            return "--:--:--"
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc).astimezone()
            else:
                dt = dt.astimezone()
            return dt.strftime("%H:%M:%S")
        except Exception:
            return text[11:19] if len(text) >= 19 else text

    def _render_api_limit(self, pct: float | None, reset_secs: int | None) -> str:
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
