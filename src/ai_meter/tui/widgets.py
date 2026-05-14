from __future__ import annotations

from typing import Iterable


def render_bar(value: float | None, width: int = 24) -> str:
    if value is None:
        return "[grey50]unknown[/]"
    value = max(0.0, min(100.0, float(value)))
    filled = int(round((value / 100.0) * width))
    color = "spring_green2" if value < 50 else ("yellow1" if value < 80 else "red1")
    return f"[{color}]" + ("█" * filled) + "[/][grey30]" + ("░" * (width - filled)) + f"[/] {value:5.1f}%"


def render_mini_bar(value: float, width: int = 7) -> str:
    value = max(0.0, min(100.0, float(value)))
    filled = int(round((value / 100.0) * width))
    color = "spring_green2" if value < 50 else ("yellow1" if value < 80 else "red1")
    return f"[{color}]" + ("█" * filled) + "[/][grey23]" + ("░" * (width - filled)) + "[/]"


def render_sparkline(values: Iterable[int | float], width: int = 48) -> str:
    glyphs = "▁▂▃▄▅▆▇█"
    data = [float(v) for v in values if v is not None]
    if not data:
        return "[grey30]" + ("-" * min(width, 16)) + "[/]"
    if len(data) > width:
        data = data[-width:]
    low = min(data)
    high = max(data)
    if high == low:
        return glyphs[0] * len(data)
    out = []
    for val in data:
        idx = int((val - low) / (high - low) * (len(glyphs) - 1))
        out.append(glyphs[idx])
    return "[cyan1]" + "".join(out) + "[/]"


def render_core_grid(core_loads: list[dict], cols: int = 2) -> str:
    """Render per-core CPU loads in a compact grid. Returns multi-line string."""
    if not core_loads:
        return ""
    lines = []
    for i in range(0, len(core_loads), cols):
        row_items = core_loads[i : i + cols]
        parts = []
        for core in row_items:
            raw = core.get("name", "")
            num = raw.replace("CPU Core #", "")
            label = f"C{num}".rjust(3)
            load = float(core.get("load_percent", 0.0))
            bar = render_mini_bar(load, 7)
            load_color = "spring_green2" if load < 50 else ("yellow1" if load < 80 else "red1")
            parts.append(f"[grey50]{label}[/] {bar}[{load_color}]{load:4.0f}%[/]")
        lines.append("  " + "  ".join(parts))
    return "\n".join(lines)


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
