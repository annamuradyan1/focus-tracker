from datetime import date, datetime

from focustracker.schedule import State, due_jobs, run_due


def test_daily_due_after_11():
    assert due_jobs(datetime(2026, 11, 18, 10, 59)) == []
    assert due_jobs(datetime(2026, 11, 18, 11, 0)) == [("daily", date(2026, 11, 17))]


def test_weekly_due_sunday_evening_and_caught_up_monday():
    sunday = datetime(2026, 11, 22, 21, 5)
    assert ("weekly", date(2026, 11, 16)) in due_jobs(sunday)
    monday_morning = datetime(2026, 11, 23, 8, 0)
    assert due_jobs(monday_morning) == [("weekly", date(2026, 11, 16))]


def test_no_duplicates_and_failures_notified_once(tmp_path):
    state = State(tmp_path / "state.json")
    sent, notes = [], []
    now = datetime(2026, 11, 18, 14, 0)

    def boom(day):
        raise RuntimeError("ActivityWatch isn't reachable")

    run_due(now, state, boom, sent.append, notes.append)
    run_due(now, state, boom, sent.append, notes.append)
    assert len(notes) == 1  # failure reported once, retried silently

    run_due(now, state, sent.append, sent.append, notes.append)
    run_due(now, State(tmp_path / "state.json"), sent.append, sent.append, notes.append)
    assert sent == [date(2026, 11, 17)]  # sent once, even across restarts
