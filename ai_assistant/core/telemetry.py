# ai_assistant/core/telemetry.py
import time
import logging
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

    # SLO Metrics
    _total_diagnose_time = 0.0
    _diagnose_count = 0
    _manual_retry_count = 0
    _delegated_task_times = []

    @classmethod
    def record_slo_metric(cls, metric_name: str, value: float):
        if metric_name == "mttd":
            cls._total_diagnose_time += value
            cls._diagnose_count += 1
        elif metric_name == "manual_retry":
            cls._manual_retry_count += 1
        elif metric_name == "delegated_task_latency":
            cls._delegated_task_times.append(value)
            if len(cls._delegated_task_times) > 100:
                cls._delegated_task_times.pop(0)

    @classmethod
    def get_slo_metrics(cls) -> Dict[str, Any]:
        mttd = cls._total_diagnose_time / cls._diagnose_count if cls._diagnose_count > 0 else 0
        avg_latency = sum(cls._delegated_task_times) / len(cls._delegated_task_times) if cls._delegated_task_times else 0

        return {
            "mttd_seconds": round(mttd, 2),
            "manual_retries": cls._manual_retry_count,
            "avg_delegated_latency_seconds": round(avg_latency, 2),
            "total_diagnostics_run": cls._diagnose_count
        }

    @classmethod
    def get_usage(cls) -> Dict[str, Any]:
        """Returns current usage statistics."""
        return {
            "total_calls": cls._total_calls,
            "total_input_tokens": cls._total_input_tokens,
            "total_output_tokens": cls._total_output_tokens,
            "total_tokens": cls._total_input_tokens + cls._total_output_tokens,
            "estimated_cost": cls._estimate_cost(),
            "slo": cls.get_slo_metrics()
        }

    @classmethod
    def get_history(cls, limit: int = 200) -> List[Dict[str, Any]]:
        """Returns recent token telemetry history, newest-first."""
        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            limit_value = 200
        limit_value = max(1, min(limit_value, 2000))
        return list(reversed(cls._history[-limit_value:]))

    @classmethod
    def _estimate_cost(cls) -> float:
        """Estimates cost based on rough Gemini Flash pricing ($0.075/1M input, $0.3/1M output)."""
        input_cost = (cls._total_input_tokens / 1_000_000) * 0.075
        output_cost = (cls._total_output_tokens / 1_000_000) * 0.30
        return round(input_cost + output_cost, 6)

# Global Instance
telemetry_tracker = TokenUsageTracker()
