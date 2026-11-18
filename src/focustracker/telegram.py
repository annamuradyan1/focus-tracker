"""Deliver reports through a Telegram bot (token and chat ID from .env)."""

from __future__ import annotations

import os
from html import escape
from pathlib import Path

import httpx

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, timeout: float = 30.0):
        self.token = token
        self.chat_id = chat_id
        self._http = httpx.Client(timeout=timeout)

    @classmethod
    def from_env(cls) -> Telegram:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            raise TelegramError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in ~/.config/focus-tracker/.env"
            )
        return cls(token, chat_id)

    def _call(self, method: str, **kwargs) -> dict:
        try:
            resp = self._http.post(API.format(token=self.token, method=method), **kwargs)
        except httpx.HTTPError as exc:
            # Don't include the URL: it contains the bot token.
            raise TelegramError(f"Couldn't reach Telegram ({type(exc).__name__}).") from None
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if not body.get("ok"):
            raise TelegramError(f"Telegram {method} failed: {body.get('description', resp.status_code)}")
        return body["result"]

    def send_message(self, html: str) -> None:
        self._call(
            "sendMessage",
            data={"chat_id": self.chat_id, "text": html[:4096], "parse_mode": "HTML",
                  "disable_web_page_preview": "true"},
        )

    def send_document(self, path: Path, caption: str = "") -> None:
        with open(path, "rb") as fh:
            self._call(
                "sendDocument",
                data={"chat_id": self.chat_id, "caption": caption[:1024]},
                files={"document": (path.name, fh, "text/html")},
            )


def format_summary(title: str, bullets: list[str]) -> str:
    """Telegram HTML message: bold title and bullet lines."""
    lines = [f"<b>{escape(title)}</b>", ""]
    lines += [f"• {escape(b)}" for b in bullets]
    return "\n".join(lines)
