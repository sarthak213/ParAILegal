"""The backend as the desktop app runs it (docs/PACKAGING.md, step 4).

    parailegal-backend --resources <install>/resources --home %LOCALAPPDATA%/ParAILegal [--port 0] [--parent-pid N]

The desktop shell (Tauri) starts this, bundled by PyInstaller (backend/parailegal-backend.spec):

  - the two folders are set before the app is imported (app/core/config.py reads them then)
  - it binds 127.0.0.1 only, on a free port when --port is 0, and prints "PARAILEGAL_PORT <n>" on
    its own line as soon as the port is held: the shell reads it, then polls /ready
  - its log goes to <home>/logs/backend.log (a windowed app has no console)
  - it exits when the shell's process ends (--parent-pid), so a crash of the shell never leaves
    the backend, and its model, running
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--resources", help="the install's resources folder (the law, search models, engine)")
    ap.add_argument("--home", help="the user's data folder (downloads, logs)")
    ap.add_argument("--port", type=int, default=0, help="0: any free port")
    ap.add_argument("--parent-pid", type=int, default=0, help="exit when this process ends")
    args = ap.parse_args()

    if args.resources:
        os.environ["PARAILEGAL_RESOURCES"] = _plain_path(args.resources)
    if args.home:
        os.environ["PARAILEGAL_HOME"] = _plain_path(args.home)
    os.environ.setdefault("SEARCH_ENGINE", "v2")
    os.environ.setdefault("PYTHONUTF8", "1")
    home = Path(os.environ.get("PARAILEGAL_HOME") or Path(__file__).resolve().parent)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    log = home / "logs" / "backend.log"
    if getattr(sys, "frozen", False) or sys.stdout is None:
        # a windowed build has no console: print() and the app's own logging go to the log file
        stream = open(log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 (open for the process's life)
        sys.stderr = stream
    logging.basicConfig(filename=log, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if os.name == "nt" else socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]
    # the shell reads this line from stdout; stdout stays the pipe the shell gave us
    if sys.__stdout__ is not None:
        sys.__stdout__.write(f"PARAILEGAL_PORT {port}\n")
        sys.__stdout__.flush()
    if getattr(sys, "frozen", False):
        sys.stdout = sys.stderr  # the app's print()s go to the log, not the shell's pipe

    if args.parent_pid:
        threading.Thread(target=_exit_with_parent, args=(args.parent_pid,), daemon=True).start()

    import uvicorn

    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="on"))
    server.run(sockets=[sock])
    return 0


_VERBATIM = re.compile(r"^\\\\\?\\(UNC\\)?")


def _plain_path(text: str) -> str:
    r"""The folder as an ordinary absolute path. Tauri gives the install folder in Windows' verbatim
    form (\\?\C:\...), which SQLite cannot open through a "file:" URI (the search pack). Read from
    the argument's text: pathlib mangles the prefix."""
    m = _VERBATIM.match(text)
    if m:
        text = ("\\\\" if m.group(1) else "") + text[m.end():]
    return os.path.abspath(text)


def _exit_with_parent(pid: int) -> None:
    """Stop when the desktop shell's process is gone (polls every two seconds)."""
    while _alive(pid):
        time.sleep(2)
    os._exit(0)  # the job object ends the model server with us (app/llm/server.py)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        k32 = ctypes.WinDLL("kernel32")
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
