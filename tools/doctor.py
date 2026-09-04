"""Beginner-friendly readiness checks without inspecting credential files."""

from __future__ import annotations

import importlib
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


def main() -> int:
    failures = 0
    system = platform.system()
    if system in {"Darwin", "Linux"}:
        _ok(f"supported host ({system})")
    else:
        _fail("unsupported host", "Use macOS or Ubuntu in Windows WSL2.")
        failures += 1

    if sys.version_info >= (3, 10):  # noqa: UP036 - doctor may run before project install
        _ok(f"Python {platform.python_version()}")
    else:
        _fail("Python 3.10+ required", "Install Python 3.12, then run make setup.")
        failures += 1

    for module in ("build123d", "flask", "PIL", "pygltflib", "yacv_server"):
        try:
            importlib.import_module(module)
        except ImportError:
            _fail(f"Python package {module}", "Run make setup.")
            failures += 1
        else:
            _ok(f"Python package {module}")

    sandbox = sandbox_status()
    if sandbox.available:
        _ok(f"CAD sandbox ({sandbox.backend})")
    else:
        _fail("CAD sandbox unavailable", sandbox.reason)
        failures += 1

    if shutil.which("opencode") is None:
        _fail("OpenCode not installed", "Follow docs/01-setup.md.")
        return 1
    _ok("OpenCode installed")

    auth = _run("opencode", "auth", "list")
    if auth.returncode == 0 and "opencode" in auth.stdout.lower():
        _ok("OpenCode Zen connected")
    else:
        _fail("OpenCode Zen not connected", "Run make connect and paste the supplied key.")
        failures += 1

    models = _run("opencode", "models", "opencode")
    if models.returncode == 0 and DEFAULT_MODEL in models.stdout:
        _ok(f"workshop model available ({DEFAULT_MODEL})")
    else:
        _fail("workshop model unavailable", "Tell Antonio before starting the editor.")
        failures += 1

    if failures:
        print(f"\n{failures} check(s) need attention.")
        return 1
    print("\nReady. Run make editor or make agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
