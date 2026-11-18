from datetime import date, datetime, timedelta, timezone

from focustracker.config import Settings
from focustracker.metrics import analyze
from focustracker.models import ClassifiedEvent, Event
from focustracker.stints import segment

T0 = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)
SETTINGS = Settings()

THREADS = {
    "A": ("Job search: Acme", "job_search", "work", "Google Docs", False),
    "B": ("Side project: game", "side_project", "work", "VS Code", False),
    "T": ("Comms", "comms", "work", "Telegram", True),
    "G": ("Comms", "comms", "work", "Gmail", True),
    "Y": ("Drift: video", "drift", "drift", "YouTube", False),
}


def day(*parts: tuple[str, float]) -> list[ClassifiedEvent]:
    """Build back-to-back events from (thread key, seconds) pairs; key '-' is a gap."""
    out, t = [], T0
    for key, seconds in parts:
        end = t + timedelta(seconds=seconds)
        if key != "-":
            thread, group, kind, label, comms = THREADS[key]
            ev = Event(t, end, label, f"{label} window")
            out.append(ClassifiedEvent(ev, thread, group, kind, label, comms, "rule"))
        t = end
    return out


def run(*parts):
    stints, blocks = segment(day(*parts), SETTINGS)
    return analyze(date(2026, 1, 15), stints, blocks, SETTINGS)


def test_flicker_under_3s_is_dropped_and_neighbours_rejoin():
    m = run(("A", 600), ("T", 2), ("A", 600))
    assert len(m.stints) == 1
    assert m.micro_count == 0
    assert len(m.switches) == 0


def test_short_detour_is_micro_interruption_not_switch():
    m = run(("A", 600), ("T", 8), ("A", 600), ("G", 20), ("A", 300))
    assert len(m.blocks) == 1
    assert len(m.switches) == 0
    assert m.micro_count == 2
    assert m.micro_by_label == {"Telegram": 1, "Gmail": 1}
    assert m.micro_seconds == 28
    block = m.blocks[0]
    assert block.duration == 600 + 8 + 600 + 20 + 300
    # Clean focus: the longest stretch without any interruption.
    assert m.clean_focus[1].duration == 600


def test_multi_app_detour_within_30s_counts_once():
    m = run(("A", 300), ("T", 10), ("Y", 15), ("A", 300))
    assert m.micro_count == 1
    assert m.micro_by_label == {"YouTube": 1}


def test_detour_over_30s_is_a_real_switch():
    m = run(("A", 600), ("T", 45), ("A", 600))
    assert [b.thread for b in m.blocks] == ["Job search: Acme", "Comms", "Job search: Acme"]
    assert len(m.switches) == 2
    assert m.micro_count == 0


def test_switch_kinds_and_pairs():
    m = run(("A", 600), ("B", 600), ("Y", 300), ("B", 600))
    assert m.switch_kinds == {"work_work": 1, "work_drift": 1, "drift_work": 1}
    assert m.top_pairs()[0][1] == 1


def test_long_gap_breaks_block():
    m = run(("A", 600), ("-", 300), ("A", 600))
    assert len(m.blocks) == 2
    assert len(m.switches) == 0  # same thread on both sides isn't a switch


def test_returns_and_abandoned_threads():
    # Leave A for B and come back after 10 min; leave A again and never return.
    m = run(("A", 600), ("B", 600), ("A", 600), ("B", 900))
    assert m.returns_left == 3  # A->B, B->A, A->B
    assert m.returns_back == 2  # A came back (10 min), B came back (10 min)
    assert m.median_return_minutes == 10
    assert [a.thread for a in m.abandoned] == ["Job search: Acme"]


def test_focus_block_stats():
    m = run(("A", 30 * 60), ("Y", 120), ("B", 10 * 60), ("T", 40))
    assert len(m.focus_blocks) == 2  # drift and short blocks excluded
    assert len(m.long_blocks) == 1
    assert m.longest_block.thread == "Job search: Acme"
    assert m.median_block_minutes == 20


def test_task_comms_vs_general_comms():
    events = day(("A", 300), ("T", 300))
    # An email about the Acme application: comms app, task thread.
    ev = events[0]
    events[0] = ClassifiedEvent(ev.event, ev.thread, ev.group, ev.kind, "Gmail", True, "rule")
    stints, blocks = segment(events, SETTINGS)
    m = analyze(date(2026, 1, 15), stints, blocks, SETTINGS)
    assert m.task_comms_seconds == 300
    assert m.general_comms_seconds == 300


def test_hourly_split_across_hour_boundary():
    m = run(("-", 50 * 60), ("A", 20 * 60))
    assert [(h.hour, h.active_seconds) for h in m.hourly] == [(9, 600), (10, 600)]
