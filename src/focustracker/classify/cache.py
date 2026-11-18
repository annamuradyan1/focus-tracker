"""SQLite cache of LLM classifications, so the same title is never sent twice.

Only a hash of (app, domain, title) is stored, never the title itself.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from ..models import Classification

SCHEMA = """
CREATE TABLE IF NOT EXISTS classifications (
    key TEXT PRIMARY KEY,
    thread TEXT NOT NULL,
    grp TEXT NOT NULL,
    confidence REAL NOT NULL,
    model TEXT NOT NULL,
    created_at REAL NOT NULL
)
"""


def title_key(app: str, domain: str, title: str) -> str:
    return hashlib.sha256(f"{app}\x1f{domain}\x1f{title}".encode()).hexdigest()


class ClassificationCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(SCHEMA)

    def get_many(self, keys: list[str]) -> dict[str, Classification]:
        found: dict[str, Classification] = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            rows = self._db.execute(
                f"SELECT key, thread, grp, confidence FROM classifications "
                f"WHERE key IN ({','.join('?' * len(chunk))})",
                chunk,
            )
            for key, thread, grp, confidence in rows:
                found[key] = Classification(thread, grp, "llm", confidence)
        return found

    def put_many(self, items: dict[str, Classification], model: str) -> None:
        now = time.time()
        self._db.executemany(
            "INSERT OR REPLACE INTO classifications VALUES (?, ?, ?, ?, ?, ?)",
            [(k, c.thread, c.group, c.confidence, model, now) for k, c in items.items()],
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()
