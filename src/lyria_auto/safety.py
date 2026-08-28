from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import SafetyBlockedError


@dataclass(frozen=True)
class SafetyResult:
    ok: bool
    matched_terms: list[str]


class PromptSafety:
    def __init__(self, blocked_terms: list[str]):
        self.blocked_terms = [t.strip().lower() for t in blocked_terms if t.strip()]

    def inspect(self, prompt: str) -> SafetyResult:
        lower = prompt.lower()
        matches = [term for term in self.blocked_terms if term in lower]
        return SafetyResult(ok=not matches, matched_terms=matches)

    def require_safe(self, prompt: str) -> None:
        result = self.inspect(prompt)
        if not result.ok:
            raise SafetyBlockedError("Prompt 含有禁止參照詞：" + ", ".join(result.matched_terms))

    def neutral_rewrite(self, prompt: str) -> str:
        lines = []
        for line in prompt.splitlines():
            lowered = line.lower()
            if any(token in lowered for token in ("artist", "song", "copyright", "lyrics by")):
                continue
            lines.append(line)
        text = " ".join(lines)
        for term in self.blocked_terms:
            text = re.sub(re.escape(term), "", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(inspired by|influence of|imitate|mimic)\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        suffix = (
            " Instrumental only. No vocals or spoken words. "
            "Create a fully original composition using only the described musical attributes."
        )
        return (text + suffix)[:4000]
