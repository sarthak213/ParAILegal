import asyncio
from types import SimpleNamespace

import pytest

from app.search.service import CaseBuilderLocked, SearchService


class FakeServer:
    def __init__(self, name):
        self.name, self.running, self.events = name, False, []

    async def ensure_running(self):
        self.running = True
        self.events.append("start")

    def stop(self):
        self.running = False
        self.events.append("stop")

    def touch(self):
        pass


def service(case=True):
    s = SearchService.__new__(SearchService)
    s.answerer = SimpleNamespace(server=FakeServer("4B"))
    s.case_server = FakeServer("9B") if case else None
    return s


def test_the_case_builder_loads_its_own_model_and_unloads_the_answer_model():
    s = service()
    s.answerer.server.running = True
    server = asyncio.run(s.case_model())
    assert server.name == "9B" and server.running and not s.answerer.server.running
    s.free_for_answers()  # an answer next: the 9B makes way
    assert not s.case_server.running


def test_without_its_own_model_the_case_builder_is_locked():
    s = service(case=False)  # the answer model alone does not open it: no fallback
    assert s.case_locked
    with pytest.raises(CaseBuilderLocked, match="Download it in Setup"):
        asyncio.run(s.case_model())
    assert not s.answerer.server.running


def test_the_locked_case_builder_answers_423(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes.case import _v2

    s = service(case=False)
    s.prepare = lambda *a, **k: None
    with pytest.raises(HTTPException) as e:
        _v2(s)
    assert e.value.status_code == 423 and "9B" in e.value.detail
    s.case_server = FakeServer("9B")
    assert _v2(s) is s
