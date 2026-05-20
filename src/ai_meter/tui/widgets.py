from __future__ import annotations

import math
from typing import Iterable


# ---------------------------------------------------------------------------
# Gradient: teal(0%) → yellow(50%) → red(100%) — 101 pre-computed hex steps
# ---------------------------------------------------------------------------
def _build_cpu_gradient() -> list[str]:
    out: list[str] = []
    for i in range(101):
        if i <= 50:
            t = i / 50.0
            r = int(round(t * 255))
            g = int(round(221 + t * 34))    # 221→255
            b = int(round(204 * (1.0 - t))) # 204→0
        else:
            t = (i - 50) / 50.0
            r = 255
            g = int(round(255 - t * 204))   # 255→51
            b = int(round(t * 51))           # 0→51
        out.append(f"#{r:02x}{g:02x}{b:02x}")
    return out

_CPU_GRADIENT = _build_cpu_gradient()


def cpu_gradient_color(pct: float) -> str:
    return _CPU_GRADIENT[max(0, min(100, round(float(pct))))]


# ---------------------------------------------------------------------------
# Braille constants (btop4win algorithm)
# ---------------------------------------------------------------------------
_LEFT_BRAILLE  = [0x00, 0x40, 0x44, 0x46, 0x47]  # dots 7,3,2,1 bottom→top
_RIGHT_BRAILLE = [0x00, 0x80, 0xA0, 0xB0, 0xB8]  # dots 8,6,5,4 bottom→top


def _val_to_dots(val: float, cur_low: float, cur_high: float) -> int:
    if val >= cur_high:
        return 4
    if val <= cur_low:
        return 0
    return round((val - cur_low) / (cur_high - cur_low) * 4)


def _render_percent_braille_graph(
    values: Iterable[int | float], width: int, height: int
) -> str:
    width = max(1, int(width))
    height = max(1, int(height))
    """
    Braille chars (2 time-steps × 4 dot-levels each).
    Color per char = gradient[max(left, right)] — not per row.
    """
    data_needed = width * 2
    data = [max(0.0, min(100.0, float(v))) for v in values]
    if len(data) < data_needed:
        data = [0.0] * (data_needed - len(data)) + data
    else:
        data = data[-data_needed:]

    lines: list[str] = []
    for row_idx in range(height):
        cur_high = (height - row_idx) / height * 100.0
        cur_low  = (height - row_idx - 1) / height * 100.0

        parts: list[str] = []
        run_color: str | None = None
        run_chars = ""

        for i in range(0, data_needed, 2):
            l_val = data[i]
            r_val = data[i + 1]
            braille = chr(
                0x2800
                + _LEFT_BRAILLE[_val_to_dots(l_val, cur_low, cur_high)]
                + _RIGHT_BRAILLE[_val_to_dots(r_val, cur_low, cur_high)]
            )
            peak = max(l_val, r_val)
            color = "#131c27" if peak < 0.5 else _CPU_GRADIENT[round(peak)]

            if color == run_color:
                run_chars += braille
            else:
                if run_color is not None:
                    parts.append(f"[{run_color}]{run_chars}[/]")
                run_color = color
                run_chars = braille

        if run_color is not None:
            parts.append(f"[{run_color}]{run_chars}[/]")
        lines.append("".join(parts))

    return "\n".join(lines)


def render_cpu_history_graph(history: list[float], width: int, height: int = 8) -> str:
    """btop4win-faithful CPU history graph."""
    return _render_percent_braille_graph(history, width, height)


# ---------------------------------------------------------------------------
# Gradient bar (used for CPU%, RAM, disk — gradient left→right by position)
# ---------------------------------------------------------------------------

def _filled_bar(value_pct: float, width: int) -> str:
    """Internal: returns just the filled+empty braille cells, no label."""
    value_pct = max(0.0, min(100.0, value_pct))
    total_units = width * 2
    filled_units = int(round((value_pct / 100.0) * total_units))

    parts: list[str] = []
    run_color: str | None = None
    run_chars = ""

    for i in range(width):
        left_unit = i * 2
        fill_in_cell = max(0, min(2, filled_units - left_unit))
        if fill_in_cell == 0:
            char = chr(0x2800)
            color = "#131c27"
        elif fill_in_cell == 1:
            char = chr(0x2800 + _LEFT_BRAILLE[4])
            color = _CPU_GRADIENT[round((left_unit + 0.5) / total_units * 100.0)]
        else:
            char = chr(0x2800 + _LEFT_BRAILLE[4] + _RIGHT_BRAILLE[4])
            color = _CPU_GRADIENT[round((left_unit + 1.0) / total_units * 100.0)]

        if color == run_color:
            run_chars += char
        else:
            if run_color is not None:
                parts.append(f"[{run_color}]{run_chars}[/]")
            run_color = color
            run_chars = char

    if run_color:
        parts.append(f"[{run_color}]{run_chars}[/]")

    return "".join(parts)


