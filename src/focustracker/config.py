"""Paths, settings and config loading.

Personal files live outside the repository:
  ~/.config/focus-tracker/threads.yaml   thread rules (copied from the bundled example)
  ~/.config/focus-tracker/.env           Telegram + Anthropic secrets
  ~/.local/share/focus-tracker/          caches, reports, send state
Both roots honour XDG_CONFIG_HOME / XDG_DATA_HOME and can be overridden with
FOCUSTRACKER_CONFIG_DIR / FOCUSTRACKER_DATA_DIR.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

APP_NAME = "focus-tracker"


def config_dir() -> Path:
    if override := os.environ.get("FOCUSTRACKER_CONFIG_DIR"):
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / APP_NAME


def data_dir() -> Path:
    if override := os.environ.get("FOCUSTRACKER_DATA_DIR"):
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_NAME


def reports_dir() -> Path:
    return data_dir() / "reports"


def example_config_path() -> Path:
    return Path(str(resources.files("focustracker") / "defaults" / "threads.example.yaml"))


def user_config_path() -> Path:
    return config_dir() / "threads.yaml"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    # Stints shorter than this are window flicker (alt-tab pass-through) and are dropped.
    flicker_seconds: float = 3
    # A detour up to this long that returns to the same thread is a micro-interruption:
    # counted, but it doesn't end the focus block.
    micro_interruption_seconds: float = 30
    # Blocks shorter than this aren't counted as focus blocks (they still count as switches).
    min_block_seconds: float = 60
    long_block_minutes: float = 25
    return_window_minutes: float = 30
    aw_url: str = "http://127.0.0.1:5600/api/0"
    timezone: str | None = None
    llm_enabled: bool = True
    llm_model: str = "claude-haiku-5-5"
    llm_batch_size: int = 40


@dataclass
class Config:
    settings: Settings = field(default_factory=Settings)
    groups: dict[str, str] = field(default_factory=dict)
    browsers: list[str] = field(default_factory=list)
    comms_apps: list[str] = field(default_factory=list)
    comms_domains: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    companies: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    path: Path | None = None

    def kind_of(self, group: str) -> str:
        """'work', 'drift' or 'neutral' for a thread group."""
        return self.groups.get(group, "neutral")


def _settings_from(raw: dict[str, Any]) -> Settings:
    flat = {k: v for k, v in raw.items() if k != "llm"}
    llm = raw.get("llm") or {}
    if "enabled" in llm:
        flat["llm_enabled"] = llm["enabled"]
    if "model" in llm:
        flat["llm_model"] = llm["model"]
    if "batch_size" in llm:
        flat["llm_batch_size"] = llm["batch_size"]
    known = {f.name for f in fields(Settings)}
    unknown = set(flat) - known
    if unknown:
        raise ConfigError(f"Unknown settings: {', '.join(sorted(unknown))}")
    return Settings(**flat)


def _normalise_companies(raw: list[Any]) -> list[dict[str, Any]]:
    out = []
    for item in raw or []:
        if isinstance(item, str):
            out.append({"name": item, "aliases": []})
        else:
            out.append({"name": item["name"], "aliases": list(item.get("aliases", []))})
    return out


def load_config(path: Path | None = None) -> Config:
    """Load thread rules from `path`, the user's config, or fail with a helpful message."""
    path = path or user_config_path()
    if not path.exists():
        raise ConfigError(
            f"No config at {path}. Run `focustracker init` to create one from the example."
        )
    raw = yaml.safe_load(path.read_text()) or {}
    comms = raw.get("comms") or {}
    return Config(
        settings=_settings_from(raw.get("settings") or {}),
        groups=dict(raw.get("groups") or {}),
        browsers=list(raw.get("browsers") or []),
        comms_apps=list(comms.get("apps") or []),
        comms_domains=list(comms.get("domains") or []),
        labels=dict(raw.get("labels") or {}),
        companies=_normalise_companies(raw.get("companies") or []),
        rules=list(raw.get("rules") or []),
        path=path,
    )


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from the user's .env into os.environ (existing vars win)."""
    path = path or config_dir() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)
