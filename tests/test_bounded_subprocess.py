"""Boundary tests for the shared process runner."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))
from cadkit._bounded_subprocess import (  # noqa: E402
    BoundedOutputLimitExceeded,
    BoundedProcessTimeout,
    run_bounded,
)


def test_stdout_limit_is_inclusive() -> None:
    command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 10)"]
    result = run_bounded(command, timeout_seconds=2, stdout_limit=10, stderr_limit=10)
    assert result.stdout == b"x" * 10


def test_stdout_limit_kills_process_on_first_byte_over_limit() -> None:
    command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 11)"]
    with pytest.raises(BoundedOutputLimitExceeded, match="stdout exceeded 10"):
        run_bounded(command, timeout_seconds=2, stdout_limit=10, stderr_limit=10)


def test_timeout_reaps_process_group_and_watched_file_overflow(tmp_path: Path) -> None:
    watched = tmp_path / "output.bin"
    command = [
        sys.executable,
        "-c",
        (
            "import time; from pathlib import Path; "
            f"Path({str(watched)!r}).write_bytes(b'x' * 11); time.sleep(10)"
        ),
    ]
    started = time.monotonic()
    with pytest.raises(BoundedOutputLimitExceeded, match="watched output"):
        run_bounded(
            command,
            timeout_seconds=3,
            stdout_limit=10,
            stderr_limit=10,
            watched_files={watched: 10},
        )
    assert time.monotonic() - started < 2


def test_timeout_is_reported_and_reaped() -> None:
    with pytest.raises(BoundedProcessTimeout):
        run_bounded(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_seconds=0.05,
            stdout_limit=10,
            stderr_limit=10,
        )


def test_watched_symlink_fails_closed_and_kills_process(tmp_path: Path) -> None:
    watched = tmp_path / "watched.bin"
    target = tmp_path / "target.bin"
    command = [
        sys.executable,
        "-c",
        (
            "import time; from pathlib import Path; "
            f"Path({str(target)!r}).write_bytes(b'x'); "
            f"Path({str(watched)!r}).symlink_to({str(target)!r}); time.sleep(10)"
        ),
    ]
    with pytest.raises(BoundedOutputLimitExceeded, match="became a symlink"):
        run_bounded(
            command,
            timeout_seconds=3,
            stdout_limit=10,
            stderr_limit=10,
            watched_files={watched: 10},
        )


@pytest.mark.skipif(os.name != "posix", reason="pass_fds is POSIX-only")
def test_pass_fds_inherits_an_open_descriptor(tmp_path: Path) -> None:
    inherited = tmp_path / "inherited.txt"
    fd = os.open(inherited, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        command = [
            sys.executable,
            "-c",
            "import os,sys; os.fstat(int(sys.argv[1])); print('inherited')",
            str(fd),
        ]
        result = run_bounded(
            command,
            timeout_seconds=2,
            stdout_limit=64,
            stderr_limit=64,
            pass_fds=(fd,),
        )
    finally:
        os.close(fd)
    assert result.stdout == b"inherited\n"


@pytest.mark.skipif(os.name != "posix", reason="pass_fds is POSIX-only")
@pytest.mark.parametrize("fds", [(-1,), (3, 3), (True,)])
def test_pass_fds_rejects_invalid_descriptors(fds: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="pass_fds"):
        run_bounded(
            [sys.executable, "-c", "pass"],
            timeout_seconds=2,
            stdout_limit=10,
            stderr_limit=10,
            pass_fds=fds,
        )
