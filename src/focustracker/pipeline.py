"""End-to-end: raw ActivityWatch data for a day -> DayMetrics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

from .aw import AWClient, RawData
from .classify import Classifier
from .config import Config, Settings
from .metrics import DayMetrics, analyze
from .models import ClassifiedEvent
from .stints import segment
from .timeline import build_timeline


def local_tz(settings: Settings) -> tzinfo:
    if settings.timezone:
        return ZoneInfo(settings.timezone)
    return datetime.now().astimezone().tzinfo


def day_bounds(day: date, tz: tzinfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tz)
    return start, datetime.combine(day + timedelta(days=1), time(0), tz)


@dataclass
class DayResult:
    metrics: DayMetrics
    events: list[ClassifiedEvent]
    llm_error: str | None = None
    llm_sent: int = 0


def load_raw(day: date, config: Config, raw: RawData | None = None) -> RawData:
    """Raw events for `day`: from the given data, or fetched live from ActivityWatch."""
    if raw is not None:
        return raw
    start, end = day_bounds(day, local_tz(config.settings))
    return AWClient(config.settings.aw_url).fetch(start, end)


def analyze_day(
    day: date,
    config: Config,
    raw: RawData | None = None,
    classifier: Classifier | None = None,
) -> DayResult:
    tz = local_tz(config.settings)
    start, end = day_bounds(day, tz)
    raw = load_raw(day, config, raw)
    timeline = [
        replace(e, start=e.start.astimezone(tz), end=e.end.astimezone(tz))
        for e in build_timeline(raw, config.browsers, start, end)
    ]
    classifier = classifier or Classifier(config)
    events = classifier.classify(timeline)
    stints, blocks = segment(events, config.settings)
    return DayResult(
        metrics=analyze(day, stints, blocks, config.settings),
        events=events,
        llm_error=classifier.llm_error,
        llm_sent=classifier.llm_sent,
    )
