"""Rule-based classification from the YAML config."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..config import Config
from ..models import Event


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]


def _domain_matches(domain: str, patterns: list[str]) -> bool:
    return any(domain == p or domain.endswith("." + p) for p in patterns)


@dataclass
class Rule:
    thread: str
    group: str
    apps: set[str]
    domains: list[str]
    titles: list[re.Pattern[str]]
    urls: list[re.Pattern[str]]

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> Rule:
        return cls(
            thread=raw["thread"],
            group=raw["group"],
            apps={a.lower() for a in _as_list(raw.get("app"))},
            domains=[d.lower() for d in _as_list(raw.get("domain"))],
            titles=[re.compile(p, re.IGNORECASE) for p in _as_list(raw.get("title"))],
            urls=[re.compile(p, re.IGNORECASE) for p in _as_list(raw.get("url"))],
        )

    def match(self, ev: Event) -> dict[str, str] | None:
        """Named groups captured by the match ({} if none), or None if no match."""
        if self.apps and ev.app.lower() not in self.apps:
            return None
        if self.domains and not _domain_matches(ev.domain, self.domains):
            return None
        captured: dict[str, str] = {}
        for patterns, text in ((self.titles, ev.title), (self.urls, ev.url)):
            if not patterns:
                continue
            found = next((m for p in patterns if (m := p.search(text))), None)
            if not found:
                return None
            captured.update({k: v for k, v in found.groupdict().items() if v})
        return captured


@dataclass
class Company:
    name: str
    pattern: re.Pattern[str]
    slugs: set[str]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


class RuleSet:
    def __init__(self, config: Config):
        self.rules = [Rule.from_config(r) for r in config.rules]
        self.companies = []
        for c in config.companies:
            names = [c["name"], *c["aliases"]]
            alternation = "|".join(re.escape(n) for n in names)
            self.companies.append(
                Company(
                    name=c["name"],
                    pattern=re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])", re.IGNORECASE),
                    slugs={_slug(n) for n in names},
                )
            )

    def company_name(self, raw: str) -> str:
        """Map a captured URL slug like 'northwind-labs' to a configured name, or prettify it."""
        slug = _slug(raw)
        for company in self.companies:
            if slug in company.slugs:
                return company.name
        return re.sub(r"[-_]+", " ", raw).strip().title()

    def match(self, ev: Event) -> tuple[str, str] | None:
        """(thread, group) for the first matching company or rule."""
        haystack = f"{ev.title} {ev.url}"
        for company in self.companies:
            if company.pattern.search(haystack):
                return f"Job search: {company.name}", "job_search"
        for rule in self.rules:
            captured = rule.match(ev)
            if captured is None:
                continue
            if "company" in captured:
                captured["company"] = self.company_name(captured["company"])
            try:
                return rule.thread.format(**captured), rule.group
            except KeyError:
                continue  # template needs a group this match didn't capture
        return None

    def known_threads(self) -> list[tuple[str, str]]:
        """Fixed (thread, group) pairs from the rules, for the LLM prompt."""
        seen: dict[str, str] = {}
        for rule in self.rules:
            if "{" not in rule.thread:
                seen.setdefault(rule.thread, rule.group)
        for company in self.companies:
            seen.setdefault(f"Job search: {company.name}", "job_search")
        return list(seen.items())
