"""POST /api/chat — SSE streaming. Test with a mocked Anthropic streaming client."""

from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


class _FakeAnthropicMessageStream:
    """Async iterator that yields scripted events. Pattern matches anthropic SDK shape:
    each event has a `.type` and event-specific fields."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for e in self._events:
            yield e


@pytest.mark.asyncio
async def test_chat_streams_plain_text_response(db_session, monkeypatch, test_user_id):
    # Patch the chat route's session factory + Anthropic client so we control both.
    from health_metrics.routes import chat as chat_route

    @asynccontextmanager
    async def _ctx():
        yield db_session
    monkeypatch.setattr(chat_route, "_session_factory", lambda: _ctx())

    # Fake Anthropic stream emits two text deltas then a message_stop
    fake_events = [
        type("E", (), {"type": "content_block_delta", "delta": type("D", (), {"type": "text_delta", "text": "Your HRV "})()})(),
        type("E", (), {"type": "content_block_delta", "delta": type("D", (), {"type": "text_delta", "text": "is low."})()})(),
        type("E", (), {"type": "message_stop"})(),
    ]

    fake_messages = AsyncMock()
    fake_messages.stream = lambda **kw: _FakeAnthropicMessageStream(fake_events)
    fake_client = AsyncMock()
    fake_client.messages = fake_messages
    monkeypatch.setattr(chat_route, "_build_anthropic_client", lambda: fake_client)

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/chat", json={
            "user_id": test_user_id,
            "messages": [{"role": "user", "content": "How am I?"}],
        })

    assert resp.status_code == 200
    body = resp.text
    # SSE events: data: {"type":"text","delta":"Your HRV "}
    assert 'data: {"type":"text","delta":"Your HRV "}' in body
    assert 'data: {"type":"text","delta":"is low."}' in body
    assert 'data: {"type":"done"}' in body


@pytest.mark.asyncio
async def test_chat_emits_tool_use_for_write_then_pauses(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import chat as chat_route

    @asynccontextmanager
    async def _ctx():
        yield db_session
    monkeypatch.setattr(chat_route, "_session_factory", lambda: _ctx())

    fake_events = [
        type("E", (), {"type": "content_block_start", "content_block": type("B", (), {
            "type": "tool_use", "id": "toolu_abc", "name": "log_weight", "input": {}
        })()})(),
        type("E", (), {"type": "content_block_delta", "delta": type("D", (), {
            "type": "input_json_delta", "partial_json": '{"date":"2026-05-17","weight_lbs":218}'
        })()})(),
        type("E", (), {"type": "content_block_stop"})(),
        type("E", (), {"type": "message_stop"})(),
    ]
    fake_messages = AsyncMock()
    fake_messages.stream = lambda **kw: _FakeAnthropicMessageStream(fake_events)
    fake_client = AsyncMock()
    fake_client.messages = fake_messages
    monkeypatch.setattr(chat_route, "_build_anthropic_client", lambda: fake_client)

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/chat", json={
            "user_id": test_user_id,
            "messages": [{"role": "user", "content": "log my weight 218"}],
        })

    assert resp.status_code == 200
    body = resp.text
    assert '"type":"tool_use"' in body
    assert '"name":"log_weight"' in body
    assert '"id":"toolu_abc"' in body
    assert '"weight_lbs":218' in body or '"weight_lbs": 218' in body


@pytest.mark.asyncio
async def test_chat_executes_write_after_confirmation(db_session, monkeypatch, test_user_id):
    """When client posts back with approved=True, the write executes via the tool handler."""
    from health_metrics.routes import chat as chat_route
    from health_metrics.models import ManualLog
    from sqlalchemy import select

    @asynccontextmanager
    async def _ctx():
        yield db_session
    monkeypatch.setattr(chat_route, "_session_factory", lambda: _ctx())

    # After confirmation, Anthropic is called again; mock it to just say "done"
    fake_events = [
        type("E", (), {"type": "content_block_delta", "delta": type("D", (), {"type": "text_delta", "text": "Done."})()})(),
        type("E", (), {"type": "message_stop"})(),
    ]
    fake_messages = AsyncMock()
    fake_messages.stream = lambda **kw: _FakeAnthropicMessageStream(fake_events)
    fake_client = AsyncMock()
    fake_client.messages = fake_messages
    monkeypatch.setattr(chat_route, "_build_anthropic_client", lambda: fake_client)

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/chat", json={
            "user_id": test_user_id,
            "messages": [
                {"role": "user", "content": "log my weight 218"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_abc", "name": "log_weight",
                     "input": {"date": "2026-05-17", "weight_lbs": 218}}
                ]},
            ],
            "tool_confirmation": {"id": "toolu_abc", "approved": True},
        })

    assert resp.status_code == 200
    assert 'data: {"type":"text","delta":"Done."}' in resp.text

    # Verify the write happened
    row = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalar_one()
    assert float(row.weight_lbs) == 218


import base64
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_chat_multimodal_uploads_image_and_passes_through(db_session, monkeypatch, test_user_id):
    """A user message with an image block should:
       1. Upload the bytes to bucket at meals/<sha>.jpg
       2. Append a system-prompt note with that key
       3. Forward the image block unchanged to Anthropic
    """
    from health_metrics.routes import chat as chat_route

    @asynccontextmanager
    async def _ctx():
        yield db_session
    monkeypatch.setattr(chat_route, "_session_factory", lambda: _ctx())

    fake_storage = MagicMock()
    fake_storage.upload_with_sha.return_value = "meals/sha_abc123.jpg"
    monkeypatch.setattr(chat_route, "get_storage", lambda: fake_storage)

    captured_call = {}

    def fake_stream(**kwargs):
        captured_call.update(kwargs)
        return _FakeAnthropicMessageStream([
            type("E", (), {"type": "content_block_delta", "delta": type("D", (), {"type": "text_delta", "text": "looks like dinner"})()})(),
            type("E", (), {"type": "message_stop"})(),
        ])

    fake_client = AsyncMock()
    fake_messages = AsyncMock()
    fake_messages.stream = fake_stream
    fake_client.messages = fake_messages
    monkeypatch.setattr(chat_route, "_build_anthropic_client", lambda: fake_client)

    img_bytes = b"\xff\xd8\xff fakejpegbytes"
    img_b64 = base64.b64encode(img_bytes).decode("ascii")

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/chat", json={
            "user_id": test_user_id,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "log this dinner"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                ],
            }],
        })

    assert resp.status_code == 200
    # Bucket upload happened with the raw bytes
    fake_storage.upload_with_sha.assert_called_once()
    args, kwargs = fake_storage.upload_with_sha.call_args
    actual_data = args[0] if args else kwargs.get("data")
    assert actual_data == img_bytes
    # The image block is forwarded UNCHANGED to Anthropic
    msgs = captured_call.get("messages", [])
    assert len(msgs) == 1
    content = msgs[0]["content"]
    assert any(b.get("type") == "image" for b in content)
    # The system prompt mentions the bucket key
    system = captured_call.get("system", "")
    assert "meals/sha_abc123.jpg" in system
