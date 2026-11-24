"""Blender subprocess control: timeout, cancellation, graceful-then-forced termination,
streamed progress events and a redacted log tail."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

EVENT_PREFIX = "F3D_EVENT "
_SECRETISH = re.compile(r"(token|password|secret|api[_-]?key)\S*", re.IGNORECASE)


@dataclass
class BlenderRun:
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    duration_ms: int
    log_tail: str


def redact(text: str, *paths: Path) -> str:
    for p in paths:
        text = text.replace(str(p), "<workspace>").replace(str(p).replace("\\", "/"), "<workspace>")
    return _SECRETISH.sub("<redacted>", text)


def blender_command(blender_bin: str, runtime_main: Path, job_file: Path) -> list[str]:
    return [
        blender_bin,
        "--background",
        "--factory-startup",  # no user prefs / add-ons
        "--disable-autoexec",  # never run Python embedded in .blend files
        "--python-exit-code",
        "4",
        "--python",
        str(runtime_main),
        "--",
        "--job",
        str(job_file),
    ]


def run_blender(
    cmd: list[str],
    *,
    timeout_seconds: float,
    is_cancelled: Callable[[], bool] = lambda: False,
    on_event: Callable[[dict], None] = lambda _e: None,
    grace_seconds: float = 5.0,
    redact_paths: tuple[Path, ...] = (),
    env: dict[str, str] | None = None,
) -> BlenderRun:
    started = time.monotonic()
    tail: deque[str] = deque(maxlen=200)
    popen_kwargs: dict = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True  # own process group -> kill everything it spawns
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        **popen_kwargs,
    )

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith(EVENT_PREFIX):
                try:
                    on_event(json.loads(line[len(EVENT_PREFIX) :]))
                except Exception:  # a broken event line must never kill the job
                    pass
            elif line.strip() and not line.startswith("Fra:"):
                tail.append(line)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    timed_out = cancelled = False
    last_cancel_check = 0.0
    while True:
        try:
            proc.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        if now - started > timeout_seconds:
            timed_out = True
        elif now - last_cancel_check > 1.0:
            last_cancel_check = now
            cancelled = bool(is_cancelled())
        if timed_out or cancelled:
            _terminate(proc, grace_seconds)
            break

    thread.join(timeout=5)
    return BlenderRun(
        exit_code=proc.returncode,
        timed_out=timed_out,
        cancelled=cancelled,
        duration_ms=int((time.monotonic() - started) * 1000),
        log_tail=redact("\n".join(tail)[-4000:], *redact_paths),
    )


def _terminate(proc: subprocess.Popen, grace: float) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=grace)
    except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=grace)
