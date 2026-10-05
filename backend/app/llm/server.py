"""The local answer model: llama.cpp's llama-server, run as a child process.

A separate process rather than llama-cpp-python inside the API: no C++ build on Windows, a crash
or out-of-memory in the model cannot take search down, and it can be stopped to free RAM.

  - started on the first answer (lazy), unless LLM_PRELOAD is set
  - one generation at a time (-np 1); a second question waits for the first
  - stopped after LLM_IDLE_UNLOAD_S seconds without a request, freeing ~3 GB
  - threads capped (LLM_THREADS) so the laptop stays usable and cool
  - "thinking" off: reasoning tokens would add 10-30 s on a CPU, and the evidence is already chosen
  - on Windows the process is tied to this one (job object), so it never outlives the API

Device (LLM_DEVICE): "auto" tries the Vulkan build on the GPU, then Vulkan with cooperative
matrices off (a known Intel Arc driver bug), then the CPU build; the first that answers /health
is kept. On the dev laptop's Arc 140T the GPU reads prompts 4.6x faster (368 vs 80 tokens/s)
and writes 1.5x faster (20 vs 14), so the wait before an answer starts drops from ~20 s to ~5 s.
"gpu" stops after the two Vulkan attempts; "cpu" uses only the CPU build.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx

logger = logging.getLogger("parai-legal")

NOT_INSTALLED, STOPPED, LOADING, READY, FAILED = "not_installed", "stopped", "loading", "ready", "failed"
MODES = {"auto": ("vulkan", "vulkan-nocoopmat", "cpu"), "gpu": ("vulkan", "vulkan-nocoopmat"), "cpu": ("cpu",)}
START_TIMEOUT_S = 180  # loading a model from a slow disk can take a while


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_JOB = None


def _tie_to_this_process(proc: subprocess.Popen) -> None:
    """Windows: put the server in a job object that dies with this process, so a crash or a Task
    Manager kill of the API never leaves a model loaded. A no-op elsewhere."""
    global _JOB
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if _JOB is None:
        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return
        _JOB = job  # held open for the life of this process
    k32.AssignProcessToJobObject(wintypes.HANDLE(_JOB), wintypes.HANDLE(int(proc._handle)))


class LlamaServer:
    def __init__(self, engine_dir: Path, model: Path, threads: int, batch_threads: int, ctx: int,
                 idle_unload_s: float, device: str = "auto") -> None:
        self.engine_dir, self.model = Path(engine_dir), Path(model)
        self.threads, self.batch_threads, self.ctx = threads, batch_threads, ctx
        self.idle_unload_s = idle_unload_s
        self.device = device if device in MODES else "auto"
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.mode: str | None = None  # the start mode that worked; tried first next time
        self.error: str | None = None
        self.last_used = 0.0
        self._lock = asyncio.Lock()
        self._loading = False
        self._idle_task: asyncio.Task | None = None

    # ── State ──────────────────────────────────────────────────────────

    def binary(self, mode: str) -> Path:
        return self.engine_dir / ("cpu" if mode == "cpu" else "vulkan") / "llama-server.exe"

    def modes(self) -> list[str]:
        """Start modes to try, in order: the one that worked last time first."""
        order = [m for m in MODES[self.device] if self.binary(m).is_file()]
        if self.mode in order:
            order.remove(self.mode)
            order.insert(0, self.mode)
        return order

    @property
    def installed(self) -> bool:
        return self.model.is_file() and bool(self.modes())

    @property
    def state(self) -> str:
        if not self.installed:
            return NOT_INSTALLED
        if self._loading:
            return LOADING
        if self.proc is not None and self.proc.poll() is None:
            return READY
        return FAILED if self.error else STOPPED

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def status(self) -> dict:
        return {"state": self.state, "model": self.model.name, "device": self.mode, "error": self.error}

    # ── Start / stop ───────────────────────────────────────────────────

    def command(self, mode: str, port: int) -> tuple[list[str], dict]:
        """The command line and extra environment for a start mode."""
        args = [str(self.binary(mode)), "-m", str(self.model), "--host", "127.0.0.1", "--port", str(port),
                "--ctx-size", str(self.ctx), "--threads", str(self.threads),
                "--threads-batch", str(self.batch_threads), "--parallel", "1",
                "--jinja", "--reasoning", "off", "--reasoning-format", "deepseek",
                "--cache-reuse", "256", "--no-webui"]
        if mode != "cpu":
            args += ["--n-gpu-layers", "99"]  # every layer on the GPU
        env = {"GGML_VK_DISABLE_COOPMAT": "1"} if mode == "vulkan-nocoopmat" else {}
        return args, env

    async def ensure_running(self) -> None:
        """Start the server if it is not running; raises RuntimeError if it cannot start."""
        self.last_used = time.monotonic()
        if self.state == READY:
            return
        async with self._lock:
            if self.state == READY:
                return
            if not self.installed:
                raise RuntimeError(f"answer model not installed ({self.model})")
            self._loading, self.error = True, None
            try:
                await self._start()
            except Exception as e:
                self.error = str(e)
                self._kill()
                raise RuntimeError(f"the answer model failed to start: {e}") from e
            finally:
                self._loading = False
            if self._idle_task is None or self._idle_task.done():
                self._idle_task = asyncio.create_task(self._unload_when_idle())

    async def _start(self) -> None:
        errors = []
        for mode in self.modes():
            try:
                await self._start_mode(mode)
                self.mode = mode
                return
            except RuntimeError as e:
                logger.warning("Answer model did not start on %s: %s", mode, e)
                errors.append(f"{mode}: {e}")
                self._kill()
        raise RuntimeError("; ".join(errors) or "no llama-server build found")

    async def _start_mode(self, mode: str) -> None:
        self.port = free_port()
        args, env = self.command(mode, self.port)
        logger.info("Starting the answer model (%s) on %s, port %s", self.model.name, mode, self.port)
        t0 = time.monotonic()
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, **env},
            cwd=str(self.binary(mode).parent), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _tie_to_this_process(self.proc)
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() - t0 < START_TIMEOUT_S:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"llama-server exited with code {self.proc.returncode}")
                try:
                    r = await client.get(f"{self.base_url}/health")
                    if r.status_code == 200 and r.json().get("status") == "ok":
                        logger.info("Answer model ready in %.1f s", time.monotonic() - t0)
                        return
                except httpx.HTTPError:
                    pass  # not listening yet
                await asyncio.sleep(0.5)
        raise RuntimeError(f"not ready after {START_TIMEOUT_S} s")

    async def _unload_when_idle(self) -> None:
        while self.state == READY:
            await asyncio.sleep(30)
            if time.monotonic() - self.last_used > self.idle_unload_s and not self._lock.locked():
                logger.info("Answer model idle for %.0f s: stopping it to free memory", self.idle_unload_s)
                self.stop()

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def _kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def stop(self) -> None:
        self._kill()
        if self._idle_task is not None and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None
