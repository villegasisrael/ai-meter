"""Compact account counters for the existing terminal panel."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ai_meter.collectors.codex_account_usage import AccountUsageState

_MONTHS = ("ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC")


def format_tokens(value: int | None) -> str:
    if value is None:
        return "unknown"
    for scale, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= scale:
            return f"{value / scale:.2f} {suffix}"
    return str(value)


def render_account_usage(
    state: AccountUsageState | None, width: int, today: date | None = None
) -> list[str]:
    today = today or datetime.now().astimezone().date()
    yesterday = today - timedelta(days=1)
    usage = state.usage if state else None
    status = state.status if state else "disabled"
    label, color = {
        "connected": ("conectada", "green"),
        "loading": ("consultando", "grey50"),
        "stale": ("desactualizada", "yellow"),
        "unavailable": ("unknown", "yellow"),
        "disabled": ("desactivada", "grey50"),
    }[status]
    gap = max(1, width - len("TOKENS DE CUENTA") - len(label))
    lines = [f"[bold cyan]TOKENS DE CUENTA[/]{' ' * gap}[{color}]{label}[/]"]
    labels = [
        f"HOY {today.day:02d} {_MONTHS[today.month - 1]}",
        f"AYER {yesterday.day:02d} {_MONTHS[yesterday.month - 1]}",
        "ACUMULADO",
    ]
    values = [
        format_tokens(usage.daily_tokens.get(today) if usage else None),
        format_tokens(usage.daily_tokens.get(yesterday) if usage else None),
        format_tokens(usage.lifetime_tokens if usage else None),
    ]
    if width >= 42:
        column = max(12, (width - 4) // 3)
        lines.append("[grey60]" + "  ".join(f"{s:<{column}}" for s in labels).rstrip() + "[/]")
        lines.append("[bold white]" + "  ".join(f"{s:<{column}}" for s in values).rstrip() + "[/]")
    else:
        lines.extend(f"[grey60]{label}[/] [bold white]{value}[/]" for label, value in zip(labels, values))
    if state and state.error:
        detail = state.error
    elif usage:
        age = max(0, int((datetime.now(timezone.utc) - usage.fetched_at).total_seconds()))
        detail = f"{len(usage.daily_tokens)} días · cada {state.interval_seconds // 60} min · hace {age // 60} min"
    else:
        detail = "sin consulta de cuenta" if state is None else "esperando datos de la cuenta"
    lines.append(f"[grey50]{detail[:width]}[/]")
    return lines
