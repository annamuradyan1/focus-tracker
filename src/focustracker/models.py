"""Core data types shared across the pipeline.

Pipeline: raw ActivityWatch events -> Event (active, browser-resolved)
-> ClassifiedEvent (thread assigned) -> Stint (contiguous time on one thread)
-> Block (a focus block: stints on one thread plus the micro-interruptions inside it).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class Event:
    start: datetime
    end: datetime
    app: str
    title: str
    url: str = ""
    incognito: bool = False

    @property
    def duration(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def domain(self) -> str:
        if not self.url:
            return ""
        host = urlsplit(self.url).hostname or ""
        return host.removeprefix("www.")


@dataclass(frozen=True, slots=True)
class Classification:
    thread: str
    group: str
    source: str  # "rule", "llm", "private" or "unclassified"
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ClassifiedEvent:
    event: Event
    thread: str
    group: str
    kind: str  # "work", "drift" or "neutral"
    label: str  # human-friendly app/site name, e.g. "Gmail", "Telegram"
    is_comms: bool
    source: str

    @property
    def start(self) -> datetime:
        return self.event.start

    @property
    def end(self) -> datetime:
        return self.event.end


@dataclass
class Stint:
    """Uninterrupted time on one thread (possibly across several apps)."""

    thread: str
    group: str
    kind: str
    start: datetime
    end: datetime
    label_seconds: Counter[str] = field(default_factory=Counter)
    comms_seconds: float = 0.0

    @property
    def duration(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def label(self) -> str:
        """The app/site where most of this stint happened."""
        if not self.label_seconds:
            return "?"
        return self.label_seconds.most_common(1)[0][0]


@dataclass
class Interruption:
    """A short detour (one or more stints) away from a block's thread and back."""

    stints: list[Stint]

    @property
    def start(self) -> datetime:
        return self.stints[0].start

    @property
    def end(self) -> datetime:
        return self.stints[-1].end

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.stints)

    @property
    def label(self) -> str:
        totals: Counter[str] = Counter()
        for s in self.stints:
            totals.update(s.label_seconds)
        return totals.most_common(1)[0][0] if totals else "?"

    @property
    def thread(self) -> str:
        return max(self.stints, key=lambda s: s.duration).thread


@dataclass
class Block:
    """Time on one thread, with micro-interruptions folded in."""

    thread: str
    group: str
    kind: str
    stints: list[Stint]
    interruptions: list[Interruption] = field(default_factory=list)

    @property
    def start(self) -> datetime:
        return self.stints[0].start

    @property
    def end(self) -> datetime:
        return self.stints[-1].end

    @property
    def duration(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def focused_seconds(self) -> float:
        return sum(s.duration for s in self.stints)

    @property
    def longest_clean_stint(self) -> Stint:
        return max(self.stints, key=lambda s: s.duration)
