"""Fact-check what Weebo writes for the user (the brief, drafts, day-sheet summaries) against the facts store.

First a deterministic pass: every event the text names is matched against the store, and the times it states are
compared with the source ("the brief says 3pm; the calendar says 4pm"), as are cancellations. Then, only when the
text makes claims about stored facts, an optional cheap second pass on the fast tier compares the remaining claims
with the evidence. Mismatches are flagged right under the text; nothing is silently rewritten.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .. import log
from ..timeparse import zone

if TYPE_CHECKING:
    from ..app import WeeboApp

logger = log.get("factcheck")

_TIME = re.compile(r"\b(?:(\d{1,2})(?::([0-5]\d))?\s*([ap])\.?\s?m\.?|([01]?\d|2[0-3]):([0-5]\d))(?![\d])", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?\n])\s+")
_STOP = {"the", "a", "an", "with", "and", "of", "for", "to", "at", "on", "in", "meeting", "call"}

SECOND_PASS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["issues"],
    "properties": {"issues": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["claim", "fact"],
        "properties": {"claim": {"type": "string"}, "fact": {"type": "string"}}}}},
}


def _minutes(match: re.Match) -> int:
    if match.group(4) is not None:
        return int(match.group(4)) * 60 + int(match.group(5))
    hour, minute, half = int(match.group(1)) % 12, int(match.group(2) or 0), match.group(3).lower()
    return (hour + (12 if half == "p" else 0)) * 60 + minute


def _clock(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    return f"{(hour % 12) or 12}{':%02d' % minute if minute else ''}{'pm' if hour >= 12 else 'am'}"


def _mentions(sentence: str, title: str) -> bool:
    sentence_l = sentence.lower()
    title_l = title.lower().strip()
    if len(title_l) >= 3 and title_l in sentence_l:
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", title_l) if len(w) > 2 and w not in _STOP]
    return len(words) >= 2 and all(re.search(rf"\b{re.escape(w)}", sentence_l) for w in words)


def deterministic(text: str, events: list[dict[str, Any]], timezone: str = "") -> list[dict[str, Any]]:
    """Issues where the text names a stored event but states a different time, or skips its cancellation."""
    issues: list[dict[str, Any]] = []
    tz = zone(timezone)
    for sentence in _SENTENCE.split(text or ""):
        stated = [_minutes(m) for m in _TIME.finditer(sentence)]
        for event in events:
            if not _mentions(sentence, event.get("title", "")):
                continue
            start = datetime.fromtimestamp(event["start"], tz)
            end = datetime.fromtimestamp(event["end"], tz)
            actual = {start.hour * 60 + start.minute, end.hour * 60 + end.minute}
            if event.get("status") == "cancelled" and "cancel" not in sentence.lower():
                issues.append({"claim": sentence.strip()[:200], "fact": f"{event['title']} was cancelled",
                               "entity": "events", "id": event["id"]})
                continue
            wrong = [m for m in stated if m not in actual]
            if stated and len(wrong) == len(stated):
                issues.append({"claim": f"says {_clock(wrong[0])} for {event['title']}",
                               "fact": f"the calendar says {_clock(start.hour * 60 + start.minute)}",
                               "entity": "events", "id": event["id"]})
    return issues


class FactCheck:
    def __init__(self, app: "WeeboApp"):
        self.app = app

    def _events(self, horizon_days: int = 8) -> list[dict[str, Any]]:
        now = time.time()
        return self.app.facts.repository.query("events", start=now - 86400, end=now + horizon_days * 86400, limit=500)

    def check(self, text: str) -> list[dict[str, Any]]:
        return deterministic(text, self._events(), self.app.settings.get("user.timezone"))

    def referenced(self, text: str) -> list[dict[str, Any]]:
        """Stored facts the text talks about (what a second pass needs as evidence)."""
        refs = [e for e in self._events() if any(_mentions(s, e.get("title", "")) for s in _SENTENCE.split(text or ""))]
        for commitment in self.app.facts.repository.query("commitments", status="open", limit=100):
            words = [w for w in re.findall(r"[a-z0-9]+", commitment["text"].lower()) if len(w) > 3][:4]
            if words and all(w in (text or "").lower() for w in words):
                refs.append(commitment)
        return refs

    @staticmethod
    def note(issues: list[dict[str, Any]]) -> str:
        return "Fact-check: " + "; ".join(f"{i['claim']}, but {i['fact']}" for i in issues[:5]) + "."

    async def second_pass(self, text: str) -> list[dict[str, Any]]:
        """A cheap model pass on the fast tier, only when the text makes claims about stored facts."""
        if not self.app.settings.get("facts.second_pass"):
            return []
        evidence = self.referenced(text)
        if not evidence:
            return []
        if self.app.heartbeat.budget()[0] is False:
            return []
        from ..codex.oneshot import OneShotError, ask
        tz = self.app.settings.get("user.timezone")
        facts = [{k: v for k, v in row.items() if k not in ("raw", "provenance")} for row in evidence[:30]]
        for row in facts:
            for key in ("start", "end", "due"):
                if isinstance(row.get(key), (int, float)):
                    row[key] = datetime.fromtimestamp(row[key], zone(tz)).strftime("%a %b %d %I:%M %p %Z")
        prompt = ("Compare each factual claim in TEXT about events, times, people or commitments with FACTS (the "
                  "source of truth). List only real mismatches; an empty list if everything agrees. Ignore opinions "
                  f"and claims FACTS don't cover.\n\nFACTS:\n{json.dumps(facts, default=str)[:12000]}\n\n"
                  f"TEXT:\n{text[:6000]}")
        engine = self.app.engine
        try:
            reply = await ask(engine, prompt, model=self.app.bots.resolve_model("fast"), effort="low",
                              schema=SECOND_PASS_SCHEMA, timeout=120, service_name="weebo-factcheck",
                              developer_instructions="You are a strict, literal fact checker. Output JSON only.")
            self.app.count_background_turn("factcheck")
            from ..brain.mind import extract_json
            return [i for i in (extract_json(reply) or {}).get("issues", []) if i.get("claim") and i.get("fact")][:5]
        except (OneShotError, ValueError, Exception) as exc:  # a failed check is reported, never fatal
            logger.info("Second-pass fact check skipped: %s", exc)
            return []

    async def review_turn(self, conv_id: str, message_id: str, text: str) -> None:
        """After a brief or draft is written: run the second pass and flag what it finds under the text."""
        issues = await self.second_pass(text)
        if not issues:
            return
        store = self.app.store
        note = store.add_message(conv_id, "event", self.note(issues), kind="factcheck",
                                 data={"issues": issues, "message_id": message_id, "pass": "second"})
        self.app.bus.publish("conv.message", {"conversation_id": conv_id, "message": note})
        store.journal("facts.factcheck", "Second-pass fact check flagged a mismatch", note["content"][:400])
