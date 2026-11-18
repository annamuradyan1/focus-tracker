"""Scheduled delivery: what to send now, without duplicates.

launchd runs `focustracker scheduled` at 11:00 daily, Sunday 21:00 and at login.
If the Mac was asleep, launchd runs the missed job on wake; the state file makes
every run idempotent, so a report is never sent twice.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from .config import data_dir

DAILY_AT = time(11, 0)
WEEKLY_AT = time(21, 0)  # on Sundays
LABEL = "com.focustracker.scheduled"


class State:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "state.json"
        self.data: dict[str, list[str]] = {"sent": [], "failures": []}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    def has(self, kind: str, key: str) -> bool:
        return key in self.data[kind]

    def add(self, kind: str, key: str) -> None:
        self.data[kind] = sorted(set(self.data[kind]) | {key})[-120:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)


def due_jobs(now: datetime) -> list[tuple[str, date]]:
    """Jobs that should have run by `now`: ('daily', day) and/or ('weekly', monday)."""
    jobs: list[tuple[str, date]] = []
    today = now.date()
    if now.time() >= DAILY_AT:
        jobs.append(("daily", today - timedelta(days=1)))
    this_monday = today - timedelta(days=today.weekday())
    if today.weekday() == 6 and now.time() >= WEEKLY_AT:
        jobs.append(("weekly", this_monday))
    elif today.weekday() <= 1:
        # Missed Sunday evening (Mac asleep or off): catch up on Monday or Tuesday.
        jobs.append(("weekly", this_monday - timedelta(days=7)))
    return jobs


def run_due(
    now: datetime,
    state: State,
    send_daily: Callable[[date], None],
    send_weekly: Callable[[date], None],
    notify_failure: Callable[[str], None],
) -> list[str]:
    """Send whatever is due and not sent yet. Returns a log of what happened."""
    log = []
    for kind, day in due_jobs(now):
        key = f"{kind}:{day.isoformat()}"
        if state.has("sent", key):
            continue
        try:
            (send_daily if kind == "daily" else send_weekly)(day)
        except Exception as exc:  # noqa: BLE001 - every failure should reach the user once
            log.append(f"{key} failed: {exc}")
            if not state.has("failures", key):
                notify_failure(f"Focus tracker couldn't send the {kind} report for {day}: {exc}")
                state.add("failures", key)
            continue
        state.add("sent", key)
        log.append(f"{key} sent")
    return log


def plist(python: str = sys.executable) -> dict:
    logs = data_dir() / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "focustracker", "scheduled"],
        "StartCalendarInterval": [
            {"Hour": DAILY_AT.hour, "Minute": DAILY_AT.minute},
            {"Weekday": 0, "Hour": WEEKLY_AT.hour, "Minute": WEEKLY_AT.minute},
        ],
        "RunAtLoad": True,
        "StandardOutPath": str(logs / "scheduled.log"),
        "StandardErrorPath": str(logs / "scheduled.log"),
    }


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def install() -> Path:
    path = plist_path()
    (data_dir() / "logs").mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(plist()))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    return path


def uninstall() -> None:
    path = plist_path()
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
    path.unlink(missing_ok=True)
