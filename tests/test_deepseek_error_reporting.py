import asyncio

import pytest

from ai_assistant.llm_interface import deepseek_client


class _FailingRequestContext:
    def __init__(self, error):
        self.error = error

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, error):
        self.error = error
        self.post_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        self.post_count += 1
        return _FailingRequestContext(self.error)


@pytest.mark.asyncio
async def test_timeout_retries_and_reports_nonempty_diagnostic(monkeypatch):
    fake_session = _FakeSession(asyncio.TimeoutError())
    monkeypatch.setattr(deepseek_client, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(deepseek_client, "DEEPSEEK_REQUEST_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(deepseek_client.aiohttp, "ClientSession", lambda: fake_session)

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(deepseek_client.asyncio, "sleep", no_sleep)

    with pytest.raises(deepseek_client.DeepseekError) as exc_info:
        await deepseek_client.invoke_raw_deepseek_async(
            [{"role": "user", "content": "test"}],
            model_name="deepseek-v4-flash",
        )

    assert fake_session.post_count == 3
    assert str(exc_info.value) == "Deepseek request timed out after 1 seconds (3 attempts)."


@pytest.mark.asyncio
async def test_blank_unexpected_exception_includes_exception_type(monkeypatch):
    fake_session = _FakeSession(RuntimeError())
    monkeypatch.setattr(deepseek_client, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(deepseek_client.aiohttp, "ClientSession", lambda: fake_session)

    with pytest.raises(deepseek_client.DeepseekError) as exc_info:
        await deepseek_client.invoke_raw_deepseek_async(
            [{"role": "user", "content": "test"}],
            model_name="deepseek-v4-flash",
        )

    assert "RuntimeError" in str(exc_info.value)
    assert not str(exc_info.value).endswith(": ")
