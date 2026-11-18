"""Reading raw events from ActivityWatch (live REST API or an exported JSON file)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

WINDOW_TYPE = "currentwindow"
AFK_TYPE = "afkstatus"
WEB_TYPE = "web.tab.current"


class ActivityWatchError(RuntimeError):
    pass


@dataclass
class RawData:
    """Raw ActivityWatch events per watcher, in AW's own event format."""

    window: list[dict[str, Any]] = field(default_factory=list)
    afk: list[dict[str, Any]] = field(default_factory=list)
    web: list[dict[str, Any]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.window

    def to_json(self) -> str:
        return json.dumps({"window": self.window, "afk": self.afk, "web": self.web})

    @classmethod
    def from_file(cls, path: Path) -> RawData:
        raw = json.loads(Path(path).read_text())
        return cls(window=raw.get("window", []), afk=raw.get("afk", []), web=raw.get("web", []))


class AWClient:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    def _get(self, path: str, **params: Any) -> Any:
        try:
            resp = self._http.get(f"{self.base_url}{path}", params=params or None)
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise ActivityWatchError(
                f"ActivityWatch isn't reachable at {self.base_url}. Is it running?"
            ) from exc
        except httpx.HTTPError as exc:
            raise ActivityWatchError(f"ActivityWatch request failed: {exc}") from exc
        return resp.json()

    def find_buckets(self) -> dict[str, list[str]]:
        """Bucket IDs per watcher type, preferring this machine's window/AFK buckets."""
        buckets: dict[str, dict[str, Any]] = self._get("/buckets/")
        hostname = self._get("/info").get("hostname")
        found: dict[str, list[str]] = {WINDOW_TYPE: [], AFK_TYPE: [], WEB_TYPE: []}
        for bucket_id, meta in buckets.items():
            kind = meta.get("type")
            if kind not in found:
                continue
            # Web watchers report hostname "unknown", so only filter window/AFK buckets.
            if kind != WEB_TYPE and hostname and meta.get("hostname") not in (hostname, None):
                continue
            found[kind].append(bucket_id)
        if not found[WINDOW_TYPE] or not found[AFK_TYPE]:
            raise ActivityWatchError(
                "Couldn't find aw-watcher-window and aw-watcher-afk buckets on this machine."
            )
        return found

    def events(self, bucket_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._get(
            f"/buckets/{bucket_id}/events",
            start=start.isoformat(),
            end=end.isoformat(),
            limit=-1,
        )

    def fetch(self, start: datetime, end: datetime) -> RawData:
        buckets = self.find_buckets()

        def collect(kind: str) -> list[dict[str, Any]]:
            return [e for b in buckets[kind] for e in self.events(b, start, end)]

        return RawData(window=collect(WINDOW_TYPE), afk=collect(AFK_TYPE), web=collect(WEB_TYPE))
