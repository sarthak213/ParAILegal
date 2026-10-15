import asyncio
import hashlib

import httpx
import pytest

from app.setup.download import Downloader, FileSpec, Item, load_manifest

DATA = bytes(range(256)) * 4096  # 1 MB


def item(url="http://host/model.gguf", data=DATA, sha=None):
    return Item("answer-model", "Answer model", "answers",
                [FileSpec("models/llm/model.gguf", url, len(data), sha or hashlib.sha256(data).hexdigest())])


def server(data=DATA, honour_range=True, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("range")
        if seen is not None:
            seen.append(rng)
        if rng and honour_range:
            start = int(rng.split("=")[1].rstrip("-"))
            return httpx.Response(206, content=data[start:])
        return httpx.Response(200, content=data)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(d: Downloader, item_id="answer-model"):
    async def go():
        d.start(item_id)
        await d.wait()
    asyncio.run(go())


def test_a_download_is_checked_and_moved_into_place(tmp_path):
    d = Downloader(tmp_path, [item()], client=server())
    assert not d.installed(d.items[0])
    run(d)
    assert d.progress["answer-model"].state == "done"
    assert (tmp_path / "models/llm/model.gguf").read_bytes() == DATA and d.installed(d.items[0])
    assert not (tmp_path / "downloads/model.gguf.part").exists()


def test_a_stopped_download_resumes_where_it_left_off(tmp_path):
    part = tmp_path / "downloads/model.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(DATA[:300_000])
    seen: list = []
    d = Downloader(tmp_path, [item()], client=server(seen=seen))
    run(d)
    assert seen == ["bytes=300000-"] and (tmp_path / "models/llm/model.gguf").read_bytes() == DATA


def test_a_server_that_ignores_the_range_starts_the_file_again(tmp_path):
    part = tmp_path / "downloads/model.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(DATA[:300_000])
    d = Downloader(tmp_path, [item()], client=server(honour_range=False))
    run(d)
    assert (tmp_path / "models/llm/model.gguf").read_bytes() == DATA


def test_a_corrupt_download_is_never_put_where_the_app_looks(tmp_path):
    d = Downloader(tmp_path, [item(sha="0" * 64)], client=server())
    run(d)
    p = d.progress["answer-model"]
    assert p.state == "failed" and "failed its check" in p.error
    assert not (tmp_path / "models/llm/model.gguf").exists() and not (tmp_path / "downloads/model.gguf.part").exists()


def test_an_item_without_a_url_is_not_available(tmp_path):
    d = Downloader(tmp_path, [item(url="")], client=server())
    assert d.status()[0]["available"] is False
    with pytest.raises(ValueError, match="not available"):
        asyncio.run(_start(d))


async def _start(d):
    d.start("answer-model")


def test_the_shipped_manifest_lists_the_three_downloads():
    items = {i.id: i for i in load_manifest()}
    assert set(items) == {"answer-model", "judgments", "case-model"}
    assert items["case-model"].size > 5_000_000_000 and not items["case-model"].required
    assert all(len(f.sha256) == 64 and f.size > 0 for i in items.values() for f in i.files)


def test_a_connection_reset_on_the_default_route_falls_back_to_ipv4(tmp_path, monkeypatch):
    from app.setup import download

    def reset(request):
        raise httpx.ConnectError("[WinError 10054] An existing connection was forcibly closed")

    made = []

    def client(ipv4=False):
        made.append(ipv4)
        return server() if ipv4 else httpx.AsyncClient(transport=httpx.MockTransport(reset))

    monkeypatch.setattr(download, "_client", client)
    d = Downloader(tmp_path, [item()])
    run(d)
    assert made == [False, True] and d.progress["answer-model"].state == "done"
    assert (tmp_path / "models/llm/model.gguf").read_bytes() == DATA
