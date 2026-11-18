"""Chart data and inline SVG for the HTML reports (no JavaScript, no chart library).

Bars and the day timeline are plain HTML (they reflow on phones); only the
column charts are SVG. Colours come from CSS custom properties defined in the
template, so light and dark mode are handled in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from html import escape

from markupsafe import Markup

from ..metrics import DayMetrics
from ..insights import fmt_minutes, fmt_span

# Fixed order = colour slot order (see the template's --g-* tokens).
GROUP_ORDER = ["job_search", "drift", "learning", "comms", "side_project", "admin", "unknown"]
GROUP_NAMES = {
    "job_search": "Job search",
    "drift": "Drift",
    "learning": "Learning",
    "comms": "Comms",
    "side_project": "Side project",
    "admin": "Admin",
    "unknown": "Unknown",
}
SWITCH_KINDS = [
    ("work_work", "work → work", "k1"),
    ("work_drift", "work → drift", "k2"),
    ("drift_work", "drift → work", "k3"),
]


def group_class(group: str) -> str:
    return f"g-{group}" if group in GROUP_NAMES else "g-unknown"


@dataclass
class Segment:
    left: float  # % of the hour row
    width: float
    css: str
    tip: str


@dataclass
class HourRow:
    hour: int
    segments: list[Segment]
    ticks: list[tuple[float, str]]  # (% position, tooltip) of micro-interruptions


def timeline_rows(m: DayMetrics) -> list[HourRow]:
    """One row per clock hour, with stints drawn as coloured segments."""
    if not m.stints:
        return []
    first, last = m.stints[0].start.hour, m.stints[-1].end.hour
    rows = {h: HourRow(h, [], []) for h in range(first, last + 1)}

    def pct(t) -> float:
        return (t.minute * 60 + t.second + t.microsecond / 1e6) / 36

    for s in m.stints:
        tip = f"{fmt_span(s.start, s.end)} · {s.thread} ({s.label})"
        cursor = s.start
        while cursor < s.end:
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            end = min(s.end, next_hour)
            if cursor.hour in rows:
                width = (end - cursor).total_seconds() / 36
                rows[cursor.hour].segments.append(
                    Segment(pct(cursor), width, group_class(s.group), tip)
                )
            cursor = end
    for i in m.interruptions:
        if i.start.hour in rows:
            rows[i.start.hour].ticks.append(
                (pct(i.start), f"{i.start:%H:%M} · {i.label}, {i.duration:.0f}s")
            )
    return list(rows.values())


@dataclass
class Bar:
    label: str
    value: float
    pct: float
    text: str
    css: str = "k1"


def bars(items: list[tuple[str, float, str, str]]) -> list[Bar]:
    """(label, value, value text, css class) -> bars scaled to the largest value."""
    top = max((v for _, v, _, _ in items), default=0) or 1
    return [Bar(label, v, 100 * v / top, text, css) for label, v, text, css in items]


def column_chart(
    categories: list[str],
    series: list[tuple[str, str, list[float]]],
    height: int = 150,
    value_format: str = "{:.0f}",
) -> Markup:
    """Stacked column chart as SVG. series = [(name, css class, values per category)]."""
    width, pad_l, pad_b, pad_t = 420, 26, 20, 16
    n = max(len(categories), 1)
    plot_w, plot_h = width - pad_l, height - pad_b - pad_t
    totals = [sum(s[2][i] for s in series) for i in range(len(categories))]
    top = max(totals, default=0) or 1
    step = _nice_step(top)
    y_max = step * -(-top // step)
    slot = plot_w / n
    bar_w = max(min(slot * 0.62, 28), 3)

    def y(v: float) -> float:
        return pad_t + plot_h - plot_h * v / y_max

    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" class="cols" '
        f'aria-label="{escape(", ".join(s[0] for s in series))} by category">'
    ]
    tick = 0.0
    while tick <= y_max + 1e-9:
        parts.append(
            f'<line class="grid" x1="{pad_l}" x2="{width}" y1="{y(tick):.1f}" y2="{y(tick):.1f}"/>'
            f'<text class="axis" x="{pad_l - 5}" y="{y(tick) + 3.5:.1f}" text-anchor="end">'
            f"{value_format.format(tick)}</text>"
        )
        tick += step
    for i, cat in enumerate(categories):
        x = pad_l + slot * i + (slot - bar_w) / 2
        base = 0.0
        stack = [(name, css, vals[i]) for name, css, vals in series if vals[i] > 0]
        for j, (name, css, v) in enumerate(stack):
            y0, y1 = y(base), y(base + v)
            gap = 1 if j else 0  # 2px surface gap between stacked fills, split across both
            h = max(y0 - y1 - gap, 0.5)
            top_seg = j == len(stack) - 1
            parts.append(
                f'<path class="{css}" d="{_bar_path(x, y1, bar_w, h, 3 if top_seg else 0)}">'
                f"<title>{escape(cat)}: {escape(name)} {value_format.format(v)}</title></path>"
            )
            base += v
        if len(categories) <= 16 or i % 2 == 0:
            parts.append(
                f'<text class="axis" x="{x + bar_w / 2:.1f}" y="{height - 6}" '
                f'text-anchor="middle">{escape(cat)}</text>'
            )
    parts.append(
        f'<line class="baseline" x1="{pad_l}" x2="{width}" y1="{y(0):.1f}" y2="{y(0):.1f}"/></svg>'
    )
    return Markup("".join(parts))


def _bar_path(x: float, y: float, w: float, h: float, r: float) -> str:
    """Rectangle with only the top corners rounded (the data end)."""
    r = min(r, w / 2, h)
    return (
        f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
        f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{y + h:.1f}Z"
    )


def _nice_step(top: float) -> float:
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500):
        if top / step <= 4:
            return step
    return 1000


def legend(items: list[tuple[str, str]]) -> Markup:
    return Markup(
        "".join(
            f'<span class="key"><i class="sw {css}"></i>{escape(name)}</span>' for name, css in items
        )
    )


def minutes_text(seconds: float) -> str:
    return fmt_minutes(seconds)
