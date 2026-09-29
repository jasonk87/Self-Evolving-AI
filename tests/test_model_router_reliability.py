import pytest

from ai_assistant.core.llm.router import ModelRouter


class FakeProvider:
    def __init__(self, name, result=None, error=None):
        self._name = name
        self.result = result
        self.error = error
        self.calls = []

    @property
    def provider_name(self):
        return self._name

    async def generate_response(self, prompt, **kwargs):
        kwargs["prompt"] = prompt
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_router_uses_gemini_fallback_and_reports_route(monkeypatch):
    router = ModelRouter()
    primary = FakeProvider("deepseek", error=RuntimeError("provider down"))
    fallback = FakeProvider("gemini", result="recovered")
    router._providers = {"deepseek": primary, "gemini": fallback}
    monkeypatch.setattr(router, "get_route", lambda task: (primary, "deepseek-v4-flash", "DIRECT", None))

    result = await router.generate_response("hello", task_name="chat")

    assert result == "recovered"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    assert router.get_last_call_info()["provider"] == "gemini"
    assert router.get_last_call_info()["fallback"] is True


@pytest.mark.asyncio
async def test_router_sends_images_directly_to_gemini_fallback(monkeypatch):
    router = ModelRouter()
    primary = FakeProvider("deepseek", result="should not be called")
    fallback = FakeProvider("gemini", result="vision result")
    router._providers = {"deepseek": primary, "gemini": fallback}
    monkeypatch.setattr(router, "get_route", lambda task: (primary, "deepseek-v4-flash", "DIRECT", None))

    result = await router.generate_response("describe", task_name="chat", images=["abc"])

    assert result == "vision result"
    assert primary.calls == []
    assert fallback.calls[0]["images"] == ["abc"]
    assert router.get_last_call_info()["model"] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_router_treats_empty_primary_response_as_failure(monkeypatch):
    router = ModelRouter()
    primary = FakeProvider("deepseek", result="")
    fallback = FakeProvider("gemini", result="recovered")
    router._providers = {"deepseek": primary, "gemini": fallback}
    monkeypatch.setattr(router, "get_route", lambda task: (primary, "deepseek-v4-flash", "DIRECT", None))

    result = await router.generate_response("hello", task_name="fact_management")

    assert result == "recovered"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    assert router.get_last_call_info()["fallback"] is True
