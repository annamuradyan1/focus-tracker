from datetime import datetime, timedelta, timezone

from focustracker.aw import RawData
from focustracker.timeline import build_timeline

T0 = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)


def ev(offset: float, duration: float, **data) -> dict:
    return {
        "timestamp": (T0 + timedelta(seconds=offset)).isoformat(),
        "duration": duration,
        "data": data,
    }


def test_afk_time_is_dropped():
    raw = RawData(
        window=[ev(0, 600, app="Code", title="main.py")],
        afk=[ev(0, 120, status="not-afk"), ev(120, 300, status="afk"), ev(420, 180, status="not-afk")],
    )
    timeline = build_timeline(raw, browsers=[])
    assert [e.duration for e in timeline] == [120, 180]


def test_browser_window_is_replaced_by_web_tabs():
    raw = RawData(
        window=[ev(0, 100, app="Google Chrome", title="Some window")],
        afk=[ev(0, 100, status="not-afk")],
        web=[
            ev(10, 40, url="https://mail.google.com/mail/u/0", title="Inbox"),
            ev(50, 30, url="https://www.youtube.com/watch", title="Video"),
        ],
    )
    timeline = build_timeline(raw, browsers=["Google Chrome"])
    assert [(e.title, e.duration) for e in timeline] == [
        ("Some window", 10),
        ("Inbox", 40),
        ("Video", 30),
        ("Some window", 20),
    ]
    assert timeline[2].domain == "youtube.com"


def test_overlapping_window_events_are_trimmed():
    raw = RawData(
        window=[ev(0, 60, app="A", title="a"), ev(50, 30, app="B", title="b")],
        afk=[ev(0, 100, status="not-afk")],
    )
    timeline = build_timeline(raw, browsers=[])
    assert [(e.app, e.duration) for e in timeline] == [("A", 50), ("B", 30)]


def test_clipped_to_day_bounds():
    raw = RawData(
        window=[ev(-300, 600, app="A", title="a")],
        afk=[ev(-300, 600, status="not-afk")],
    )
    timeline = build_timeline(raw, browsers=[], day_start=T0, day_end=T0 + timedelta(hours=1))
    assert timeline[0].start == T0
    assert timeline[0].duration == 300
