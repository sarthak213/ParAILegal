"""First-run downloads: the models and the Supreme Court judgments (docs/PACKAGING.md).

The installer carries the app and the law; these come once, after it:

    answer-model   the 4B answer model                    answers (otherwise "sources only")
    judgments      the judgments index and the citator    judgments search, precedents, leading cases
    case-model     the 9B Case Builder model              the Case Builder (locked until it is in)

Each item is files in the manifest (app/setup/manifest.json): where it goes under the data folder
(config.HOME_DIR), where it comes from, its size and SHA-256. A download:

  - goes to <home>/downloads/<name>.part first, resuming where it stopped (HTTP Range), so a
    dropped connection or a closed laptop does not start 5 GB over
  - is checked against its SHA-256 before it is moved into place, in one rename: a half-written
    or corrupt model is never where the app looks for one
  - runs one at a time, and can be cancelled (the part is kept, for the next try)

An item is installed when each of its files is in place with the manifest's size (the checksum is
checked once, on download, not on every start: hashing 5 GB takes a minute).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

# the shipped manifest; PARAILEGAL_MANIFEST points at another (a test server, or a mirror)
MANIFEST = Path(os.environ.get("PARAILEGAL_MANIFEST") or Path(__file__).with_name("manifest.json"))
CHUNK = 1 << 20  # 1 MB


@dataclass
class FileSpec:
    path: str     # under the data folder: "models/llm/Qwen3.5-4B-Q4_K_M.gguf"
    url: str      # "" until hosting is decided: the item is then "unavailable"
    size: int
    sha256: str


@dataclass
class Item:
    id: str
    title: str
    unlocks: str
    files: list[FileSpec]
    required: bool = False

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


@dataclass
class Progress:
    state: str = "idle"          # idle | downloading | verifying | done | failed | cancelled
    done: int = 0                # bytes of the item on disk (parts included)
    total: int = 0
    file: str = ""
    error: str = ""


def load_manifest(path: Path = MANIFEST) -> list[Item]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Item(i["id"], i["title"], i["unlocks"], [FileSpec(**f) for f in i["files"]], i.get("required", False))
            for i in data["items"]]


@dataclass
class Downloader:
    home: Path
    items: list[Item]
    client: Any = None   # an httpx.AsyncClient; tests pass one with a mock transport
    on_done: Any = None  # called with the item's id when it is installed (the app takes it up)
    progress: dict[str, Progress] = field(default_factory=dict)
    _task: asyncio.Task | None = None
    _ipv4: bool = False  # set after a connection failed over the default route (see _run)
    _current: str = ""

    # state

    def target(self, f: FileSpec) -> Path:
        return self.home / f.path

    def part(self, f: FileSpec) -> Path:
        return self.home / "downloads" / (Path(f.path).name + ".part")

    def installed(self, item: Item) -> bool:
        return all(self.target(f).is_file() and self.target(f).stat().st_size == f.size for f in item.files)

    def item(self, item_id: str) -> Item:
        found = next((i for i in self.items if i.id == item_id), None)
        if found is None:
            raise KeyError(item_id)
        return found

    def status(self) -> list[dict]:
        out = []
        for i in self.items:
            p = self.progress.get(i.id) or Progress(total=i.size, done=self._on_disk(i))
            out.append({"id": i.id, "title": i.title, "unlocks": i.unlocks, "required": i.required,
                        "size": i.size, "installed": self.installed(i),
                        "available": all(f.url for f in i.files), "progress": asdict(p)})
        return out

    def _on_disk(self, item: Item) -> int:
        n = 0
        for f in item.files:
            if self.target(f).is_file() and self.target(f).stat().st_size == f.size:
                n += f.size
            elif self.part(f).is_file():
                n += self.part(f).stat().st_size
        return n

    # running

    def start(self, item_id: str) -> None:
        """Start downloading an item in the background; ValueError if one is running or it has no URL."""
        item = self.item(item_id)
        if self._task is not None and not self._task.done():
            raise ValueError(f"{self._current} is downloading; one at a time")
        if not all(f.url for f in item.files):
            raise ValueError(f"{item.title} is not available for download yet")
        self._current = item_id
        self.progress[item_id] = Progress(state="downloading", total=item.size, done=self._on_disk(item))
        self._task = asyncio.create_task(self._run(item))

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def wait(self) -> None:
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self, item: Item) -> None:
        p = self.progress[item.id]
        client = self.client or _client(ipv4=self._ipv4)
        try:
            for f in item.files:
                if self.target(f).is_file() and self.target(f).stat().st_size == f.size:
                    continue
                p.file = f.path
                try:
                    await self._fetch(client, f, p)
                except httpx.ConnectError:
                    if self.client is not None or self._ipv4:
                        raise
                    # some networks' IPv6 route resets connections (seen to Hugging Face's CDN, where
                    # IPv4 got through every time): IPv4 from now on, and this file again
                    await client.aclose()
                    self._ipv4 = True
                    client = _client(ipv4=True)
                    await self._fetch(client, f, p)
            p.state, p.done = "done", item.size
            if self.on_done is not None:
                self.on_done(item.id)
        except asyncio.CancelledError:
            p.state = "cancelled"
            raise
        except Exception as e:  # the part is kept: the next try resumes
            p.state, p.error = "failed", f"{type(e).__name__}: {e}"
        finally:
            if self.client is None:
                await client.aclose()

    async def _fetch(self, client: Any, f: FileSpec, p: Progress) -> None:
        part = self.part(f)
        part.parent.mkdir(parents=True, exist_ok=True)
        have = part.stat().st_size if part.is_file() else 0
        if have > f.size:  # a part longer than the file: start again
            part.unlink()
            have = 0
        if have < f.size:
            headers = {"Range": f"bytes={have}-"} if have else {}
            async with client.stream("GET", f.url, headers=headers) as r:
                if r.status_code == 200 and have:  # the server ignored the range: start again
                    p.done -= have
                    have = 0
                    part.unlink()
                elif r.status_code not in (200, 206):
                    raise RuntimeError(f"{f.url} answered {r.status_code}")
                with part.open("ab") as out:
                    async for chunk in r.aiter_bytes(CHUNK):
                        out.write(chunk)
                        p.done += len(chunk)
        p.state = "verifying"
        digest = await asyncio.to_thread(_sha256, part)
        if part.stat().st_size != f.size or digest != f.sha256:
            part.unlink()  # corrupt: the next try starts clean
            raise RuntimeError(f"{Path(f.path).name} failed its check (size or SHA-256); it will be downloaded again")
        target = self.target(f)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, target)
        p.state = "downloading"


def _client(ipv4: bool = False) -> httpx.AsyncClient:
    """The HTTP client for downloads; ipv4: connect over IPv4 only (bound to 0.0.0.0)."""
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2) if ipv4 else \
        httpx.AsyncHTTPTransport(retries=2)
    return httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.hexdigest()
