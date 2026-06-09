from __future__ import annotations

import re
import time
from datetime import datetime, timezone
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
    render_activity_graph,
    render_bar,
    render_core_grid,
    render_cpu_history_graph,
    render_temp_bar,
    usage_values,
)
from ai_meter.tui.workers import PeriodicWorker


class AiMeterTui(App[None]):
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("p", "pause", "Pause"),
        Binding("c", "toggle_claude", "Claude"),
        Binding("x", "toggle_codex", "Codex"),
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
    #bottom_row.no-ai #right_panel { width: 100%; }

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
        self.state = RuntimeState(paused=bool(self.engine.config.app.start_paused))
        self.current_view = "1"
        self.refresh_ms = max(100, int(self.engine.config.app.refresh_interval_ms))
        self._show_claude = self.engine.provider_enabled("claude")
        self._show_codex = self.engine.provider_enabled("codex")
        self._render_timer: Timer | None = None
        self._light_worker: PeriodicWorker | None = None
        self._heavy_worker: PeriodicWorker | None = None
        self._api_worker: PeriodicWorker | None = None
        self._last_section_render: dict[str, float] = {}

    def compose(self) -> ComposeResult:
        yield Static("", id="status_line")
        with Horizontal(id="top_row"):
            yield Static("", id="cpu_graph")
            yield Static("", id="cpu_details")
        ai_enabled = self._show_claude or self._show_codex
        with Horizontal(id="bottom_row", classes="" if ai_enabled else "no-ai"):
            if ai_enabled:
                with Vertical(id="ai_panels"):
                    if self._show_claude:
                        yield Static("", id="claude_panel")
                    if self._show_codex:
                        yield Static("", id="codex_panel")
            with Vertical(id="right_panel"):
                yield Static("", id="system")
                yield Static("", id="events")
        yield Footer()

    def on_mount(self) -> None:
        self._start_timers()
        self._start_workers()
        self._render_snapshot(force=True)

    def on_unmount(self) -> None:
        for worker in (self._light_worker, self._heavy_worker, self._api_worker):
            if worker is not None:
                worker.stop(timeout_s=0.5)

    def _start_timers(self) -> None:
        if self._render_timer is not None:
            self._render_timer.stop()
        self._render_timer = self.set_interval(self.refresh_ms / 1000.0, self._render_snapshot)

    def _start_workers(self) -> None:
        light_s = max(0.1, self.engine.config.app.collector_light_interval_ms / 1000.0)
        heavy_s = max(2.0, float(self.engine.config.app.collector_heavy_interval_s))
        self._light_worker = PeriodicWorker(
            "light",
            self.engine.run_light_collection,
            light_s,
            should_run=lambda: not self.state.paused,
        )
        self._heavy_worker = PeriodicWorker(
            "heavy",
            self.engine.run_heavy_collection,
            heavy_s,
            should_run=lambda: not self.state.paused,
        )
        self._light_worker.start(initial_delay_s=0.05)
        self._heavy_worker.start(initial_delay_s=0.15)
        if self.engine.claude_api is not None:
            self._api_worker = PeriodicWorker(
                "claude-api",
                self.engine.run_claude_api_collection,
                float(self.engine.claude_api.cache_seconds),
                should_run=lambda: not self.state.paused,
            )
            self._api_worker.start(initial_delay_s=0.5)

    def action_refresh_now(self) -> None:
        if self._light_worker is not None:
            self._light_worker.trigger(force=True)
        if self._heavy_worker is not None:
            self._heavy_worker.trigger(force=True)
        if self._api_worker is not None:
            self._api_worker.trigger(force=True)
        self._render_snapshot(force=True)

    def action_pause(self) -> None:
        self.state.paused = not self.state.paused
        self._render_snapshot(force=True)

    def action_help(self) -> None:
        self.notify("keys: q r p c x -/+ h 1-7")

    def action_toggle_claude(self) -> None:
        if not self.engine.provider_enabled("claude"):
            self.notify("Claude disabled in config")
            return
        self._show_claude = not self._show_claude
        self._sync_ai_visibility()
        state = "shown" if self._show_claude else "hidden"
        self.notify(f"Claude {state}")
        self._render_snapshot(force=True)

    def action_toggle_codex(self) -> None:
        if not self.engine.provider_enabled("codex"):
            self.notify("Codex disabled in config")
            return
        self._show_codex = not self._show_codex
        self._sync_ai_visibility()
        state = "shown" if self._show_codex else "hidden"
        self.notify(f"Codex {state}")
        self._render_snapshot(force=True)

    def action_faster(self) -> None:
        self.refresh_ms = max(100, self.refresh_ms - 100)
        self._start_timers()
        self._render_snapshot(force=True)

    def action_slower(self) -> None:
        self.refresh_ms = min(5000, self.refresh_ms + 100)
        self._start_timers()
        self._render_snapshot(force=True)

    def action_view(self, num: str) -> None:
        self.current_view = num
        self._render_snapshot(force=True)

    def _render_snapshot(self, force: bool = False) -> None:
        now = time.monotonic()
        snap = self.engine.snapshot()
        paused_tag = "[yellow]PAUSED[/] | " if self.state.paused else ""
        api_tag = (
            "[green]usage-api=on[/]"
            if self.engine.claude_api is not None
            else "[grey50]usage-api=off[/]"
        )
        providers = ",".join(self.engine.enabled_ai_providers) or "none"
        visible = ",".join(self._visible_ai_providers()) or "none"
        self.query_one("#status_line", Static).update(
            f"[bold cyan]view={self.current_view}[/] | {paused_tag}"
            f"providers=[white]{providers}[/] | visible=[white]{visible}[/] | "
            f"model-calls=[red]off[/] | {api_tag} | "
            f"refresh=[white]{self.refresh_ms}ms[/] | "
            f"{datetime.now().strftime('%H:%M:%S')} | [green]+[/]/[red]-[/]"
        )

        if self._section_due(
            "cpu", self.engine.config.ui.cpu_render_interval_ms, now, force
        ):
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

            cpu_details_widget = self.query_one("#cpu_details", Static)
            cpu_details_widget.update(
                self._render_cpu_details(
                    snap.system_meta,
                    self._panel_content_width(cpu_details_widget),
                )
            )

        render_ai = self._section_due(
            "ai", self.engine.config.ui.ai_render_interval_ms, now, force
        )
        if render_ai and self._show_claude:
            claude_widget = self.query_one("#claude_panel", Static)
            limit_w = self._limit_bar_width(claude_widget)
            activity_w = self._activity_width(claude_widget)
            activity_h = self._activity_graph_height(claude_widget, reserved_lines=10)
            claude_total = latest_total(snap.claude_usage)
            claude_vals = usage_values(snap.claude_usage)
            claude_graph = render_activity_graph(
                claude_vals, width=activity_w, height=activity_h
            )
            claude_src = latest_field(snap.claude_usage, "source")
            claude_acc = latest_field(snap.claude_usage, "accuracy")
            claude_meta = snap.provider_meta.get("claude", {})
            claude_account = (
                claude_meta.get("account", {}) if isinstance(claude_meta, dict) else {}
            )

            api = snap.claude_api_usage
            collector = self.engine.claude_api
            is_stale = bool(collector is not None and collector.is_stale)
            if api is not None:
                api_limit_lines = self._render_api_limits(api, limit_w)
                if is_stale:
                    age_txt = self._format_age(getattr(api, "age_seconds", None))
                    if collector is not None and collector.token_expired:
                        api_tag_line = (
                            f"[yellow]last known {age_txt} ago — token expired, relogin in Claude Code[/]"
                        )
                    else:
                        api_tag_line = f"[yellow]stale: last known {age_txt} ago[/]"
                else:
                    api_tag_line = f"[grey40]api fetched {api.fetched_at}[/]"
            else:
                err = (
                    self.engine.claude_api.last_error
                    if self.engine.claude_api is not None
                    else None
                )
                if self.engine.claude_api is None:
                    api_limit_lines = (
                        "5h     [yellow]unknown[/] [grey40](usage-api off)[/]\n"
                        "weekly [yellow]unknown[/] [grey40](usage-api off)[/]"
                    )
                    api_tag_line = "[grey40]local observed usage only[/]"
                elif self.engine.claude_api.token_expired:
                    api_limit_lines = (
                        "5h     [yellow]unknown[/] [grey40](token expired)[/]\n"
                        "weekly [yellow]unknown[/] [grey40](token expired)[/]"
                    )
                    api_tag_line = "[yellow]OAuth token expired — relogin in Claude Code[/]"
                elif err:
                    api_limit_lines = (
                        "5h     [red]unknown[/] [grey40](auth/fetch error)[/]\n"
                        "weekly [red]unknown[/] [grey40](auth/fetch error)[/]"
                    )
                    retry_s = self.engine.claude_api.retry_after_seconds
                    if "429" in err and retry_s > 0:
                        api_tag_line = f"[red]HTTP 429: retry in {retry_s}s[/]"
                    else:
                        api_tag_line = f"[red]{err}[/]"
                else:
                    api_limit_lines = (
                        "5h     [yellow]unknown[/] [grey40](loading...)[/]\n"
                        "weekly [yellow]unknown[/] [grey40](loading...)[/]"
                    )
                    api_tag_line = "[grey40]token loaded; waiting first API fetch[/]"

            claude_widget.update(
                "[bold magenta]CLAUDE[/]\n"
                f"plan: [white]{claude_account.get('organization_type', 'unknown')}[/]\n"
                f"{api_limit_lines}\n"
                f"{api_tag_line}\n"
                f"tokens (last): [white]{claude_total if claude_total is not None else 'unknown'}[/]\n"
                f"source: [cyan]{claude_src}[/] | acc: [cyan]{claude_acc}[/]\n"
                f"activity [grey40]history ({len(claude_vals)} points)[/]\n"
                f"{claude_graph}\n"
                f"samples: {len(snap.claude_usage)}"
            )

        if render_ai and self._show_codex:
            codex_widget = self.query_one("#codex_panel", Static)
            limit_w = self._limit_bar_width(codex_widget)
            activity_w = self._activity_width(codex_widget)
            activity_h = self._activity_graph_height(codex_widget, reserved_lines=9)
            codex_total = latest_total(snap.codex_usage)
            codex_vals = usage_values(snap.codex_usage)
            codex_graph = render_activity_graph(
                codex_vals, width=activity_w, height=activity_h
            )
            codex_src = latest_field(snap.codex_usage, "source")
            codex_acc = latest_field(snap.codex_usage, "accuracy")
            codex_meta = snap.provider_meta.get("codex", {})
            codex_limits = (
                codex_meta.get("rate_limits", {}) if isinstance(codex_meta, dict) else {}
            )
            codex_widget.update(
                "[bold dodger_blue1]CODEX[/]\n"
                f"plan: [white]{codex_limits.get('plan_type', 'unknown')}[/]\n"
                f"5h     {self._render_limit_line(codex_limits, 'primary', limit_w)}\n"
                f"weekly {self._render_limit_line(codex_limits, 'secondary', limit_w)}\n"
                f"tokens (last): [white]{codex_total if codex_total is not None else 'unknown'}[/]\n"
                f"source: [cyan]{codex_src}[/] | acc: [cyan]{codex_acc}[/]\n"
                f"activity [grey40]history ({len(codex_vals)} points)[/]\n"
                f"{codex_graph}\n"
                f"samples: {len(snap.codex_usage)}"
            )

        if self._section_due(
            "system", self.engine.config.ui.system_render_interval_ms, now, force
        ):
            system_widget = self.query_one("#system", Static)
            system_widget.update(
                self._render_system_stats(
                    snap.system_meta,
                    self._panel_content_width(system_widget),
                )
            )

        if self._section_due(
            "events", self.engine.config.ui.events_render_interval_ms, now, force
        ):
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

    def _section_due(
        self, name: str, interval_ms: int | float, now: float, force: bool
    ) -> bool:
        interval_s = max(0.05, float(interval_ms) / 1000.0)
        last = self._last_section_render.get(name)
        if force or last is None or (now - last) >= interval_s:
            self._last_section_render[name] = now
            return True
        return False

    def _render_cpu_details(self, meta: dict[str, Any], width: int = 44) -> str:
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
        bar_width = max(16, min(160, width - 12))
        core_cols = 5 if width >= 120 else 4 if width >= 92 else 3 if width >= 68 else 2
        mini_width = max(5, min(14, (width // core_cols) - 11))

        lines = [
            f"[bold white]{cpu_name}[/]  {freq_str}[{cpu_color}]{cpu:.1f}%[/]  [grey40]{metrics_source}[/]",
            f"CPU  {render_bar(cpu, bar_width)}",
        ]
        if core_loads:
            lines.append(render_core_grid(core_loads, cols=core_cols, mini_width=mini_width))
        return "\n".join(lines)

    def _render_system_stats(self, meta: dict[str, Any], width: int = 44) -> str:
        if not meta:
            return "[bold green]SYSTEM[/]\n[grey50]no data yet[/]"

        ram = float(meta.get("ram_percent", 0.0))
        disk = float(meta.get("disk_percent", 0.0))
        cpu_temp = meta.get("cpu_temp_c")
        temp_source = str(meta.get("cpu_temp_source", "unknown"))
        gpu_temp = meta.get("gpu_temp_c")
        gpu_name = str(meta.get("gpu_name") or "GPU")
        gpu_source = str(meta.get("gpu_temp_source") or "unknown")
        needs_admin = bool(meta.get("needs_admin"))
        net_up = float(meta.get("net_sent_mb", 0.0))
        net_dn = float(meta.get("net_recv_mb", 0.0))
        top = meta.get("top_processes", [])
        bar_width = max(16, min(160, width - 18))
        temp_bar_width = max(14, min(120, width - 38))

        # CPU temperature bar (100°C = critical)
        if isinstance(cpu_temp, (int, float)):
            cpu_temp_str = render_temp_bar(cpu_temp, max_temp=100.0, width=temp_bar_width) + f" [grey50]({temp_source})[/]"
        elif needs_admin:
            cpu_temp_str = "[grey50]driver blocked[/]"
        else:
            cpu_temp_str = f"[grey50]unknown ({temp_source})[/]"

        # GPU temperature bar
        gpu_label = gpu_name.split()[-1] if gpu_name else "GPU"
        if isinstance(gpu_temp, (int, float)):
            source_label = gpu_source if gpu_source != "unknown" else gpu_label
            gpu_temp_str = render_temp_bar(gpu_temp, max_temp=100.0, width=temp_bar_width) + f" [grey50]({source_label})[/]"
        else:
            gpu_temp_str = f"[grey50]unknown ({gpu_source})[/]"

        # Network — pick unit automatically
        def _fmt_net(mb: float) -> str:
            return f"{mb / 1024:.1f}GB" if mb >= 1024 else f"{mb:.1f}MB"

        lines = [
            "[bold green]SYSTEM[/]",
            f"ram  {render_bar(ram, bar_width)}",
            f"disk {render_bar(disk, bar_width)}",
            f"temp {cpu_temp_str}",
            f"gpu  {gpu_temp_str}",
            f"net  [#00ddcc]↑[/]{_fmt_net(net_up)}  [#ff8800]↓[/]{_fmt_net(net_dn)}",
            "processes:",
        ]
        proc_cols = 3 if width >= 112 else 2 if width >= 74 else 1
        max_processes = 9 if proc_cols == 3 else 8 if proc_cols == 2 else 5
        proc_items = [
            self._format_process_item(p, compact=proc_cols > 1)
            for p in top[:max_processes]
        ]
        if proc_cols > 1:
            col_width = max(28, width // proc_cols - 2)
            for idx in range(0, len(proc_items), proc_cols):
                row = proc_items[idx : idx + proc_cols]
                lines.append(
                    "  " + "  ".join(self._pad_markup(item, col_width) for item in row).rstrip()
                )
        else:
            lines.extend(f"  {item}" for item in proc_items[:5])
        return "\n".join(lines)

    def _format_process_item(self, proc: dict[str, Any], compact: bool = False) -> str:
        name_width = 18 if compact else 22
        name = str(proc.get("name", "?"))[:name_width]
        mem = float(proc.get("mem_mb", 0))
        mem_color = cpu_gradient_color(min(100, mem / 2))
        return f"[grey70]{name:<{name_width}}[/] mem=[{mem_color}]{mem:>7.1f}MB[/]"

    def _pad_markup(self, value: str, width: int) -> str:
        visible = len(re.sub(r"\[[^\]]+\]", "", value))
        return value + (" " * max(0, width - visible))

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

    def _panel_content_width(self, widget: Static, fallback: int = 44) -> int:
        width = int(widget.size.width)
        if width <= 8:
            width = fallback
        return max(16, width - 4)

    def _limit_bar_width(self, widget: Static) -> int:
        content_width = self._panel_content_width(widget)
        return max(14, min(96, content_width - 30))

    def _activity_width(self, widget: Static) -> int:
        content_width = self._panel_content_width(widget)
        return max(20, min(160, content_width))

    def _activity_graph_height(self, widget: Static, reserved_lines: int) -> int:
        height = int(widget.size.height)
        if height <= 0:
            return 6
        return max(2, min(8, height - reserved_lines))

    def _visible_ai_providers(self) -> list[str]:
        visible: list[str] = []
        if self.engine.provider_enabled("claude") and self._show_claude:
            visible.append("claude")
        if self.engine.provider_enabled("codex") and self._show_codex:
            visible.append("codex")
        return visible

    def _sync_ai_visibility(self) -> None:
        visible = bool(self._visible_ai_providers())
        for widget in self.query("#claude_panel"):
            widget.display = self._show_claude
        for widget in self.query("#codex_panel"):
            widget.display = self._show_codex
        for widget in self.query("#ai_panels"):
            widget.display = visible
        bottom_rows = list(self.query("#bottom_row"))
        if not bottom_rows:
            return
        bottom_row = bottom_rows[0]
        if visible:
            bottom_row.remove_class("no-ai")
        else:
            bottom_row.add_class("no-ai")

    def _render_api_limit(
        self, pct: float | None, reset_secs: int | None, width: int
    ) -> str:
        if pct is None:
            return "[yellow]unknown[/]"
        reset_text = self._format_reset_seconds(reset_secs)
        return f"{render_bar(pct, width)} [grey60]reset {reset_text}[/]"

    def _render_api_limits(self, api: Any, width: int) -> str:
        limits = getattr(api, "limits", None)
        if not limits:
            return (
                f"5h     {self._render_api_limit(api.five_hour_pct, api.five_hour_reset_secs, width)}\n"
                f"weekly {self._render_api_limit(api.seven_day_pct, api.seven_day_reset_secs, width)}"
            )
        rows: list[str] = []
        for raw in limits[:6]:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "limit")[:7]
            pct = raw.get("pct")
            reset_secs = raw.get("reset_secs")
            detail = raw.get("detail")
            line = self._render_api_limit(float(pct), reset_secs, width) if pct is not None else "[yellow]unknown[/]"
            if detail:
                line = f"{line} [grey50]{detail}[/]"
            rows.append(f"{name:<7}{line}")
        return "\n".join(rows) if rows else "[yellow]unknown[/]"

    def _render_limit_line(
        self, rate_limits: dict[str, Any], key: str, width: int
    ) -> str:
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
        return f"{render_bar(float(used), width)} [grey60]reset {reset_text}[/]"

    def _format_age(self, value: Any) -> str:
        try:
            total = int(value)
        except (TypeError, ValueError):
            return "?"
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        if days > 0:
            return f"{days}d {hours}h"
        if hours > 0:
            return f"{hours}h {minutes}m"
        if minutes > 0:
            return f"{minutes}m"
        return f"{total}s"

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
