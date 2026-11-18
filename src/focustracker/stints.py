"""From classified events to stints and focus blocks.

Noise handling is deliberately light, because short switches are the point:
  1. Stints under `flicker_seconds` (default 3s) are dropped: passing through a
     window while alt-tabbing isn't a switch.
  2. A detour away from a thread that lasts at most `micro_interruption_seconds`
     (default 30s) and comes back to the same thread is a micro-interruption. It
     is kept and counted on the block, but it doesn't end the block.
Everything longer is a real switch to another block.
"""

from __future__ import annotations

from collections import Counter

from .config import Settings
from .models import Block, ClassifiedEvent, Interruption, Stint


def _gap(a_end, b_start) -> float:
    return (b_start - a_end).total_seconds()


def build_stints(events: list[ClassifiedEvent], max_gap: float) -> list[Stint]:
    """Merge consecutive same-thread events (closer than `max_gap`) into stints."""
    stints: list[Stint] = []
    for ev in events:
        last = stints[-1] if stints else None
        if last and last.thread == ev.thread and _gap(last.end, ev.start) <= max_gap:
            last.end = max(last.end, ev.end)
        else:
            last = Stint(ev.thread, ev.group, ev.kind, ev.start, ev.end, Counter())
            stints.append(last)
        last.label_seconds[ev.label] += ev.event.duration
        if ev.is_comms:
            last.comms_seconds += ev.event.duration
    return stints


def drop_flicker(stints: list[Stint], flicker_seconds: float, max_gap: float) -> list[Stint]:
    """Remove stints shorter than `flicker_seconds`, re-joining the neighbours they split."""
    out: list[Stint] = []
    for stint in stints:
        if stint.duration < flicker_seconds:
            continue
        last = out[-1] if out else None
        if last and last.thread == stint.thread and _gap(last.end, stint.start) <= max_gap:
            last.end = stint.end
            last.label_seconds.update(stint.label_seconds)
            last.comms_seconds += stint.comms_seconds
        else:
            out.append(stint)
    return out


def build_blocks(stints: list[Stint], settings: Settings) -> list[Block]:
    """Group stints into blocks, folding short same-thread detours in as interruptions."""
    limit = settings.micro_interruption_seconds
    blocks: list[Block] = []
    i, n = 0, len(stints)
    while i < n:
        first = stints[i]
        block = Block(first.thread, first.group, first.kind, [first])
        j = i + 1
        while j < n:
            nxt = stints[j]
            if _gap(block.end, nxt.start) > limit:
                break
            if nxt.thread == block.thread:
                block.stints.append(nxt)
                j += 1
                continue
            # Look for a short detour that returns to this block's thread.
            k = j
            while k < n and stints[k].thread != block.thread:
                if _gap(block.end, stints[k].end) > limit:
                    break
                k += 1
            if k < n and stints[k].thread == block.thread and _gap(block.end, stints[k].start) <= limit:
                block.interruptions.append(Interruption(stints[j:k]))
                block.stints.append(stints[k])
                j = k + 1
            else:
                break
        blocks.append(block)
        i = j
    return blocks


def segment(events: list[ClassifiedEvent], settings: Settings) -> tuple[list[Stint], list[Block]]:
    max_gap = settings.micro_interruption_seconds
    stints = drop_flicker(build_stints(events, max_gap), settings.flicker_seconds, max_gap)
    return stints, build_blocks(stints, settings)
