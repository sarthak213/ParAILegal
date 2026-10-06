"""Streaming chat against llama-server's OpenAI-compatible endpoint."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx


async def complete_json(base_url: str, messages: list[dict], schema: dict, max_tokens: int,
                        temperature: float = 0.1) -> dict:
    """One completion constrained to a JSON schema (llama-server turns it into a grammar, so the
    reply always parses). Thinking off, as for answers."""
    body = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=180.0, write=10.0, pool=5.0)) as client:
        r = await client.post(f"{base_url}/v1/chat/completions", json=body)
    if r.status_code != 200:
        raise RuntimeError(f"answer model error {r.status_code}: {r.text[:300]}")
    return json.loads(r.json()["choices"][0]["message"]["content"])


class ChatResult:
    """Filled in while streaming: why generation stopped, and the token counts."""

    def __init__(self) -> None:
        self.finish_reason: str | None = None
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None


async def stream_chat(base_url: str, messages: list[dict], max_tokens: int, temperature: float,
                      result: ChatResult | None = None) -> AsyncIterator[str]:
    """Yield the answer text as it is generated. Any reasoning text is dropped (thinking is off,
    and the server's reasoning-format keeps stray thoughts out of `content`)."""
    body = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{base_url}/v1/chat/completions", json=body) as r:
            if r.status_code != 200:
                detail = (await r.aread()).decode(errors="replace")[:300]
                raise RuntimeError(f"answer model error {r.status_code}: {detail}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if result is not None and chunk.get("usage"):
                    result.prompt_tokens = chunk["usage"].get("prompt_tokens")
                    result.completion_tokens = chunk["usage"].get("completion_tokens")
                for choice in chunk.get("choices") or []:
                    if result is not None and choice.get("finish_reason"):
                        result.finish_reason = choice["finish_reason"]
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        yield text
