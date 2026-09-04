"""Beginner-friendly readiness checks without inspecting credential files."""

from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import sys

from agent.config import DEFAULT_MODEL
from cadkit.case_execution import sandbox_status


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed OpenCode argv from this module only
            args, capture_output=True, text=True, timeout=8, check=False
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "timed out")


def _ok(label: str) -> None:
    print(f"OK   {label}")


def _fail(label: str, fix: str) -> None:
    print(f"FAIL {label}\n     Fix: {fix}")


def _check(condition: bool, label: str, fix: str) -> int:
    if condition:
        _ok(label)
        return 0
    _fail(label, fix)
    return 1


def _check_runtime() -> int:
    system = platform.system()
    failures = _check(
        system in {"Darwin", "Linux"},
        f"supported host ({system})",
        "Use macOS or Ubuntu in Windows WSL2.",
    )
    failures += _check(
        sys.version_info[:2] == (3, 12),
        f"Python {platform.python_version()}",
        "Install Python 3.12, then run make setup.",
    )

    for module in ("build123d", "flask", "PIL", "pygltflib", "yacv_server"):
        if importlib.util.find_spec(module) is None:
            _fail(f"Python package {module}", "Run make setup.")
            failures += 1
        else:
            _ok(f"Python package {module}")

    sandbox = sandbox_status()
    failures += _check(
        sandbox.available,
        f"CAD sandbox ({sandbox.backend})",
        sandbox.reason,
    )
    return failures


def _check_opencode() -> int:
    failures = 0

    if shutil.which("opencode") is None:
        _fail("OpenCode not installed", "Follow docs/01-setup.md.")
        return 1
    _ok("OpenCode installed")

    auth = _run("opencode", "auth", "list")
    failures += _check(
        auth.returncode == 0 and "opencode" in auth.stdout.lower(),
        "OpenCode Zen connected",
        "Run make connect and paste the supplied key.",
    )

    _ok(f"workshop model selected ({DEFAULT_MODEL})")
    return failures


def main() -> int:
    failures = _check_runtime() + _check_opencode()

    if failures:
        print(f"\n{failures} check(s) need attention.")
        return 1
    print("\nReady. Run make editor or make agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
