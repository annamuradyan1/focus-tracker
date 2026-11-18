"""Turn raw watcher events into one clean, non-overlapping timeline of active time.

- Only time when the AFK watcher says "not-afk" is kept.
- Browser windows are replaced by the web watcher's active tab (URL + tab title)
  wherever the two overlap; elsewhere the window title is kept.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Any, Iterable

from .aw import RawData
from .models import Event

Interval = tuple[datetime, datetime]


def _parse(raw: dict[str, Any]) -> tuple[datetime, datetime, dict[str, Any]]:
    start = datetime.fromisoformat(raw["timestamp"])
    return start, start + timedelta(seconds=float(raw.get("duration", 0))), raw.get("data", {})


def _merge(intervals: Iterable[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _without_overlaps(events: list[Event]) -> list[Event]:
    """Sort events and trim each one so it ends before the next starts."""
    events = sorted(events, key=lambda e: e.start)
    out: list[Event] = []
    for i, ev in enumerate(events):
        end = min(ev.end, events[i + 1].start) if i + 1 < len(events) else ev.end
        if end > ev.start:
            out.append(_replace_span(ev, ev.start, end))
    return out


def _replace_span(ev: Event, start: datetime, end: datetime) -> Event:
    return Event(start, end, ev.app, ev.title, ev.url, ev.incognito)


def _clip(events: list[Event], intervals: list[Interval]) -> list[Event]:
    """Intersect sorted, non-overlapping events with sorted, merged intervals."""
    out: list[Event] = []
    starts = [s for s, _ in intervals]
    for ev in events:
        i = max(bisect_right(starts, ev.start) - 1, 0)
        while i < len(intervals) and intervals[i][0] < ev.end:
            lo, hi = max(ev.start, intervals[i][0]), min(ev.end, intervals[i][1])
            if hi > lo:
                out.append(_replace_span(ev, lo, hi))
            i += 1
    return out


def _overlay_web(piece: Event, web: list[Event], web_starts: list[datetime]) -> list[Event]:
    """Split a browser-window piece into tab-level events using the web watcher."""
    out: list[Event] = []
    cursor = piece.start
    i = max(bisect_right(web_starts, piece.start) - 1, 0)
    while i < len(web) and web[i].start < piece.end:
        tab = web[i]
        lo, hi = max(piece.start, tab.start), min(piece.end, tab.end)
        if hi > lo:
            if lo > cursor:
                out.append(_replace_span(piece, cursor, lo))
            out.append(Event(lo, hi, piece.app, tab.title, tab.url, tab.incognito))
            cursor = hi
        i += 1
    if cursor < piece.end:
        out.append(_replace_span(piece, cursor, piece.end))
    return out


def build_timeline(
    raw: RawData,
    browsers: Iterable[str],
    day_start: datetime | None = None,
    day_end: datetime | None = None,
) -> list[Event]:
    browser_apps = {b.lower() for b in browsers}

    active = _merge(
        (s, e) for s, e, d in map(_parse, raw.afk) if d.get("status") == "not-afk" and e > s
    )
    if day_start and day_end:
        active = _clip_intervals(active, day_start, day_end)

    windows = _without_overlaps(
        [
            Event(s, e, d.get("app", ""), d.get("title", ""), d.get("url", "") or "")
            for s, e, d in map(_parse, raw.window)
        ]
    )
    web = _without_overlaps(
        [
            Event(s, e, "", d.get("title", ""), d.get("url", "") or "", bool(d.get("incognito")))
            for s, e, d in map(_parse, raw.web)
        ]
    )
    web_starts = [w.start for w in web]

    timeline: list[Event] = []
    for piece in _clip(windows, active):
        if piece.app.lower() in browser_apps and web:
            timeline.extend(_overlay_web(piece, web, web_starts))
        else:
            timeline.append(piece)
    return timeline


def _clip_intervals(intervals: list[Interval], start: datetime, end: datetime) -> list[Interval]:
    return [(max(s, start), min(e, end)) for s, e in intervals if e > start and s < end]