def render_bar(value: float | None, width: int = 24) -> str:
    if value is None:
        return "[grey50]unknown[/]"
    value = max(0.0, min(100.0, float(value)))
    return _filled_bar(value, width) + f" {value:5.1f}%"


def render_mini_bar(value: float, width: int = 7) -> str:
    value = max(0.0, min(100.0, float(value)))
    return _filled_bar(value, width)


def render_temp_bar(temp_c: float | None, max_temp: float = 100.0, width: int = 18) -> str:
    """Temperature as a gradient bar. max_temp°C = 100% (critical red)."""
    if temp_c is None or not isinstance(temp_c, (int, float)):
        return "[grey50]N/A[/]"
    pct = max(0.0, min(100.0, float(temp_c) / max_temp * 100.0))
    color = _CPU_GRADIENT[round(pct)]
    return _filled_bar(pct, width) + f" [{color}]{temp_c:.1f}°C[/]"


# ---------------------------------------------------------------------------
# Activity graphs (Claude / Codex token activity)
# ---------------------------------------------------------------------------

def render_activity_graph(
    values: Iterable[int | float], width: int = 48, height: int = 6
) -> str:
    data: list[float] = []
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            data.append(max(0.0, number))

    if not data:
        return _render_percent_braille_graph([], width, height)

    high = max(data)
    if high <= 0:
        normalized = [0.0 for _ in data]
    else:
        normalized = [(value / high) * 100.0 for value in data]
    return _render_percent_braille_graph(normalized, width, height)


def render_sparkline(values: Iterable[int | float], width: int = 48) -> str:
    glyphs = "▁▂▃▄▅▆▇█"
    data = [float(v) for v in values if v is not None]
    if not data:
        return "[grey30]" + ("-" * min(width, 16)) + "[/]"
    if len(data) > width:
        data = data[-width:]
    low, high = min(data), max(data)
    if high == low:
        return "[#00ddcc]" + glyphs[0] * len(data) + "[/]"

    pairs: list[tuple[str, str]] = []
    for val in data:
        norm = (val - low) / (high - low)
        idx = int(norm * (len(glyphs) - 1))
        pairs.append((_CPU_GRADIENT[round(norm * 100)], glyphs[idx]))

    out: list[str] = []
    cur_color, cur_chars = pairs[0]
    for color, char in pairs[1:]:
        if color == cur_color:
            cur_chars += char
        else:
            out.append(f"[{cur_color}]{cur_chars}[/]")
            cur_color, cur_chars = color, char
    out.append(f"[{cur_color}]{cur_chars}[/]")
    return "".join(out)


# ---------------------------------------------------------------------------
# Core grid
# ---------------------------------------------------------------------------

def render_core_grid(core_loads: list[dict], cols: int = 2, mini_width: int = 7) -> str:
    if not core_loads:
        return ""
    mini_width = max(4, min(14, int(mini_width)))
    lines = []
    for i in range(0, len(core_loads), cols):
        row_items = core_loads[i : i + cols]
        parts = []
        for core in row_items:
            raw = core.get("name", "")
            num = raw.replace("CPU Core #", "")
            label = f"C{num}".rjust(3)
            load = float(core.get("load_percent", 0.0))
            bar = render_mini_bar(load, mini_width)
            load_color = cpu_gradient_color(load)
            parts.append(f"[grey50]{label}[/] {bar}[{load_color}]{load:4.0f}%[/]")
        lines.append("  " + "  ".join(parts))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Usage helpers
# ---------------------------------------------------------------------------

def latest_total(usage_rows: list[dict]) -> int | None:
    for row in usage_rows:
        total = row.get("total_tokens")
        if total is not None:
            try:
                return int(total)
            except (TypeError, ValueError):
                continue
    return None


def latest_field(usage_rows: list[dict], field: str) -> str:
    for row in usage_rows:
        value = row.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return "unknown"


def latest_percent(usage_rows: list[dict]) -> float | None:
    for row in usage_rows:
        value = row.get("usage_percent")
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def usage_values(usage_rows: list[dict]) -> list[float]:
    values: list[float] = []
    for row in reversed(usage_rows):
        total = row.get("total_tokens")
        if total is None:
            continue
        try:
            values.append(float(total))
        except (TypeError, ValueError):
            continue
    return values
