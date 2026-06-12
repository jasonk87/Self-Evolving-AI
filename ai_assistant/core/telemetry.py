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
    _total_thinking_tokens = 0
    _total_calls = 0
    _history: List[Dict[str, Any]] = []

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenUsageTracker, cls).__new__(cls)
        return cls._instance

    @classmethod
    def track_call(
        cls,
        model: str,
        prompt_len: int,
        response_len: int,
        task: str = "unknown",
        usage_metadata: Dict[str, Any] = None,
        thinking_budget: int = None,
    ):
        """
        Tracks a single LLM call. Uses provider metadata when available and
        falls back to chars / 4 for older call sites.
        """
        usage_metadata = usage_metadata or {}
        est_input = usage_metadata.get("promptTokenCount", prompt_len // 4)
        est_output = usage_metadata.get("candidatesTokenCount", response_len // 4)
        thinking_tokens = usage_metadata.get("thoughtsTokenCount", 0) or 0

        cls._total_input_tokens += est_input
        cls._total_output_tokens += est_output
        cls._total_thinking_tokens += thinking_tokens
        cls._total_calls += 1

        entry = {
            "timestamp": time.time(),
            "model": model,
            "task": task,
            "input_tokens": est_input,
            "output_tokens": est_output,
            "thinking_tokens": thinking_tokens,
            "thinking_budget": thinking_budget,
            "total_tokens": est_input + est_output + thinking_tokens
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
            "total_thinking_tokens": cls._total_thinking_tokens,
            "total_tokens": cls._total_input_tokens + cls._total_output_tokens + cls._total_thinking_tokens,
            "model_usage": cls.get_model_usage(),
            "estimated_cost": cls._estimate_cost(),
            "slo": cls.get_slo_metrics()
        }

    @classmethod
    def get_model_usage(cls) -> Dict[str, Any]:
        """Returns token usage grouped by model for UI breakdowns."""
        usage: Dict[str, Any] = {}
        for entry in cls._history:
            model = entry.get("model", "unknown")
            if model not in usage:
                usage[model] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "thinking_tokens": 0,
                    "total_tokens": 0,
                    "calls": 0,
                    "latest_thinking_budget": None,
                }
            usage[model]["input_tokens"] += int(entry.get("input_tokens") or 0)
            usage[model]["output_tokens"] += int(entry.get("output_tokens") or 0)
            usage[model]["thinking_tokens"] += int(entry.get("thinking_tokens") or 0)
            usage[model]["total_tokens"] += int(entry.get("total_tokens") or 0)
            usage[model]["calls"] += 1
            if entry.get("thinking_budget") is not None:
                usage[model]["latest_thinking_budget"] = entry.get("thinking_budget")
        return usage

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
        output_cost = ((cls._total_output_tokens + cls._total_thinking_tokens) / 1_000_000) * 0.30
        return round(input_cost + output_cost, 6)

    @classmethod
    def get_category_costs(cls) -> Dict[str, float]:
        """Calculates estimated cost grouped by task category prefixes."""
        costs = {}
        for entry in cls._history:
            task = entry.get("task", "unknown")
            # Determine prefix, e.g. "ollama_async_cot_research" -> "research"
            parts = task.split('_')
            _prefix = parts[-1] if parts else task

            # For generic tasks, let's look at the first part, or some heuristics
            if "research" in task: category = "research"
            elif "dream" in task: category = "dreaming"
            elif "chat" in task: category = "chat"
            elif "coding" in task or "code" in task: category = "coding"
            else: category = "other"

            est_input = entry.get("input_tokens", 0)
            est_output = entry.get("output_tokens", 0)
            thinking_tokens = entry.get("thinking_tokens", 0)
            cost = ((est_input / 1_000_000) * 0.075) + (((est_output + thinking_tokens) / 1_000_000) * 0.30)

            costs[category] = costs.get(category, 0.0) + cost

        return {k: round(v, 6) for k, v in costs.items()}

    @classmethod
    def check_hard_limit_exceeded(cls, daily_limit: int) -> bool:
        """Checks if the daily limit is exceeded."""
        return (cls._total_input_tokens + cls._total_output_tokens + cls._total_thinking_tokens) > daily_limit

    @classmethod
    def check_category_limit_exceeded(cls, task: str, category_limits: Dict[str, float]) -> bool:
        """Checks if the specific category limit in USD is exceeded."""
        costs = cls.get_category_costs()

        if "research" in task: category = "research"
        elif "dream" in task: category = "dreaming"
        elif "chat" in task: category = "chat"
        elif "coding" in task or "code" in task: category = "coding"
        else: category = "other"

        limit = category_limits.get(category)
        if limit is not None and costs.get(category, 0.0) > limit:
            return True
        return False

# Global Instance
telemetry_tracker = TokenUsageTracker()
