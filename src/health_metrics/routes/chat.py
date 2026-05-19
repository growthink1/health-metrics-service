"""POST /api/chat — SSE streaming chat with Anthropic tool-use.

Lifecycle:
  client POST /api/chat {messages, [tool_confirmation]}
    ↓
  backend builds system prompt from DB state
    ↓
  if tool_confirmation.approved=True in the request, execute the write tool
  server-side first; append the tool_result to the messages history before
  calling Anthropic. (If approved=False, append a "user declined" tool_result.)
    ↓
  call anthropic.messages.stream(system=..., tools=..., messages=...)
    ↓
  for each event: emit SSE
    - text_delta → data: {"type":"text","delta":"..."}
    - tool_use stop → data: {"type":"tool_use","id":"...","name":"...","input":{...}}
    - message_stop → data: {"type":"done"}
"""

import base64
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog
from anthropic import AsyncAnthropic
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat_prompts import build_system_prompt
from ..chat_tools import TOOL_DEFINITIONS, TOOL_HANDLERS, WRITE_TOOLS
from ..config import get_settings
from ..db import AsyncSessionLocal
from ..storage import get_storage

log = structlog.get_logger()
router = APIRouter(prefix="/api")


class ToolConfirmation(BaseModel):
    id: str
    approved: bool


class ChatRequest(BaseModel):
    user_id: str | None = None
    messages: list[dict[str, Any]]
    tool_confirmation: ToolConfirmation | None = None


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx():
        async with AsyncSessionLocal() as session:
            yield session
    return _ctx()


def _build_anthropic_client() -> AsyncAnthropic | None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


@router.post("/chat")
async def chat(req: ChatRequest):
    settings = get_settings()
    uid = req.user_id or settings.user_id
    client = _build_anthropic_client()

    async def gen():
        if client is None:
            yield _sse({"type": "error", "message": "ANTHROPIC_API_KEY not configured"})
            yield _sse({"type": "done"})
            return

        async with _session_factory() as session:
            # 1. Handle confirmation: execute write tool + append tool_result to history
            messages = list(req.messages)

            # 1a. Image preprocessing: any image blocks in user messages get
            # uploaded to the bucket; record their keys for the system prompt.
            image_hints: list[str] = []
            store = get_storage()
            for msg in messages:
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not (isinstance(block, dict) and block.get("type") == "image"):
                        continue
                    src = block.get("source") or {}
                    if src.get("type") != "base64":
                        continue
                    media = src.get("media_type", "image/jpeg")
                    if "png" in media:
                        ext = "png"
                    elif "webp" in media:
                        ext = "webp"
                    else:
                        ext = "jpg"
                    try:
                        raw = base64.b64decode(src.get("data", ""))
                    except Exception:
                        continue
                    if store is None:
                        # Storage not configured; still pass the image to Anthropic
                        # so vision works, but no key to thread into the prompt.
                        continue
                    try:
                        key = store.upload_with_sha(raw, prefix="meals", ext=ext)
                        image_hints.append(key)
                    except Exception:
                        log.exception("chat_image_upload_failed")

            if req.tool_confirmation is not None:
                # Find the matching tool_use block in the latest assistant message
                tool_use_block = _find_tool_use(messages, req.tool_confirmation.id)
                if tool_use_block is None:
                    yield _sse({"type": "error", "message": f"tool_use_id {req.tool_confirmation.id} not found"})
                    yield _sse({"type": "done"})
                    return

                if req.tool_confirmation.approved:
                    handler = TOOL_HANDLERS.get(tool_use_block["name"])
                    if handler is None:
                        result = {"ok": False, "error": f"unknown tool {tool_use_block['name']}"}
                    else:
                        try:
                            result = await handler(session, uid, **tool_use_block["input"])
                        except Exception as e:
                            log.exception("chat_tool_handler_failed", tool=tool_use_block["name"])
                            result = {"ok": False, "error": str(e)}
                else:
                    result = {"ok": False, "error": "user declined"}

                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": req.tool_confirmation.id,
                        "content": json.dumps(result),
                        "is_error": not result.get("ok", False),
                    }],
                })

            # 2. Build the system prompt + call Anthropic
            system = await build_system_prompt(session, uid, image_hints=image_hints or None)
            try:
                async with client.messages.stream(
                    model=settings.narration_model,
                    system=system,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                    max_tokens=2048,
                ) as stream:
                    current_tool_use: dict[str, Any] | None = None
                    current_tool_json = ""
                    async for event in stream:
                        etype = getattr(event, "type", None)
                        if etype == "content_block_start":
                            block = getattr(event, "content_block", None)
                            if block is not None and getattr(block, "type", None) == "tool_use":
                                current_tool_use = {
                                    "id": block.id, "name": block.name, "input": dict(block.input) if block.input else {},
                                }
                                current_tool_json = ""
                        elif etype == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            dtype = getattr(delta, "type", None)
                            if dtype == "text_delta":
                                yield _sse({"type": "text", "delta": delta.text})
                            elif dtype == "input_json_delta":
                                current_tool_json += delta.partial_json
                        elif etype == "content_block_stop":
                            if current_tool_use is not None:
                                # Finalize tool_use input from accumulated JSON
                                if current_tool_json:
                                    try:
                                        current_tool_use["input"] = json.loads(current_tool_json)
                                    except json.JSONDecodeError:
                                        pass
                                yield _sse({"type": "tool_use", **current_tool_use})
                                current_tool_use = None
                                current_tool_json = ""
                        elif etype == "message_stop":
                            break
            except Exception as e:
                log.exception("chat_anthropic_failed")
                yield _sse({"type": "error", "message": str(e)})
            yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")


def _find_tool_use(messages: list[dict[str, Any]], tool_use_id: str) -> dict[str, Any] | None:
    """Walk back through messages to find the tool_use block with matching id."""
    for msg in reversed(messages):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                    return block
    return None
