"""Plain-language summary bullets from the metrics."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from .metrics import DayMetrics
from .models import Block


def fmt_minutes(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def fmt_time(t: datetime) -> str:
    return t.strftime("%H:%M")


def fmt_span(start: datetime, end: datetime) -> str:
    return f"{fmt_time(start)}–{fmt_time(end)}"


def fmt_counts(counter: Counter[str], limit: int = 3) -> str:
    return ", ".join(f"{label} {n}" for label, n in counter.most_common(limit))


def describe_block(block: Block) -> str:
    """e.g. '52 min on Job search: Acme, 11 micro-interruptions (Telegram 8, Gmail 3)'."""
    text = f"{fmt_minutes(block.duration)} on {block.thread}"
    n = len(block.interruptions)
    if n == 0:
        return f"{text}, no micro-interruptions"
    sources = Counter(i.label for i in block.interruptions)
    plural = "s" if n != 1 else ""
    return f"{text}, {n} micro-interruption{plural} ({fmt_counts(sources)})"


def daily_insights(m: DayMetrics) -> list[str]:
    if not m.stints:
        return ["No active time was recorded for this day."]
    bullets: list[str] = []

    kinds = m.switch_kinds
    total = len(m.switches)
    if total:
        work_work = kinds.get("work_work", 0)
        to_drift = kinds.get("work_drift", 0)
        bullets.append(
            f"You switched tasks {m.switches_per_hour:.1f} times per active hour "
            f"({total} switches in {m.active_hours:.1f} h). {work_work} were work → work; "
            f"{to_drift} went to drift."
        )

    if block := m.longest_block:
        bullets.append(
            f"Longest focus block: {describe_block(block)}, {fmt_span(block.start, block.end)}."
        )

    if clean := m.clean_focus:
        block, stint = clean
        bullets.append(
            f"Longest stretch with zero interruptions: {fmt_minutes(stint.duration)} on "
            f"{block.thread}, {fmt_span(stint.start, stint.end)}."
        )

    if m.micro_count:
        top_label, top_n = m.micro_by_label.most_common(1)[0]
        bullets.append(
            f"{m.micro_count} micro-interruptions ({m.micro_per_hour:.1f}/hour, "
            f"{fmt_minutes(m.micro_seconds)} in total). {top_label} pulled you away {top_n} times."
        )

    if m.return_rate is not None:
        text = (
            f"When you left a task, you came back within "
            f"{m.settings.return_window_minutes:.0f} min {m.return_rate:.0%} of the time."
        )
        if m.abandoned:
            names = ", ".join(a.thread for a in m.abandoned[:3])
            more = f" and {len(m.abandoned) - 3} more" if len(m.abandoned) > 3 else ""
            text += f" Left and not resumed: {names}{more}."
        bullets.append(text)

    return bullets[:5]
