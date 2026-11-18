"""Render daily and weekly reports as single self-contained HTML files."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from importlib import resources

from jinja2 import Environment, PackageLoader, select_autoescape

from ..insights import daily_insights, describe_block, fmt_counts, fmt_minutes, fmt_span
from ..metrics import DayMetrics
from ..pipeline import DayResult
from . import charts

_env = Environment(
    loader=PackageLoader("focustracker", "report/templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _css() -> str:
    return (resources.files("focustracker") / "report" / "templates" / "report.css").read_text()


def _group_legend(groups: set[str]) -> list[tuple[str, str]]:
    return [(charts.GROUP_NAMES[g], charts.group_class(g)) for g in charts.GROUP_ORDER if g in groups]


def _switch_chart(m: DayMetrics):
    hours = [h for h in m.hourly if h.active_seconds > 0]
    per_hour = defaultdict(Counter)
    for s in m.switches:
        per_hour[s.at.hour][s.kind] += 1
    series = [
        (label, css, [per_hour[h.hour][kind] for h in hours]) for kind, label, css in charts.SWITCH_KINDS
    ]
    return charts.column_chart([f"{h.hour}" for h in hours], series)


def _micro_chart(m: DayMetrics):
    hours = [h for h in m.hourly if h.active_seconds > 0]
    return charts.column_chart(
        [f"{h.hour}" for h in hours], [("micro-interruptions", "k1", [h.micro for h in hours])]
    )


@dataclass
class BlockRow:
    thread: str
    css: str
    span: str
    minutes: str
    micro: int
    sources: str
    clean: str
    pct: float


def _block_rows(m: DayMetrics, limit: int = 8) -> list[BlockRow]:
    top = sorted(m.focus_blocks, key=lambda b: -b.duration)[:limit]
    longest = top[0].duration if top else 1
    return [
        BlockRow(
            thread=b.thread,
            css=charts.group_class(b.group),
            span=fmt_span(b.start, b.end),
            minutes=fmt_minutes(b.duration),
            micro=len(b.interruptions),
            sources=fmt_counts(Counter(i.label for i in b.interruptions)),
            clean=fmt_minutes(b.longest_clean_stint.duration),
            pct=100 * b.duration / longest,
        )
        for b in top
    ]


def daily_context(result: DayResult, sample: bool = False) -> dict:
    m = result.metrics
    unclassified = sum(e.event.duration for e in result.events if e.source == "unclassified")
    return {
        "css": _css(),
        "sample": sample,
        "day": m.day,
        "m": m,
        "insights": daily_insights(m),
        "rows": charts.timeline_rows(m),
        "group_legend": charts.legend(_group_legend({s.group for s in m.stints})),
        "switch_legend": charts.legend([(label, css) for _, label, css in charts.SWITCH_KINDS]),
        "switch_chart": _switch_chart(m),
        "micro_chart": _micro_chart(m),
        "blocks": _block_rows(m),
        "longest": describe_block(m.longest_block) if m.longest_block else None,
        "micro_sources": charts.bars(
            [(label, n, str(n), "k1") for label, n in m.micro_by_label.most_common(8)]
        ),
        "micro_threads": charts.bars(
            [(t, n, str(n), "k1") for t, n in m.micro_by_thread.most_common(6)]
        ),
        "pairs": m.top_pairs(8),
        "threads": charts.bars(
            [(t, sec, fmt_minutes(sec), charts.group_class(g)) for t, g, sec in m.thread_totals[:12]]
        ),
        "fmt_minutes": fmt_minutes,
        "fmt_span": fmt_span,
        "unclassified_share": unclassified / m.active_seconds if m.active_seconds else 0,
        "llm_error": result.llm_error,
    }


def render_daily(result: DayResult, sample: bool = False) -> str:
    return _env.get_template("daily.html.j2").render(**daily_context(result, sample))


@dataclass
class DayRow:
    day: date
    m: DayMetrics | None

    @property
    def label(self) -> str:
        return self.day.strftime("%a")


def weekly_insights(days: list[DayRow]) -> list[str]:
    active = [d for d in days if d.m and d.m.stints]
    if not active:
        return ["No active time was recorded this week."]
    bullets = []
    total_switches = sum(len(d.m.switches) for d in active)
    total_hours = sum(d.m.active_hours for d in active)
    calm = min(active, key=lambda d: d.m.switches_per_hour)
    busy = max(active, key=lambda d: d.m.switches_per_hour)
    bullets.append(
        f"You switched tasks {total_switches / total_hours:.1f} times per active hour this week "
        f"({total_switches} switches in {total_hours:.0f} h)."
    )
    if len(active) > 1:
        bullets.append(
            f"Calmest day: {calm.day:%A} ({calm.m.switches_per_hour:.1f}/h). "
            f"Most fragmented: {busy.day:%A} ({busy.m.switches_per_hour:.1f}/h)."
        )
    blocks = [(d, d.m.longest_block) for d in active if d.m.longest_block]
    if blocks:
        d, b = max(blocks, key=lambda x: x[1].duration)
        bullets.append(f"Longest focus block: {describe_block(b)}, {d.day:%A} {fmt_span(b.start, b.end)}.")
    micro = Counter()
    for d in active:
        micro.update(d.m.micro_by_label)
    if micro:
        n = sum(micro.values())
        label, top = micro.most_common(1)[0]
        bullets.append(
            f"{n} micro-interruptions ({n / total_hours:.1f}/hour). {label} accounted for {top / n:.0%} of them."
        )
    long_blocks = sum(len(d.m.long_blocks) for d in active)
    limit = active[0].m.settings.long_block_minutes
    bullets.append(f"{long_blocks} focus blocks of {limit:.0f}+ min across {len(active)} active days.")
    return bullets[:5]


def weekly_context(days: list[tuple[date, DayResult | None]], sample: bool = False) -> dict:
    rows = [DayRow(d, r.metrics if r else None) for d, r in days]
    labels = [r.label for r in rows]

    def per_day(fn) -> list[float]:
        return [fn(r.m) if r.m and r.m.stints else 0 for r in rows]

    switch_series = [
        (label, css, per_day(lambda m, k=kind: m.switch_kinds.get(k, 0) / m.active_hours))
        for kind, label, css in charts.SWITCH_KINDS
    ]
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        if r.m:
            for thread, group, sec in r.m.thread_totals:
                totals[(thread, group)] += sec
    top_threads = sorted(totals.items(), key=lambda x: -x[1])[:12]
    groups = {g for (_, g) in totals}
    return {
        "css": _css(),
        "sample": sample,
        "start": days[0][0],
        "end": days[-1][0],
        "rows": rows,
        "insights": weekly_insights(rows),
        "switch_legend": charts.legend([(label, css) for _, label, css in charts.SWITCH_KINDS]),
        "switch_chart": charts.column_chart(labels, switch_series, value_format="{:.0f}"),
        "micro_chart": charts.column_chart(
            labels, [("micro-interruptions per hour", "k1", per_day(lambda m: m.micro_per_hour))]
        ),
        "block_chart": charts.column_chart(
            labels, [("median focus block (min)", "k1", per_day(lambda m: m.median_block_minutes))]
        ),
        "clean_chart": charts.column_chart(
            labels,
            [("longest clean stretch (min)", "k1",
              per_day(lambda m: m.clean_focus[1].duration / 60 if m.clean_focus else 0))],
        ),
        "group_legend": charts.legend(_group_legend(groups)),
        "threads": charts.bars(
            [(t, sec, fmt_minutes(sec), charts.group_class(g)) for (t, g), sec in top_threads]
        ),
        "fmt_minutes": fmt_minutes,
    }


def render_weekly(days: list[tuple[date, DayResult | None]], sample: bool = False) -> str:
    return _env.get_template("weekly.html.j2").render(**weekly_context(days, sample))
