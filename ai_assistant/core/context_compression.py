"""Persistent, token-aware context compression for long-running conversations."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

from ai_assistant.core.llm.router import model_router
from ai_assistant.utils.token_counter import estimate_tokens


logger = logging.getLogger(__name__)

CONTEXT_METADATA_KEY = "context_compression"
SUMMARY_VERSION = 1


def _clip_text_preserving_ends(text: str, max_tokens: int) -> str:
    """Fit text without silently discarding either its beginning or latest tail."""
    value = str(text or "")
    if estimate_tokens(value) <= max_tokens:
        return value
    char_budget = min(len(value), max(200, max_tokens * 4))
    marker = "\n\n[...context compressed...]\n\n"
    for _ in range(8):
        head_chars = int(char_budget * 0.68)
        tail_chars = max(1, char_budget - head_chars)
        candidate = value[:head_chars].rstrip() + marker + value[-tail_chars:].lstrip()
        measured = estimate_tokens(candidate)
        if measured <= max_tokens:
            return candidate
        char_budget = max(100, int(char_budget * (max_tokens / max(measured, 1)) * 0.9))
    return value[:80].rstrip() + marker + value[-80:].lstrip()


def compact_transcript_text(transcript: str, max_tokens: int) -> str:
    """Compact a formatted transcript while retaining early summary and recent turns."""
    return _clip_text_preserving_ends(transcript, max_tokens)


def compact_execution_history(execution_history: str, max_tokens: int = 5000) -> str:
    """Reduce ReAct history while retaining actions, outcomes, errors, and recent evidence."""
    text = str(execution_history or "")
    if estimate_tokens(text) <= max_tokens:
        return text

    blocks = [block.strip() for block in re.split(r"(?=Cycle\s+\d+:)", text) if block.strip()]
    if len(blocks) <= 2:
        return _clip_text_preserving_ends(text, max_tokens)

    older_blocks = blocks[:-2]
    recent_blocks = blocks[-2:]
    compacted_older = []
    important_line = re.compile(
        r"(^Cycle\s+\d+:|action|tool|result|error|failed|success|quality gate|control observation)",
        re.IGNORECASE,
    )
    for block in older_blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        selected = [line for line in lines if important_line.search(line)]
        if not selected and lines:
            selected = lines[:2]
        compacted_older.append(_clip_text_preserving_ends("\n".join(selected), 450))

    result = (
        "[COMPRESSED EARLIER EXECUTION HISTORY]\n"
        + "\n\n".join(compacted_older)
        + "\n\n[RECENT EXECUTION HISTORY - VERBATIM]\n"
        + "\n\n".join(recent_blocks)
    )
    return _clip_text_preserving_ends(result, max_tokens)


class ContextCompressor:
    """Creates and persists a rolling summary while keeping recent turns verbatim."""

    def __init__(
        self,
        chat_manager: Any,
        *,
        llm_router: Any = model_router,
        trigger_tokens: int = 16000,
        max_prepared_tokens: int = 24000,
        summary_max_tokens: int = 4000,
        recent_message_count: int = 12,
        summarization_chunk_tokens: int = 60000,
    ):
        self.chat_manager = chat_manager
        self.llm_router = llm_router
        self.trigger_tokens = max(2000, int(trigger_tokens))
        self.max_prepared_tokens = max(self.trigger_tokens, int(max_prepared_tokens))
        self.summary_max_tokens = max(500, int(summary_max_tokens))
        self.recent_message_count = max(4, int(recent_message_count))
        self.summarization_chunk_tokens = max(1000, int(summarization_chunk_tokens))
        self._locks: Dict[str, asyncio.Lock] = {}

    async def prepare_history(
        self,
        session_id: Optional[str],
        history: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        normalized = self._normalize_history(history)
        if not normalized:
            return []
        if not session_id or not self.chat_manager:
            return self._fit_messages_to_budget(normalized)

        lock = self._locks.setdefault(str(session_id), asyncio.Lock())
        async with lock:
            return await self._prepare_locked(str(session_id), normalized)

    async def _prepare_locked(
        self,
        session_id: str,
        history: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        session = self.chat_manager.get_session(session_id) or {}
        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
        compression = metadata.get(CONTEXT_METADATA_KEY)
        compression = compression if isinstance(compression, dict) else {}

        prior_summary = str(compression.get("summary") or "").strip()
        try:
            summarized_count = int(compression.get("summarized_message_count") or 0)
        except (TypeError, ValueError):
            summarized_count = 0
        if summarized_count < 0 or summarized_count > len(history):
            prior_summary = ""
            summarized_count = 0

        prepared = self._with_summary(prior_summary, history[summarized_count:])
        if self._messages_token_count(prepared) <= self.trigger_tokens:
            return self._fit_messages_to_budget(prepared)

        cutoff = max(summarized_count, len(history) - self.recent_message_count)
        if cutoff > summarized_count:
            older_messages = history[summarized_count:cutoff]
            summary = await self._summarize(prior_summary, older_messages)
            summarized_count = cutoff
            compression_count = int(compression.get("compression_count") or 0) + 1
            compression = {
                "version": SUMMARY_VERSION,
                "summary": summary,
                "summarized_message_count": summarized_count,
                "compression_count": compression_count,
                "updated_at": time.time(),
                "source_message_count": len(history),
                "source_tokens": self._messages_token_count(history),
            }
            try:
                self.chat_manager.update_session_metadata(
                    session_id,
                    {CONTEXT_METADATA_KEY: compression},
                )
                logger.info(
                    "Compressed session context %s: %s messages summarized, %s recent messages retained.",
                    session_id,
                    summarized_count,
                    len(history) - summarized_count,
                )
            except Exception as exc:
                logger.warning("Could not persist compressed context for %s: %s", session_id, exc)
            prepared = self._with_summary(summary, history[summarized_count:])

        return self._fit_messages_to_budget(prepared)

    async def _summarize(self, prior_summary: str, messages: List[Dict[str, Any]]) -> str:
        rolling_summary = prior_summary
        for chunk in self._chunk_messages(messages, self.summarization_chunk_tokens):
            rolling_summary = await self._summarize_chunk(rolling_summary, chunk)
        return rolling_summary or self._deterministic_summary(prior_summary, messages)

    async def _summarize_chunk(self, prior_summary: str, messages: List[Dict[str, Any]]) -> str:
        transcript = self._format_messages(messages)
        prompt = f"""Maintain a dense, loss-resistant rolling summary of a conversation.

