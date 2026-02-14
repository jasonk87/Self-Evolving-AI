# ai_assistant/core/telemetry.py
import time
import json
import logging
import asyncio
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class TokenUsageTracker:
    """Singleton for tracking LLM token usage (estimated) across the system."""

    _instance = None
    _total_input_tokens = 0
    _total_output_tokens = 0
    _total_calls = 0
    _history: List[Dict[str, Any]] = []

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenUsageTracker, cls).__new__(cls)
        return cls._instance

    @classmethod
    def track_call(cls, model: str, prompt_len: int, response_len: int, task: str = "unknown"):
        """
        Tracks a single LLM call. Estimates tokens as chars / 4 (rough heuristic).
        """
        est_input = prompt_len // 4
        est_output = response_len // 4

        cls._total_input_tokens += est_input
        cls._total_output_tokens += est_output
        cls._total_calls += 1

        entry = {
            "timestamp": time.time(),
            "model": model,
            "task": task,
            "input_tokens": est_input,
            "output_tokens": est_output,
            "total_tokens": est_input + est_output
        }
        cls._history.append(entry)

        # Keep history manageable
        if len(cls._history) > 1000:
            cls._history = cls._history[-1000:]

        logger.debug(f"Telemetry: Tracked call ({est_input} in, {est_output} out) for task '{task}'")

    @classmethod
    def get_usage(cls) -> Dict[str, Any]:
        """Returns current usage statistics."""
        return {
            "total_calls": cls._total_calls,
            "total_input_tokens": cls._total_input_tokens,
            "total_output_tokens": cls._total_output_tokens,
            "total_tokens": cls._total_input_tokens + cls._total_output_tokens,
            "estimated_cost": cls._estimate_cost()
        }

    @classmethod
    def _estimate_cost(cls) -> float:
        """Estimates cost based on rough Gemini Flash pricing ($0.075/1M input, $0.3/1M output)."""
        input_cost = (cls._total_input_tokens / 1_000_000) * 0.075
        output_cost = (cls._total_output_tokens / 1_000_000) * 0.30
        return round(input_cost + output_cost, 6)

# Global Instance
telemetry_tracker = TokenUsageTracker()
