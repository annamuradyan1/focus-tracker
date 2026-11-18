"""Classify titles that no rule matched, using the Claude API.

Only the app name, the site domain and the window/tab title are sent, in
batches. Full URLs are not sent. Results are cached by the caller.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..models import Classification

SYSTEM_PROMPT = """\
You sort a person's computer activity into the work threads they track.
A thread is the task they were doing, not the app: an email to a recruiter at a
company they're applying to belongs to that company's application thread, not
to general comms.

Known threads (name | group):
{threads}

For each item, pick the best known thread. You may create a new thread only for a
specific company the person is applying to, named exactly "Job search: <Company>"
with group job_search. If the title is too generic to tell (e.g. "New Tab",
"Untitled", a bare app name), answer thread "Unknown" with group unknown.
confidence is your probability (0 to 1) that the thread is right."""


@dataclass(frozen=True)
class TitleItem:
    key: str
    app: str
    domain: str
    title: str


class LLMError(RuntimeError):
    pass


class LLMClassifier:
    def __init__(
        self,
        model: str,
        known_threads: list[tuple[str, str]],
        groups: list[str],
        batch_size: int = 40,
    ):
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError(
                "LLM classification needs the 'anthropic' package: uv sync --extra llm"
            ) from exc
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.batch_size = batch_size
        self.groups = sorted(set(groups) | {"unknown"})
        threads = "\n".join(f"- {name} | {group}" for name, group in known_threads)
        self.system = SYSTEM_PROMPT.format(threads=threads)

    def _schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "thread": {"type": "string"},
                            "group": {"type": "string", "enum": self.groups},
                            "confidence": {"type": "number"},
                        },
                        "required": ["id", "thread", "group", "confidence"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        }

    def _classify_batch(self, batch: list[TitleItem]) -> dict[str, Classification]:
        lines = "\n".join(
            json.dumps({"id": i, "app": it.app, "site": it.domain, "title": it.title})
            for i, it in enumerate(batch)
        )
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=self.system,
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": self._schema()},
                },
                messages=[{"role": "user", "content": f"Classify these items:\n{lines}"}],
            )
        except self._anthropic.AuthenticationError as exc:
            raise LLMError("Claude API rejected the API key (ANTHROPIC_API_KEY).") from exc
        except self._anthropic.APIError as exc:
            raise LLMError(f"Claude API request failed: {exc}") from exc

        if response.stop_reason in ("refusal", "max_tokens"):
            raise LLMError(f"Claude API stopped early ({response.stop_reason}).")
        text = next(b.text for b in response.content if b.type == "text")
        out: dict[str, Classification] = {}
        for row in json.loads(text)["items"]:
            if 0 <= row["id"] < len(batch):
                confidence = min(max(float(row["confidence"]), 0.0), 1.0)
                out[batch[row["id"]].key] = Classification(
                    row["thread"].strip() or "Unknown", row["group"], "llm", confidence
                )
        return out

    def classify(self, items: list[TitleItem]) -> dict[str, Classification]:
        results: dict[str, Classification] = {}
        for i in range(0, len(items), self.batch_size):
            results.update(self._classify_batch(items[i : i + self.batch_size]))
        return results
