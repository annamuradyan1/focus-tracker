"""Generate realistic synthetic ActivityWatch data for demos and tests.

The output is in ActivityWatch's own raw format (window, AFK and web watcher
events), so it goes through exactly the same pipeline as real data. Every
company, person and project here is made up.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .aw import RawData

CHROME = "Google Chrome"


@dataclass(frozen=True)
class Place:
    """Somewhere work happens: an app window or a browser tab."""

    app: str
    title: str
    url: str = ""


def tab(title: str, url: str) -> Place:
    return Place(CHROME, title, url)


INBOX = tab("Inbox (4) - me@example.com - Gmail", "https://mail.google.com/mail/u/0/#inbox")

TASKS: dict[str, list[Place]] = {
    "northwind": [
        tab("Cover letter – Northwind Labs - Google Docs", "https://docs.google.com/document/d/nw1/edit"),
        tab("Senior Product Analyst - Northwind Labs", "https://boards.greenhouse.io/northwindlabs/jobs/4417"),
        tab("Northwind Labs | About us", "https://northwindlabs.example/about"),
    ],
    "lumenfold": [
        tab("Lumenfold - Product Operations Lead", "https://jobs.lever.co/lumenfold/88a1/apply"),
        tab("Lumenfold take-home – notes - Google Docs", "https://docs.google.com/document/d/lf2/edit"),
        tab("Re: Take-home timing – Lumenfold - me@example.com - Gmail",
            "https://mail.google.com/mail/u/0/#inbox/lf"),
    ],
    "tidewell": [
        tab("Tidewell Health - Data Product Manager", "https://jobs.ashbyhq.com/tidewell-health/71c2"),
        tab("Tidewell Health interview prep - Google Docs", "https://docs.google.com/document/d/tw3/edit"),
    ],
    "cv": [
        tab("CV – master version - Google Docs", "https://docs.google.com/document/d/cv0/edit"),
        tab("Claude – tighten resume bullet points", "https://claude.ai/chat/cv"),
    ],
    "boards": [
        tab("Product analyst jobs | LinkedIn", "https://www.linkedin.com/jobs/search/?keywords=product"),
        tab("Remote product jobs – Wellfound", "https://wellfound.com/jobs"),
    ],
    "game": [
        Place("Code", "lobby.tsx — party-game"),
        Place("Code", "scoring.ts — party-game"),
        Place("iTerm2", "npm run dev — party-game"),
        tab("Party game – lobby screens – Figma", "https://www.figma.com/design/pg/party-game"),
        tab("Pull requests · partygame-dev/party-game", "https://github.com/partygame-dev/party-game/pulls"),
    ],
    "learning": [
        tab("Product Analytics – Week 3 | Coursera", "https://www.coursera.org/learn/product-analytics"),
        tab("Retention curves, explained | Medium", "https://medium.com/@analytics/retention-curves"),
        tab("Claude – how to model game rounds", "https://claude.ai/chat/rounds"),
    ],
}

# Long detours that are real switches (minutes, not seconds).
DETOURS: list[tuple[float, Place, tuple[int, int]]] = [
    (5, INBOX, (60, 300)),
    (4, Place("Telegram", "Telegram"), (45, 240)),
    (2, Place("Slack", "Slack | general | Indie Makers"), (45, 180)),
    (2, tab("Feed | LinkedIn", "https://www.linkedin.com/feed/"), (60, 360)),
    (2, tab("Board game night ideas - YouTube", "https://www.youtube.com/watch?v=bg1"), (120, 600)),
    (2, tab("r/boardgames", "https://www.reddit.com/r/boardgames/"), (60, 400)),
    (1, tab("Instagram", "https://www.instagram.com/"), (40, 240)),
    (1, Place("Calendar", "Calendar"), (40, 150)),
    (1, tab("New Tab", ""), (35, 90)),
]

# Quick checks that become micro-interruptions (seconds).
GLANCES: list[tuple[float, Place]] = [
    (8, Place("Telegram", "Telegram")),
    (3, INBOX),
    (2, Place("Slack", "Slack | general | Indie Makers")),
    (1, Place("WhatsApp", "WhatsApp")),
    (1, tab("Instagram", "https://www.instagram.com/")),
]

SESSIONS = [(time(9, 25), time(12, 50)), (time(13, 45), time(16, 15)), (time(16, 35), time(19, 0))]
WEEKEND_SESSIONS = [(time(11, 0), time(12, 30)), (time(15, 0), time(16, 10))]


def _pick(rng: random.Random, weighted: list[tuple]) -> Any:
    """Pick one entry by its leading weight; returns the rest of the tuple (or the single item)."""
    entry = rng.choices(weighted, weights=[x[0] for x in weighted])[0]
    return entry[1:] if len(entry) > 2 else entry[1]


class DayGenerator:
    def __init__(self, day: date, tz: ZoneInfo, seed: int | None = None):
        self.day = day
        self.tz = tz
        self.rng = random.Random(seed if seed is not None else day.toordinal())
        self.pieces: list[tuple[datetime, float, Place]] = []
        self.active: list[tuple[datetime, datetime]] = []

    def _emit(self, t: datetime, seconds: float, place: Place) -> datetime:
        seconds = max(1.0, round(seconds, 1))
        self.pieces.append((t, seconds, place))
        return t + timedelta(seconds=seconds)

    def _block(self, t: datetime, end: datetime, task: str, minutes: float, glance_rate: float) -> datetime:
        """Work on one task, with glances, flicker and occasional real detours."""
        rng = self.rng
        block_end = min(end, t + timedelta(minutes=minutes))
        places = TASKS[task]
        place = places[0]
        while t < block_end:
            t = self._emit(t, min(rng.expovariate(1 / 140) + 15, (block_end - t).total_seconds()), place)
            if t >= block_end:
                break
            roll = rng.random()
            if roll < glance_rate:
                t = self._emit(t, rng.uniform(4, 26), _pick(rng, GLANCES))
            elif roll < glance_rate + 0.08:
                t = self._emit(t, rng.uniform(0.6, 2.4), rng.choice(places + [INBOX]))
            elif roll < glance_rate + 0.11:
                return t  # pulled away for real; the caller picks a detour
            if rng.random() < 0.35:
                place = rng.choice(places)
        return t

    def _session(self, start: datetime, end: datetime, focus_tasks: list[str]) -> None:
        rng = self.rng
        t = start
        previous: str | None = None
        while t < end - timedelta(minutes=2):
            if previous and rng.random() < 0.3:
                task = previous  # coming back to what was left
            else:
                task = rng.choice([x for x in focus_tasks if x != previous] or focus_tasks)
            deep = rng.random() < 0.18
            minutes = rng.uniform(40, 58) if deep else rng.lognormvariate(2.4, 0.6)
            glance_rate = rng.uniform(0.15, 0.3) if deep else rng.uniform(0.2, 0.45)
            t = self._block(t, end, task, minutes, glance_rate)
            if t >= end:
                break
            if rng.random() < 0.75:
                place, (lo, hi) = _pick(rng, DETOURS)
                t = self._emit(t, min(rng.uniform(lo, hi), (end - t).total_seconds()), place)
            previous = task
        self.active.append((start, min(t, end)))

    def generate(self) -> RawData:
        weekend = self.day.weekday() >= 5
        plan = WEEKEND_SESSIONS if weekend else SESSIONS
        focus = ["game", "learning"] if weekend else self.rng.sample(
            ["northwind", "lumenfold", "tidewell", "cv", "boards", "game", "learning"], 5
        )
        for s, e in plan:
            jitter = timedelta(minutes=self.rng.uniform(-12, 12))
            start = datetime.combine(self.day, s, self.tz) + jitter
            end = datetime.combine(self.day, e, self.tz) + jitter
            self._session(start, end, focus)
        return self._to_raw()

    def _to_raw(self) -> RawData:
        def aw_event(t: datetime, seconds: float, data: dict[str, Any]) -> dict[str, Any]:
            return {"timestamp": t.astimezone(ZoneInfo("UTC")).isoformat(), "duration": seconds, "data": data}

        raw = RawData()
        for t, seconds, place in self.pieces:
            data = {"app": place.app, "title": place.title}
            if place.url:
                data["url"] = place.url
            raw.window.append(aw_event(t, seconds, data))
            if place.app == CHROME:
                raw.web.append(aw_event(t, seconds, {
                    "url": place.url or "chrome://newtab/", "title": place.title,
                    "audible": False, "incognito": False, "tabCount": 9,
                }))
        cursor = datetime.combine(self.day, time(0), self.tz)
        for start, end in self.active:
            raw.afk.append(aw_event(cursor, (start - cursor).total_seconds(), {"status": "afk"}))
            raw.afk.append(aw_event(start, (end - start).total_seconds(), {"status": "not-afk"}))
            cursor = end
        return raw


def generate_day(day: date, tz: ZoneInfo, seed: int | None = None) -> RawData:
    return DayGenerator(day, tz, seed).generate()
