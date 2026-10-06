"""Answers written by the local model from the prepared evidence (app/answer/pipeline.py)."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

from app.answer import prompts
from app.answer.pipeline import Prepared
from app.answer.sources_only import DISCLAIMER
from app.llm.client import ChatResult, stream_chat
from app.llm.server import LlamaServer

logger = logging.getLogger("parai-legal")


class EmptyAnswer(RuntimeError):
    pass


class LocalAnswerer:
    def __init__(self, server: LlamaServer, temperature: float) -> None:
        self.server = server
        self.temperature = temperature

    @property
    def ready(self) -> bool:
        return self.server.state == "ready"

    async def stream(self, p: Prepared) -> AsyncIterator[str]:
        """The answer, piece by piece: the caveat line (code), the model's text, the legal aid
        line in Summarise mode (code). The disclaimer is added by `finish`."""
        await self.server.ensure_running()
        lead = prompts.lead(p.outcome)
        if lead:
            yield lead
        result = ChatResult()
        t0, first, wrote = time.monotonic(), None, False
        async for text in stream_chat(self.server.base_url, prompts.messages(p.question, p.context, p.mode, p.outcome, p.read_as),
                                      prompts.MODES[p.mode].max_tokens, self.temperature, result):
            if first is None:
                first = time.monotonic() - t0
            wrote = wrote or bool(text.strip())
            self.server.touch()
            yield text
        self.server.touch()
        logger.info("Answer: mode %s, first token %.1f s, total %.1f s, %s prompt + %s answer tokens, finish %s",
                    p.mode, first or 0, time.monotonic() - t0, result.prompt_tokens,
                    result.completion_tokens, result.finish_reason)
        if not wrote:
            raise EmptyAnswer(f"the answer model returned no text (finish reason: {result.finish_reason})")
        tail = prompts.tail(p.mode)
        if tail:
            yield tail

    async def aclose(self) -> None:
        self.server.stop()


def finish(answer: str) -> str:
    return answer.rstrip() + "\n\n" + DISCLAIMER
