from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_assistant.core.context_compression import (
    CONTEXT_METADATA_KEY,
    ContextCompressor,
    compact_execution_history,
)
from ai_assistant.core.controller import SystemController


class FakeChatManager:
    def __init__(self, session_id, history):
        self.session_id = session_id
        self.session = {"id": session_id, "history": list(history), "metadata": {}}
        self.update_calls = 0

    def get_session(self, session_id):
        return self.session if session_id == self.session_id else None

    def update_session_metadata(self, session_id, metadata):
        assert session_id == self.session_id
        self.session.setdefault("metadata", {}).update(metadata)
        self.update_calls += 1
        return self.session


def _long_history(count=20, width=700):
    return [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"message-{index} " + (chr(97 + index % 20) * width),
        }
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_long_history_is_summarized_persisted_and_reused():
    history = _long_history()
    history[3]["content"] = r"Decision: use C:\Projects\Orion\main.py and preserve approval checks. " + ("x" * 700)
    chat_manager = FakeChatManager("session-1", history)
    summary_text = (
        "USER FACTS AND PREFERENCES\nUser values approval checks.\n"
        "DECISIONS AND COMMITMENTS\nUse C:\\Projects\\Orion\\main.py.\n"
        "ARTIFACTS, FILE PATHS, TOOL RESULTS, AND ERRORS\nC:\\Projects\\Orion\\main.py"
    )
    router = SimpleNamespace(generate_response=AsyncMock(return_value=summary_text))
    compressor = ContextCompressor(
        chat_manager,
        llm_router=router,
        trigger_tokens=2000,
        max_prepared_tokens=4000,
        recent_message_count=4,
    )

    prepared = await compressor.prepare_history("session-1", history)

    assert prepared[0]["role"] == "system"
    assert "COMPRESSED PRIOR CONVERSATION" in prepared[0]["content"]
    assert r"C:\Projects\Orion\main.py" in prepared[0]["content"]
    assert prepared[-4:] == history[-4:]
    compression = chat_manager.session["metadata"][CONTEXT_METADATA_KEY]
    assert compression["summarized_message_count"] == len(history) - 4
    assert compression["compression_count"] == 1
    assert router.generate_response.await_count == 1

    prepared_again = await compressor.prepare_history("session-1", history)

    assert prepared_again == prepared
    assert router.generate_response.await_count == 1
    assert chat_manager.update_calls == 1


@pytest.mark.asyncio
async def test_failed_llm_compression_uses_detail_preserving_fallback():
    history = _long_history()
    history[5]["content"] = (
        r"Approved decision: edit C:\Work\Self Evolving AI\agent.py. "
        "Previous attempt failed with TypeError and must not be repeated."
    )
    chat_manager = FakeChatManager("session-2", history)
    router = SimpleNamespace(generate_response=AsyncMock(side_effect=RuntimeError("provider down")))
    compressor = ContextCompressor(
        chat_manager,
        llm_router=router,
        trigger_tokens=2000,
        max_prepared_tokens=4000,
        recent_message_count=4,
    )

    prepared = await compressor.prepare_history("session-2", history)
    summary = prepared[0]["content"]

    assert "ROLLING SUMMARY FALLBACK" in summary
    assert r"C:\Work\Self Evolving AI\agent.py" in summary
    assert "TypeError" in summary
    assert prepared[-4:] == history[-4:]


@pytest.mark.asyncio
async def test_very_large_history_is_compressed_in_bounded_chunks():
    history = _long_history(count=24, width=1200)
    chat_manager = FakeChatManager("session-chunked", history)
    router = SimpleNamespace(
        generate_response=AsyncMock(return_value="USER FACTS AND PREFERENCES\nNo new facts.\nCONVERSATION NARRATIVE\nProgress retained.")
    )
    compressor = ContextCompressor(
        chat_manager,
        llm_router=router,
        trigger_tokens=2000,
        max_prepared_tokens=4000,
        recent_message_count=4,
        summarization_chunk_tokens=1000,
    )

    prepared = await compressor.prepare_history("session-chunked", history)

    assert router.generate_response.await_count > 1
    for call in router.generate_response.await_args_list:
        assert "NEW OLDER MESSAGES TO ABSORB" in call.kwargs["prompt"]
    assert prepared[-4:] == history[-4:]


@pytest.mark.asyncio
async def test_prepared_budget_keeps_latest_message_complete_enough():
    history = _long_history(count=8, width=2500)
    history[-1]["content"] = "LATEST CRITICAL REQUEST " + ("z" * 6000)
    compressor = ContextCompressor(
        chat_manager=None,
        trigger_tokens=2000,
        max_prepared_tokens=2500,
    )

    prepared = await compressor.prepare_history(None, history)

    assert prepared[-1]["content"].startswith("LATEST CRITICAL REQUEST")
    assert len(prepared) < len(history)
    assert compressor._messages_token_count(prepared) <= compressor.max_prepared_tokens


def test_execution_history_compaction_preserves_failures_and_recent_cycles():
    history = ""
    for cycle in range(1, 8):
        outcome = "Error: permission denied" if cycle == 3 else f"Result: success-{cycle}"
        history += (
            f"Cycle {cycle}:\nAction: tool_{cycle}\n{outcome}\n"
            + ("payload " * 700)
            + "\n"
        )

    compacted = compact_execution_history(history, max_tokens=1800)

    assert "COMPRESSED EARLIER EXECUTION HISTORY" in compacted
    assert "permission denied" in compacted
    assert "Cycle 6:" in compacted
    assert "Cycle 7:" in compacted


@pytest.mark.asyncio
async def test_controller_passes_compressed_history_to_orchestrator():
    prepared = [{"role": "system", "content": "compressed"}, {"role": "user", "content": "latest"}]
    compressor = SimpleNamespace(prepare_history=AsyncMock(return_value=prepared))
    captured = {}

    class FakeOrchestrator:
        async def process_prompt(self, **kwargs):
            captured.update(kwargs)
            kwargs["state"].current_status = "completed"
            return kwargs["state"]

    controller = SystemController(FakeOrchestrator(), context_compressor=compressor)
    original = [{"role": "user", "content": "old"}, {"role": "user", "content": "latest"}]

    state = await controller.handle_user_request(
        "latest",
        conversation_history=original,
        session_id="session-3",
    )

    assert captured["conversation_history"] == prepared
    assert state.context_limits["history_messages_before"] == 2
    assert state.context_limits["history_messages_after"] == 2