PRIOR ROLLING SUMMARY:
{prior_summary or "None yet."}

NEW OLDER MESSAGES TO ABSORB:
{transcript}

Return plain text with these exact sections:
USER FACTS AND PREFERENCES
GOALS AND CONSTRAINTS
DECISIONS AND COMMITMENTS
ACTIVE WORK AND STATUS
ARTIFACTS, FILE PATHS, TOOL RESULTS, AND ERRORS
UNRESOLVED QUESTIONS
CONVERSATION NARRATIVE

Rules:
- Preserve exact names, numbers, paths, identifiers, decisions, corrections, and user constraints.
- Preserve failures and unsuccessful attempts so they are not repeated.
- Distinguish confirmed facts from guesses or unresolved claims.
- Merge the prior summary with the new messages; do not merely summarize the newest message.
- Be dense and factual. Do not add commentary or invent information.
"""
        try:
            summary = await self.llm_router.generate_response(
                prompt=prompt,
                task_name="summarization",
                temperature=0.1,
                # Thinking tokens share the completion allowance. Give the
                # summarizer room to reason, then enforce the persisted summary
                # budget after generation.
                max_tokens=max(8192, self.summary_max_tokens),
            )
            summary = str(summary or "").strip()
            if len(summary) >= 40:
                return _clip_text_preserving_ends(summary, self.summary_max_tokens)
        except Exception as exc:
            logger.warning("LLM context compression failed; using deterministic fallback: %s", exc)
        return self._deterministic_summary(prior_summary, messages)

    @classmethod
    def _chunk_messages(
        cls,
        messages: List[Dict[str, Any]],
        max_tokens: int,
    ) -> List[List[Dict[str, Any]]]:
        expanded = []
        for message in messages:
            expanded.extend(cls._split_message_to_fit(message, max_tokens))

        chunks: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        current_tokens = 0
        for message in expanded:
            message_tokens = estimate_tokens(str(message.get("content") or "")) + 8
            if current and current_tokens + message_tokens > max_tokens:
                chunks.append(current)
                current = []
                current_tokens = 0
            current.append(message)
            current_tokens += message_tokens
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _split_message_to_fit(message: Dict[str, Any], max_tokens: int) -> List[Dict[str, Any]]:
        content = str(message.get("content") or "")
        if estimate_tokens(content) + 8 <= max_tokens:
            return [message]

        parts = []
        remaining = content
        part_number = 1
        content_budget = max(200, max_tokens - 24)
        while remaining:
            low = 1
            high = len(remaining)
            best = 1
            while low <= high:
                midpoint = (low + high) // 2
                if estimate_tokens(remaining[:midpoint]) <= content_budget:
                    best = midpoint
                    low = midpoint + 1
                else:
                    high = midpoint - 1
            item = dict(message)
            item["content"] = (
                f"[Continuation part {part_number}]\n" + remaining[:best]
            )
            parts.append(item)
            remaining = remaining[best:]
            part_number += 1
        return parts

    def _deterministic_summary(
        self,
        prior_summary: str,
        messages: List[Dict[str, Any]],
    ) -> str:
        critical = re.compile(
            r"(prefer|remember|name|goal|must|should|decid|approved|denied|error|failed|"
            r"success|todo|unresolved|file|path|project|[A-Za-z]:\\|/[^\s]+/[^\s]+)",
            re.IGNORECASE,
        )
        selected = []
        for index, message in enumerate(messages):
            content = str(message.get("content") or "")
            if index < 2 or index >= len(messages) - 4 or critical.search(content):
                selected.append({**message, "content": _clip_text_preserving_ends(content, 350)})
        fallback = (
            "ROLLING SUMMARY FALLBACK (verbatim evidence; LLM summary unavailable)\n"
            + (f"PRIOR SUMMARY:\n{prior_summary}\n\n" if prior_summary else "")
            + self._format_messages(selected)
        )
        return _clip_text_preserving_ends(fallback, self.summary_max_tokens)

    def _fit_messages_to_budget(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self._messages_token_count(messages) <= self.max_prepared_tokens:
            return messages

        summary_message = messages[0] if messages and messages[0].get("_compressed_context") else None
        recent = messages[1:] if summary_message else messages
        selected_reversed = []
        reserved = estimate_tokens(str(summary_message.get("content") or "")) if summary_message else 0
        remaining = max(500, self.max_prepared_tokens - reserved)

        for message in reversed(recent):
            message_tokens = estimate_tokens(str(message.get("content") or "")) + 8
            if message_tokens <= remaining:
                selected_reversed.append(message)
                remaining -= message_tokens
                continue
            if not selected_reversed:
                clipped = dict(message)
                clipped["content"] = _clip_text_preserving_ends(
                    str(message.get("content") or ""),
                    max(300, remaining - 8),
                )
                selected_reversed.append(clipped)
            break

        selected = list(reversed(selected_reversed))
        if summary_message:
            clipped_summary = dict(summary_message)
            clipped_summary["content"] = _clip_text_preserving_ends(
                str(summary_message.get("content") or ""),
                min(self.summary_max_tokens, max(500, self.max_prepared_tokens // 3)),
            )
            selected.insert(0, clipped_summary)
        return selected

    @staticmethod
    def _normalize_history(history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        normalized = []
        for message in history or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").casefold()
            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"
            item = dict(message)
            item["role"] = role
            item["content"] = str(message.get("content") or "")
            normalized.append(item)
        return normalized

    @staticmethod
    def _with_summary(summary: str, recent: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not summary:
            return list(recent)
        return [{
            "role": "system",
            "content": (
                "COMPRESSED PRIOR CONVERSATION\n"
                "Treat this as authoritative context retained from older turns. "
                "Recent verbatim messages follow.\n\n"
                + summary
            ),
            "_compressed_context": True,
        }] + list(recent)

    @staticmethod
    def _format_messages(messages: List[Dict[str, Any]]) -> str:
        parts = []
        for index, message in enumerate(messages):
            role = str(message.get("role") or "user").upper()
            content = str(message.get("content") or "")
            images = message.get("images") or []
            image_note = f"\n[Attached images: {len(images)}]" if images else ""
            parts.append(f"MESSAGE {index + 1} - {role}:\n{content}{image_note}")
        return "\n\n".join(parts)

    @staticmethod
    def _messages_token_count(messages: List[Dict[str, Any]]) -> int:
        return sum(
            estimate_tokens(str(message.get("content") or "")) + 8
            for message in messages
        )
