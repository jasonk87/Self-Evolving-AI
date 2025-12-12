import asyncio
import datetime
import pytest
from unittest.mock import AsyncMock
from ai_assistant.custom_tools.generated.remind_at_3_40 import remind_at_3_40


@pytest.mark.asyncio
async def test_remind_at_3_40_happy_path(monkeypatch):
    """Test the normal operation of remind_at_3_40."""
    callback_mock = AsyncMock()

    # Mock datetime.datetime.now() to return a time close to 3:40 PM
    now = datetime.datetime.now()
    target_time = now.replace(hour=15, minute=39, second=59, microsecond=0)
    monkeypatch.setattr(datetime, 'datetime', type('datetime', (datetime.datetime,), {'now': classmethod(lambda cls: target_time)}))

    await remind_at_3_40(callback_mock)

    # Assert that the callback was called
    callback_mock.assert_called_once()


@pytest.mark.asyncio
async def test_remind_at_3_40_already_past_3_40(monkeypatch):
    """Test when the current time is already past 3:40 PM."""
    callback_mock = AsyncMock()

    # Mock datetime.datetime.now() to return a time past 3:40 PM
    now = datetime.datetime.now()
    target_time = now.replace(hour=15, minute=40, second=1, microsecond=0)
    monkeypatch.setattr(datetime, 'datetime', type('datetime', (datetime.datetime,), {'now': classmethod(lambda cls: target_time)}))

    await remind_at_3_40(callback_mock)

    # Assert that the callback was called
    callback_mock.assert_called_once()


@pytest.mark.asyncio
async def test_remind_at_3_40_callback_raises_exception(monkeypatch):
    """Test when the callback function raises an exception."""
    async def callback_with_exception():
        raise ValueError("Callback failed")

    # Mock datetime.datetime.now() to return a time close to 3:40 PM
    now = datetime.datetime.now()
    target_time = now.replace(hour=15, minute=39, second=59, microsecond=0)
    monkeypatch.setattr(datetime, 'datetime', type('datetime', (datetime.datetime,), {'now': classmethod(lambda cls: target_time)}))

    with pytest.raises(ValueError, match="Callback failed"):
        await remind_at_3_40(callback_with_exception)


@pytest.mark.asyncio
async def test_remind_at_3_40_midnight(monkeypatch):
    """Test when the current time is close to midnight."""
    callback_mock = AsyncMock()

    # Mock datetime.datetime.now() to return a time close to midnight
    now = datetime.datetime.now()
    target_time = now.replace(hour=0, minute=0, second=1, microsecond=0)
    monkeypatch.setattr(datetime, 'datetime', type('datetime', (datetime.datetime,), {'now': classmethod(lambda cls: target_time)}))

    await remind_at_3_40(callback_mock)

    # Assert that the callback was called
    callback_mock.assert_called_once()


@pytest.mark.asyncio
async def test_remind_at_3_40_early_morning(monkeypatch):
    """Test when the current time is in the early morning."""
    callback_mock = AsyncMock()

    # Mock datetime.datetime.now() to return a time in the early morning
    now = datetime.datetime.now()
    target_time = now.replace(hour=3, minute=39, second=59, microsecond=0)
    monkeypatch.setattr(datetime, 'datetime', type('datetime', (datetime.datetime,), {'now': classmethod(lambda cls: target_time)}))

    await remind_at_3_40(callback_mock)

    # Assert that the callback was called
    callback_mock.assert_called_once()