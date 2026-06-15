"""
Tests for `_send_chunks` in `telegram_monitor`.

The helper splits a long report into chunks (max 4000 chars each) and sends
each chunk via `TelegramClient.send_message`. It must:

1. Not pass a `comment` kwarg (Telethon doesn't accept it — the correct
   argument is `comment_to`, used to post into a forum topic).
2. Pass `comment_to=settings.report_to_thread` only when a thread id is set.
3. Send each chunk as a separate `send_message` call.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from telegram_monitor import _send_chunks
import telegram_monitor


class _FakeSettings:
    def __init__(self, report_to="@anastasiz1712", report_to_thread=None):
        self.report_to = report_to
        self.report_to_thread = report_to_thread


@pytest.fixture
def fake_settings(monkeypatch):
    def _set(report_to_thread=None):
        settings = _FakeSettings(report_to_thread=report_to_thread)
        monkeypatch.setattr(telegram_monitor, "settings", settings)
        return settings
    return _set


@pytest.fixture
def fake_client():
    client = SimpleNamespace(send_message=AsyncMock())
    return client


def test_send_chunks_source_does_not_use_comment_kwarg():
    """Guard against regressing the original bug."""
    source = inspect.getsource(_send_chunks)
    assert "comment=" not in source, (
        "send_message does not accept 'comment' in Telethon; "
        "use 'comment_to' (forum topic id) or omit the kwarg."
    )
    assert "comment_to" in source


@pytest.mark.asyncio
async def test_send_chunks_without_thread_does_not_pass_comment_to(fake_settings, fake_client):
    fake_settings(report_to_thread=None)
    # 3 lines of 1500 chars each => 4500 chars total => 2 chunks (max_len=4000)
    big_line = "x" * 1500
    lines = [big_line, big_line, big_line]

    await _send_chunks(fake_client, lines, label="Test")

    assert fake_client.send_message.await_count >= 2
    for call in fake_client.send_message.await_args_list:
        kwargs = call.kwargs
        assert "comment_to" not in kwargs
        assert call.args[0] == "@anastasiz1712"


@pytest.mark.asyncio
async def test_send_chunks_with_thread_passes_comment_to(fake_settings, fake_client):
    fake_settings(report_to_thread=42)
    lines = ["hello"]

    await _send_chunks(fake_client, lines, label="Test")

    assert fake_client.send_message.await_count == 1
    call = fake_client.send_message.await_args
    assert call.kwargs.get("comment_to") == 42
    assert call.args[0] == "@anastasiz1712"
    assert call.args[1] == "hello"


@pytest.mark.asyncio
async def test_send_chunks_splits_long_content(fake_settings, fake_client):
    fake_settings(report_to_thread=None)
    # 4 lines of 1500 chars each -> must split into at least 2 chunks
    # (max_len = 4000, so 3 fit, 4th starts a new chunk).
    big_line = "x" * 1500
    lines = [big_line, big_line, big_line, big_line]

    await _send_chunks(fake_client, lines, label="Test")

    assert fake_client.send_message.await_count >= 2
    for call in fake_client.send_message.await_args_list:
        sent = call.args[1]
        assert len(sent) <= 4000


@pytest.mark.asyncio
async def test_send_chunks_single_short_message(fake_settings, fake_client):
    fake_settings(report_to_thread=None)
    lines = ["short"]

    await _send_chunks(fake_client, lines, label="Test")

    assert fake_client.send_message.await_count == 1
    call = fake_client.send_message.await_args
    assert call.args == ("@anastasiz1712", "short")
    assert call.kwargs == {}
