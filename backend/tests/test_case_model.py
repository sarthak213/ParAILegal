import asyncio
from types import SimpleNamespace

import pytest

from app.search.service import SearchService


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


def test_without_a_case_model_the_answer_model_serves_the_case_builder():
    s = service(case=False)
    assert asyncio.run(s.case_model()).name == "4B"


def test_no_model_installed():
    s = service(case=False)
    s.answerer = None
    with pytest.raises(RuntimeError, match="No answer model"):
        asyncio.run(s.case_model())
