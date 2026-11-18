"""Daily metrics: switches, focus blocks, micro-interruptions, returns, time of day.

All functions are pure: they take blocks/stints and settings and return numbers,
so they work the same on live data, exported files and synthetic days.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median

from .config import Settings
from .models import Block, Interruption, Stint


@dataclass
class Switch:
    at: datetime
    src: Block
    dst: Block

    @property
    def kind(self) -> str:
        """'work_work', 'work_drift', 'drift_work' or 'other'."""
        pair = f"{self.src.kind}_{self.dst.kind}"
        return pair if pair in ("work_work", "work_drift", "drift_work") else "other"


@dataclass
class HourStats:
    hour: int
    active_seconds: float = 0.0
    switches: int = 0
    micro: int = 0
    block_seconds: list[float] = field(default_factory=list)

    @property
    def active_hours(self) -> float:
        return self.active_seconds / 3600

    @property
    def switches_per_hour(self) -> float | None:
        return self.switches / self.active_hours if self.active_seconds >= 600 else None

    @property
    def micro_per_hour(self) -> float | None:
        return self.micro / self.active_hours if self.active_seconds >= 600 else None

    @property
    def avg_block_minutes(self) -> float | None:
        return sum(self.block_seconds) / len(self.block_seconds) / 60 if self.block_seconds else None


@dataclass
class Abandoned:
    thread: str
    left_at: datetime
    total_seconds: float


@dataclass
class DayMetrics:
    day: date
    settings: Settings
    stints: list[Stint]
    blocks: list[Block]
    active_seconds: float
    switches: list[Switch]
    focus_blocks: list[Block]
    interruptions: list[Interruption]
    hourly: list[HourStats]
    returns_left: int
    returns_back: int
    return_seconds: list[float]
    abandoned: list[Abandoned]
    thread_totals: list[tuple[str, str, float]]  # (thread, group, seconds), largest first
    group_totals: dict[str, float]
    task_comms_seconds: float
    general_comms_seconds: float

    # --- switching ----------------------------------------------------------------
    @property
    def active_hours(self) -> float:
        return self.active_seconds / 3600

    @property
    def switches_per_hour(self) -> float:
        return len(self.switches) / self.active_hours if self.active_seconds else 0.0

    @property
    def switch_kinds(self) -> Counter[str]:
        return Counter(s.kind for s in self.switches)

    def top_pairs(self, n: int = 8) -> list[tuple[tuple[str, str], int]]:
        return Counter((s.src.thread, s.dst.thread) for s in self.switches).most_common(n)

    # --- focus blocks -------------------------------------------------------------
    @property
    def median_block_minutes(self) -> float:
        return median(b.duration for b in self.focus_blocks) / 60 if self.focus_blocks else 0.0

    @property
    def longest_block(self) -> Block | None:
        return max(self.focus_blocks, key=lambda b: b.duration, default=None)

    @property
    def long_blocks(self) -> list[Block]:
        limit = self.settings.long_block_minutes * 60
        return [b for b in self.focus_blocks if b.duration >= limit]

    @property
    def clean_focus(self) -> tuple[Block, Stint] | None:
        """Longest stretch on one work thread with no micro-interruption at all."""
        best = None
        for block in self.focus_blocks:
            stint = block.longest_clean_stint
            if best is None or stint.duration > best[1].duration:
                best = (block, stint)
        return best

    # --- micro-interruptions ------------------------------------------------------
    @property
    def micro_count(self) -> int:
        return len(self.interruptions)

    @property
    def micro_seconds(self) -> float:
        return sum(i.duration for i in self.interruptions)

    @property
    def micro_per_hour(self) -> float:
        return self.micro_count / self.active_hours if self.active_seconds else 0.0

    @property
    def micro_per_focus_block(self) -> float:
        if not self.focus_blocks:
            return 0.0
        return sum(len(b.interruptions) for b in self.focus_blocks) / len(self.focus_blocks)

    @property
    def micro_by_label(self) -> Counter[str]:
        return Counter(i.label for i in self.interruptions)

    @property
    def micro_by_thread(self) -> Counter[str]:
        return Counter(i.thread for i in self.interruptions)

    # --- returns ------------------------------------------------------------------
    @property
    def return_rate(self) -> float | None:
        return self.returns_back / self.returns_left if self.returns_left else None

    @property
    def median_return_minutes(self) -> float | None:
        return median(self.return_seconds) / 60 if self.return_seconds else None


def _hourly(stints: list[Stint], blocks: list[Block], switches: list[Switch],
            interruptions: list[Interruption], focus_blocks: list[Block]) -> list[HourStats]:
    hours: dict[int, HourStats] = defaultdict(lambda: HourStats(0))
    for stint in stints:
        cursor = stint.start
        while cursor < stint.end:
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            chunk_end = min(next_hour, stint.end)
            hours[cursor.hour].active_seconds += (chunk_end - cursor).total_seconds()
            cursor = chunk_end
    for s in switches:
        hours[s.at.hour].switches += 1
    for i in interruptions:
        hours[i.start.hour].micro += 1
    for b in focus_blocks:
        hours[b.start.hour].block_seconds.append(b.duration)
    for h, stats in hours.items():
        stats.hour = h
    return [hours[h] for h in sorted(hours)]


def _returns(blocks: list[Block], window: float, min_seconds: float, thread_seconds: dict[str, float]):
    """Leaves from task threads, how many came back within `window`, and threads never resumed."""
    left = back = 0
    times: list[float] = []
    last_block: dict[str, int] = {}
    for idx, b in enumerate(blocks):
        last_block[b.thread] = idx

    def is_task(b: Block) -> bool:
        return b.kind == "work" and b.group != "comms"

    for idx, b in enumerate(blocks[:-1]):
        if not is_task(b) or blocks[idx + 1].thread == b.thread:
            continue
        left += 1
        nxt = next((x for x in blocks[idx + 1:] if x.thread == b.thread), None)
        if nxt is not None:
            gap = (nxt.start - b.end).total_seconds()
            times.append(gap)
            back += gap <= window

    abandoned = [
        Abandoned(b.thread, b.end, thread_seconds[b.thread])
        for idx, b in enumerate(blocks)
        if is_task(b)
        and last_block[b.thread] == idx
        and idx < len(blocks) - 1
        and thread_seconds[b.thread] >= min_seconds
    ]
    return left, back, times, abandoned


def analyze(day: date, stints: list[Stint], blocks: list[Block], settings: Settings) -> DayMetrics:
    switches = [
        Switch(b.start, a, b) for a, b in zip(blocks, blocks[1:]) if a.thread != b.thread
    ]
    focus_blocks = [
        b for b in blocks if b.kind == "work" and b.duration >= settings.min_block_seconds
    ]
    interruptions = [i for b in blocks for i in b.interruptions]

    thread_seconds: dict[str, float] = defaultdict(float)
    thread_group: dict[str, str] = {}
    group_totals: dict[str, float] = defaultdict(float)
    task_comms = general_comms = 0.0
    for s in stints:
        thread_seconds[s.thread] += s.duration
        thread_group[s.thread] = s.group
        group_totals[s.group] += s.duration
        if s.group == "comms":
            general_comms += s.duration
        else:
            task_comms += s.comms_seconds

    left, back, times, abandoned = _returns(
        blocks, settings.return_window_minutes * 60, settings.min_block_seconds, thread_seconds
    )
    return DayMetrics(
        day=day,
        settings=settings,
        stints=stints,
        blocks=blocks,
        active_seconds=sum(s.duration for s in stints),
        switches=switches,
        focus_blocks=focus_blocks,
        interruptions=interruptions,
        hourly=_hourly(stints, blocks, switches, interruptions, focus_blocks),
        returns_left=left,
        returns_back=back,
        return_seconds=times,
        abandoned=abandoned,
        thread_totals=sorted(
            ((t, thread_group[t], sec) for t, sec in thread_seconds.items()),
            key=lambda x: -x[2],
        ),
        group_totals=dict(group_totals),
        task_comms_seconds=task_comms,
        general_comms_seconds=general_comms,
    )
