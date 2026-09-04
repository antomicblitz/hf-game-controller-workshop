"""Small POSIX process runner with hard output and file-size boundaries."""

from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast


@dataclass(frozen=True)
class BoundedProcessResult:
    """The bounded process result, with output retained only up to its limits."""

    returncode: int
    stdout: bytes
    stderr: bytes


class BoundedProcessError(RuntimeError):
    """Base class for a process that exceeded a configured boundary."""

    def __init__(self, message: str, *, stdout: bytes = b"", stderr: bytes = b"") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class BoundedOutputLimitExceeded(BoundedProcessError):
    """The process exceeded stdout, stderr, or a watched output-file limit."""


class BoundedProcessTimeout(BoundedProcessError):
    """The process exceeded its monotonic wall-clock budget."""


def run_bounded(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
    watched_files: Mapping[Path | str, int] | None = None,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    preexec_fn: object | None = None,
    pass_fds: Sequence[int] = (),
) -> BoundedProcessResult:
    """Run argv without a shell, draining both pipes concurrently.

    The process is put in its own POSIX process group. On timeout or any
    boundary breach the whole group is killed and reaped before the exception
    is returned to the caller. Limits are inclusive: a stream of exactly N
    bytes succeeds, while byte N+1 fails.
    """
    watched, inherited_fds = _validate_runner_inputs(
        argv,
        timeout_seconds,
        stdout_limit,
        stderr_limit,
        watched_files,
        pass_fds,
    )

    process = subprocess.Popen(  # noqa: S603
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
        preexec_fn=cast(Callable[[], object] | None, preexec_fn),
        pass_fds=inherited_fds,
    )
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    streams = (
        (cast(BinaryIO, process.stdout), "stdout"),
        (cast(BinaryIO, process.stderr), "stderr"),
    )
    for stream, name in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)

    started = time.monotonic()
    failure: BoundedProcessError | None = None
    try:
        while selector.get_map() or process.poll() is None:
            failure = _poll_failure(started, timeout_seconds, watched, buffers)
            if failure is not None:
                break
            failure = _drain_events(selector, started, timeout_seconds, buffers, limits)
            if failure is not None:
                break
        if failure is not None:
            _terminate_group(process)
            raise failure
        return BoundedProcessResult(
            returncode=process.wait(),
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
        )
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_group(process)
        else:
            process.wait()
        for stream, _ in streams:
            stream.close()


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    """Kill and reap a process group, tolerating a child that already exited."""
    if process.poll() is None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _watched_file_failure(
    watched: Mapping[Path, int], buffers: Mapping[str, bytearray]
) -> BoundedOutputLimitExceeded | None:
    for path, limit in watched.items():
        if path.is_symlink():
            return BoundedOutputLimitExceeded(
                f"watched output {path} became a symlink",
                stdout=bytes(buffers["stdout"]),
                stderr=bytes(buffers["stderr"]),
            )
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        if size > limit:
            return BoundedOutputLimitExceeded(
                f"watched output {path} exceeded {limit} bytes",
                stdout=bytes(buffers["stdout"]),
                stderr=bytes(buffers["stderr"]),
            )
    return None


def _validate_runner_inputs(
    argv: Sequence[str],
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
    watched_files: Mapping[Path | str, int] | None,
    pass_fds: Sequence[int],
) -> tuple[dict[Path, int], tuple[int, ...]]:
    if not argv:
        raise ValueError("argv must be a non-empty sequence of strings")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if stdout_limit < 0 or stderr_limit < 0:
        raise ValueError("output limits must be non-negative")
    watched = {Path(path): limit for path, limit in (watched_files or {}).items()}
    if any(limit < 0 for limit in watched.values()):
        raise ValueError("watched file limits must be non-negative")
    inherited_fds = tuple(pass_fds)
    if os.name != "posix" and inherited_fds:
        raise ValueError("pass_fds is supported only on POSIX")
    if any(type(fd) is not int or fd < 0 for fd in inherited_fds):
        raise ValueError("pass_fds must contain only non-negative integers")
    if len(set(inherited_fds)) != len(inherited_fds):
        raise ValueError("pass_fds must not contain duplicate file descriptors")
    return watched, inherited_fds


def _poll_failure(
    started: float,
    timeout_seconds: float,
    watched: Mapping[Path, int],
    buffers: Mapping[str, bytearray],
) -> BoundedProcessError | None:
    if time.monotonic() - started >= timeout_seconds:
        return BoundedProcessTimeout(
            f"process timed out after {timeout_seconds:g} seconds",
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
        )
    return _watched_file_failure(watched, buffers)


def _drain_events(
    selector: selectors.BaseSelector,
    started: float,
    timeout_seconds: float,
    buffers: dict[str, bytearray],
    limits: Mapping[str, int],
) -> BoundedOutputLimitExceeded | None:
    remaining_time = timeout_seconds - (time.monotonic() - started)
    events = selector.select(min(0.05, remaining_time))
    for key, _ in events:
        failure = _drain_one_event(selector, key, buffers, limits)
        if failure is not None:
            return failure
    return None


def _drain_one_event(
    selector: selectors.BaseSelector,
    key: selectors.SelectorKey,
    buffers: dict[str, bytearray],
    limits: Mapping[str, int],
) -> BoundedOutputLimitExceeded | None:
    stream = cast(BinaryIO, key.fileobj)
    name = cast(str, key.data)
    try:
        chunk = os.read(stream.fileno(), limits[name] - len(buffers[name]) + 1)
    except OSError:
        chunk = b""
    if not chunk:
        selector.unregister(stream)
        return None
    remaining = limits[name] - len(buffers[name])
    if len(chunk) > remaining:
        buffers[name].extend(chunk[:remaining])
        return BoundedOutputLimitExceeded(
            f"{name} exceeded {limits[name]} bytes",
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
        )
    buffers[name].extend(chunk)
    return None


__all__ = [
    "BoundedOutputLimitExceeded",
    "BoundedProcessError",
    "BoundedProcessResult",
    "BoundedProcessTimeout",
    "run_bounded",
]
