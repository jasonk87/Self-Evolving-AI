"""Email facts from raw connector responses, never from what a model says about them.

When a bot (or the chief) reads mail through a connector, Codex reports the tool call's raw result. Weebo parses
thread records out of that result and stores them as provenanced facts, so "threads needing a reply" come from the
mailbox itself. Records without a real timestamp are skipped rather than guessed.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from .. import log

logger = log.get("mail_ingest")

MAIL_HINT = re.compile(r"mail|gmail|outlook|inbox|thread|message", re.IGNORECASE)


def _when(record: dict[str, Any]) -> float | None:
    for key in ("internalDate", "internal_date"):
        if record.get(key):
            try:
                return float(record[key]) / 1000
            except (TypeError, ValueError):
                pass
    for key in ("last_message_at", "timestamp", "date", "receivedDateTime", "sent_at", "received_at"):
        value = record.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (int, float)):
            return float(value) / (1000 if value > 10**11 else 1)
        text = str(value)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
        try:
            return parsedate_to_datetime(text).timestamp()
        except (TypeError, ValueError, IndexError):
            pass
    return None


def _people(record: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("participants", "from", "sender", "to", "cc"):
        value = record.get(key)
        values = value if isinstance(value, list) else [value] if value else []
        for item in values:
            if isinstance(item, dict):
                item = item.get("email") or item.get("emailAddress", {}).get("address") or item.get("name")
            if item and str(item) not in out:
                out.append(str(item)[:200])
    return out[:20]


def thread_records(payload: Any) -> list[dict[str, Any]]:
    """Every dict in the payload that looks like a mail thread or message: an id, a subject and a timestamp."""
    found: list[dict[str, Any]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(node, dict):
            external = node.get("threadId") or node.get("thread_id") or node.get("id")
            if external and isinstance(node.get("subject"), str):
                when = _when(node)
                if when is not None:
                    labels = [str(label).upper() for label in node.get("labelIds") or node.get("labels") or []]
                    found.append({"external_id": str(external), "subject": node["subject"][:300],
                                  "participants": _people(node), "last_message_at": when,
                                  "needs_reply": bool(node.get("unread") or node.get("is_unread") or "UNREAD" in labels),
                                  "summary": str(node.get("snippet") or node.get("summary") or "")[:300]})
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node[:500]:
                walk(value, depth + 1)

    walk(payload)
    newest: dict[str, dict[str, Any]] = {}
    for record in found:  # several messages of one thread: keep the latest
        current = newest.get(record["external_id"])
        if current is None or record["last_message_at"] >= current["last_message_at"]:
            newest[record["external_id"]] = record
    return list(newest.values())


def ingest(repository, source: str, result: dict[str, Any] | None, ttl_seconds: float = 900) -> int:
    """Store the thread records in a connector's raw result. Returns how many were stored or refreshed."""
    count = 0
    for content in (result or {}).get("content") or []:
        if not isinstance(content, dict) or content.get("type") != "text":
            continue
        try:
            payload = json.loads(content.get("text") or "")
        except (TypeError, json.JSONDecodeError):
            continue
        for record in thread_records(payload):
            try:
                repository.upsert("threads", record, source=source, ttl_seconds=ttl_seconds)
                count += 1
            except ValueError as exc:
                logger.debug("Skipped a mail record: %s", exc)
    return count
