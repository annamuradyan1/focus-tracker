"""Assign every event to a thread: rules first, then (optionally) the Claude API."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..config import Config, data_dir
from ..models import Classification, ClassifiedEvent, Event
from .cache import ClassificationCache, title_key
from .llm import LLMClassifier, LLMError, TitleItem
from .rules import RuleSet, _domain_matches

log = logging.getLogger(__name__)

UNKNOWN = Classification("Unknown", "unknown", "unclassified", 0.0)
PRIVATE = Classification("Private browsing", "unknown", "private", 1.0)
AMBIGUOUS_CONFIDENCE = 0.6


class Classifier:
    def __init__(self, config: Config, use_llm: bool = True, cache_path: Path | None = None):
        self.config = config
        self.rules = RuleSet(config)
        self.use_llm = (
            use_llm and config.settings.llm_enabled and bool(os.environ.get("ANTHROPIC_API_KEY"))
        )
        self.cache_path = cache_path or data_dir() / "cache.sqlite"
        self.llm_error: str | None = None
        self.llm_sent = 0
        self._browsers = {b.lower() for b in config.browsers}
        self._comms_apps = {a.lower() for a in config.comms_apps}

    def label(self, ev: Event) -> str:
        """Human-friendly source name: site for browser tabs, app otherwise."""
        if ev.domain and (ev.app.lower() in self._browsers or not ev.app):
            return self.config.labels.get(ev.domain, ev.domain)
        return self.config.labels.get(ev.app, ev.app or "?")

    def is_comms(self, ev: Event) -> bool:
        return ev.app.lower() in self._comms_apps or _domain_matches(
            ev.domain, self.config.comms_domains
        )

    def _classify_unmatched(self, items: dict[str, TitleItem]) -> dict[str, Classification]:
        if not items or not self.use_llm:
            return {}
        cache = ClassificationCache(self.cache_path)
        try:
            found = cache.get_many(list(items))
            missing = [it for key, it in items.items() if key not in found]
            if missing:
                llm = LLMClassifier(
                    self.config.settings.llm_model,
                    self.rules.known_threads(),
                    list(self.config.groups),
                    self.config.settings.llm_batch_size,
                )
                fresh = llm.classify(missing)
                self.llm_sent += len(missing)
                cache.put_many(fresh, self.config.settings.llm_model)
                found.update(fresh)
            return found
        except LLMError as exc:
            self.llm_error = str(exc)
            log.warning("LLM classification skipped: %s", exc)
            return cache.get_many(list(items))
        finally:
            cache.close()

    def classify_all(self, events: list[Event]) -> list[tuple[Event, Classification]]:
        decided: list[Classification | str] = []  # a Classification, or a cache key to resolve
        pending: dict[str, TitleItem] = {}
        for ev in events:
            if ev.incognito:
                decided.append(PRIVATE)
            elif match := self.rules.match(ev):
                decided.append(Classification(match[0], match[1], "rule"))
            else:
                key = title_key(ev.app, ev.domain, ev.title)
                pending.setdefault(key, TitleItem(key, ev.app, ev.domain, ev.title))
                decided.append(key)

        resolved = self._classify_unmatched(pending)
        return [
            (ev, d if isinstance(d, Classification) else resolved.get(d, UNKNOWN))
            for ev, d in zip(events, decided)
        ]

    def classify(self, events: list[Event]) -> list[ClassifiedEvent]:
        return [
            ClassifiedEvent(
                event=ev,
                thread=c.thread,
                group=c.group,
                kind=self.config.kind_of(c.group),
                label=self.label(ev),
                is_comms=self.is_comms(ev),
                source=c.source,
                confidence=c.confidence,
            )
            for ev, c in self.classify_all(events)
        ]


__all__ = ["AMBIGUOUS_CONFIDENCE", "Classifier", "LLMError"]
