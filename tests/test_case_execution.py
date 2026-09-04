"""Behavioral tests for the fail-closed CAD execution boundary."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import resource
import runpy
import shutil
import socket
import subprocess
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest

_here = Path(__file__).resolve().parent
_session = _here.parent
sys.path.insert(0, str(_session / "tools"))


@contextmanager
def _noop_seccomp_filter() -> Generator[None, None, None]:
    yield


def test_linux_probe_requires_network_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    from cadkit import _sandbox

    seen: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_which(_name: str) -> str:
        return "/fake/bwrap"

    monkeypatch.setattr(_sandbox.shutil, "which", fake_which)
    available, reason = _sandbox._probe_linux(runner)  # pyright: ignore[reportPrivateUsage]

    assert available is True
    assert reason == "bubblewrap network namespace available"
    assert "--unshare-net" in seen[0]


def test_linux_status_prefers_direct_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    from cadkit import _sandbox

    probes: list[str] = []

    def direct_probe() -> tuple[bool, str]:
        probes.append("direct")
        return True, "direct probe passed"

    def fallback_probe() -> tuple[bool, str]:
        probes.append("fallback")
        raise AssertionError("fallback must not run after a successful direct probe")

    monkeypatch.setattr(_sandbox.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_sandbox, "_probe_linux", direct_probe)
    monkeypatch.setattr(_sandbox, "_probe_linux_unshare", fallback_probe)
    monkeypatch.setattr(_sandbox, "seccomp_filter", _noop_seccomp_filter)

    assert _sandbox.status() == (True, "bubblewrap", "direct probe passed")
    assert probes == ["direct"]


def test_linux_status_selects_unshare_after_direct_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cadkit import _sandbox

    probes: list[str] = []

    def direct_probe() -> tuple[bool, str]:
        probes.append("direct")
        return False, "direct probe failed"

    def fallback_probe() -> tuple[bool, str]:
        probes.append("fallback")
        return True, "wrapped probe passed"

    monkeypatch.setattr(_sandbox.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_sandbox, "_probe_linux", direct_probe)
    monkeypatch.setattr(_sandbox, "_probe_linux_unshare", fallback_probe)
    monkeypatch.setattr(_sandbox, "seccomp_filter", _noop_seccomp_filter)

    assert _sandbox.status() == (True, "bubblewrap-unshare", "wrapped probe passed")
    assert probes == ["direct", "fallback"]


def test_linux_unshare_probe_requests_user_and_network_namespaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cadkit import _sandbox

    seen: list[list[str]] = []

    def fake_which(name: str) -> str:
        return f"/fake/{name}"

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(_sandbox.shutil, "which", fake_which)
    available, reason = _sandbox._probe_linux_unshare(runner)  # pyright: ignore[reportPrivateUsage]

    assert available is True
    assert reason == "wrapped bubblewrap network namespace available"
    assert seen[0] == [
        "/fake/unshare",
        "--user",
        "--map-root-user",
        "--net",
        "/fake/bwrap",
        "--die-with-parent",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-cgroup-try",
        "--ro-bind",
        "/",
        "/",
        "/usr/bin/true",
    ]


def test_macos_probe_proves_network_and_write_denial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit import _sandbox

    probe_sentinel = "cadkit-seatbelt-probe:network-denied=1:outside-write-denied=1"
    seen: list[tuple[list[str], dict[str, object]]] = []
    python_host = tmp_path / "runtime" / "python3.11"
    python_host.parent.mkdir()
    python_host.touch()
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(python_host)

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, probe_sentinel, "")

    def trusted_path() -> str:
        return "/usr/bin/sandbox-exec"

    monkeypatch.setattr(_sandbox, "_trusted_macos_sandbox_exec", trusted_path)
    monkeypatch.setattr(_sandbox.sys, "executable", str(venv_python))
    available, reason = _sandbox._probe_macos(runner)  # pyright: ignore[reportPrivateUsage]

    assert available is True
    assert reason == "sandbox-exec network and write isolation available"
    command, kwargs = seen[0]
    assert command[0] == "/usr/bin/sandbox-exec"
    assert command[1] == "-p"
    profile = command[2]
    assert "(deny network*)" in profile
    assert '(allow file-write* (subpath "' in profile
    assert str(venv_python) not in profile
    assert str(python_host.resolve()) in profile
    assert command[3] == str(python_host.resolve())
    assert command[4] == "-I"
    assert command[5] == "-S"
    assert "-c" in command
    assert "cadkit-seatbelt-probe" in command[command.index("-c") + 1]
    assert kwargs["timeout"] == 5
    assert kwargs["check"] is False


def test_worker_startup_exposes_only_launcher_roots(tmp_path: Path) -> None:
    from cadkit import _sandbox

    worker_path = _session / "tools" / "cadkit" / "_case_worker.py"
    explicit_root = tmp_path / "explicit-site-packages"
    explicit_root.mkdir()
    (explicit_root / "launcher_only_probe.py").write_text("VALUE = 'explicit-root'\n")
    dependency_paths = _sandbox._dependency_paths()  # pyright: ignore[reportPrivateUsage]
    worker_args = _sandbox._worker_args(  # pyright: ignore[reportPrivateUsage]
        source_path=tmp_path / "source.py",
        brep_path=tmp_path / "case.brep",
        metadata_path=tmp_path / "metadata.json",
        assembly_path=None,
        worker_path=worker_path,
        logical_case_path="examples/generated/case.py",
        backend="sandbox-exec",
    )
    for path in [*dependency_paths, str(explicit_root)]:
        worker_args.extend(["--site-packages", path])
    worker_index = worker_args.index(str(worker_path))
    startup_flags = worker_args[:worker_index]
    worker_cli = worker_args[worker_index:]
    probe = """
import sys
startup = {"site_loaded": "site" in sys.modules, "paths": list(sys.path)}

import json
import runpy
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="cadkit_startup_probe")
from launcher_only_probe import VALUE
print(json.dumps({"startup": startup, "worker_paths": sys.path, "value": VALUE}))
"""

    result = subprocess.run(  # noqa: S603
        [str(Path(sys.executable).resolve()), *startup_flags, "-c", probe, *worker_cli],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["startup"]["site_loaded"] is False
    assert not any(path.endswith("site-packages") for path in payload["startup"]["paths"])
    assert payload["value"] == "explicit-root"
    allowed_roots = {str(explicit_root), *dependency_paths}
    assert {
        path for path in payload["worker_paths"] if path.endswith("site-packages")
    } <= allowed_roots


def test_macos_probe_rejects_nonzero_or_ambiguous_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cadkit import _sandbox

    monkeypatch.setattr(_sandbox, "_trusted_macos_sandbox_exec", lambda: "/usr/bin/sandbox-exec")

    def nonzero(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 1, "cadkit-seatbelt-probe:network-denied=1:outside-write-denied=1", "denied"
        )

    assert _sandbox._probe_macos(nonzero)[0] is False  # pyright: ignore[reportPrivateUsage]

    def ambiguous(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "unexpected", "")

    assert _sandbox._probe_macos(ambiguous)[0] is False  # pyright: ignore[reportPrivateUsage]


def test_macos_probe_reports_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    from cadkit import _sandbox

    def no_trusted_path() -> None:
        return None

    monkeypatch.setattr(_sandbox, "_trusted_macos_sandbox_exec", no_trusted_path)

    available, reason = _sandbox._probe_macos()  # pyright: ignore[reportPrivateUsage]

    assert available is False
    assert reason == "sandbox-exec is not available"


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(["sandbox-exec"], 5),
        OSError("sandbox-exec failed"),
    ],
    ids=["timeout", "os-error"],
)
def test_macos_probe_reports_runner_failures(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    from cadkit import _sandbox

    monkeypatch.setattr(_sandbox, "_trusted_macos_sandbox_exec", lambda: "/usr/bin/sandbox-exec")

    def runner(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise failure

    available, reason = _sandbox._probe_macos(runner)  # pyright: ignore[reportPrivateUsage]

    assert available is False
    assert "sandbox-exec capability probe failed" in reason


def test_macos_ignores_path_fake_for_probe_and_build_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit import _sandbox

    path_lookups: list[str] = []

    def fake_which(_name: str) -> str:
        path_lookups.append(_name)
        return str(tmp_path / "sandbox-exec")

    def no_trusted_path() -> None:
        return None

    monkeypatch.setattr(_sandbox.shutil, "which", fake_which)
    monkeypatch.setattr(_sandbox, "_trusted_macos_sandbox_exec", no_trusted_path)

    available, reason = _sandbox._probe_macos()  # pyright: ignore[reportPrivateUsage]
    assert available is False
    assert reason == "sandbox-exec is not available"
    assert path_lookups == []

    with pytest.raises(FileNotFoundError, match="sandbox-exec is not installed"):
        _sandbox.build_command(
            source_path=tmp_path / "source.py",
            brep_path=tmp_path / "case.brep",
            metadata_path=tmp_path / "metadata.json",
            worker_path=_session / "tools" / "cadkit" / "_case_worker.py",
            logical_case_path=str(tmp_path / "case.py"),
            sandbox_root=tmp_path,
            backend="sandbox-exec",
        )
    assert path_lookups == []


def test_macos_worker_profile_allows_only_the_canonical_python_runtime(
    tmp_path: Path,
) -> None:
    from cadkit import _sandbox

    runtime_root = tmp_path / "uv-runtime"
    python_host = runtime_root / "bin" / "python3.12"
    python_host.parent.mkdir(parents=True)
    python_host.touch()
    sandbox_root = tmp_path / "sandbox"
    sandbox_root.mkdir()

    profile = _sandbox._seatbelt_profile(  # pyright: ignore[reportPrivateUsage]
        python_host=python_host,
        worker_path=_session / "tools" / "cadkit" / "_case_worker.py",
        sandbox_root=sandbox_root,
        venv_root=None,
        dependency_paths=[],
    )

    assert f'(allow file-read* (subpath "{runtime_root}"))' in profile
    assert f'(allow file-read* (subpath "{tmp_path}"))' not in profile


def test_seccomp_filter_fails_closed_without_memfd(monkeypatch: pytest.MonkeyPatch) -> None:
    from cadkit import _sandbox

    monkeypatch.setattr(_sandbox.os, "memfd_create", None, raising=False)

    with (
        pytest.raises(_sandbox.SeccompUnavailable, match="memfd_create is unavailable"),
        _sandbox.seccomp_filter(),
    ):
        pytest.fail("unavailable memfd must not yield a filter")


def test_seccomp_filter_fails_closed_without_libseccomp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit import _sandbox

    descriptor = os.open(tmp_path / "filter", os.O_RDWR | os.O_CREAT, 0o600)

    def fake_memfd(_name: str, _flags: int) -> int:
        return descriptor

    monkeypatch.setattr(_sandbox.os, "memfd_create", fake_memfd, raising=False)

    def unavailable_library(*_args: object, **_kwargs: object) -> object:
        raise OSError("libseccomp is unavailable")

    monkeypatch.setattr(_sandbox.ctypes, "CDLL", unavailable_library)

    with (
        pytest.raises(_sandbox.SeccompUnavailable, match="libseccomp filter generation failed"),
        _sandbox.seccomp_filter(),
    ):
        pytest.fail("unavailable libseccomp must not yield a filter")

    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_seccomp_filter_contains_process_creation_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit import _sandbox

    descriptor = os.open(tmp_path / "filter", os.O_RDWR | os.O_CREAT, 0o600)

    def fake_memfd(_name: str, _flags: int) -> int:
        return descriptor

    monkeypatch.setattr(_sandbox.os, "memfd_create", fake_memfd, raising=False)
    library = mock.Mock()
    library.seccomp_init.return_value = mock.sentinel.context
    library.seccomp_syscall_resolve_name.side_effect = {
        b"fork": 1,
        b"vfork": 2,
        b"clone3": 3,
        b"clone": 4,
    }.__getitem__
    library.seccomp_export_bpf.return_value = 0
    library.seccomp_rule_add_array.return_value = 0
    rules: list[tuple[int, int, int, tuple[int, int, int, int] | None]] = []

    def record_rule(
        _context: object,
        action: int,
        syscall_number: int,
        argument_count: int,
        comparison: object | None,
    ) -> int:
        comparison_values = None
        if comparison is not None:
            comparison_pointer = ctypes.cast(
                cast(Any, comparison),
                ctypes.POINTER(_sandbox._ScmpArgCmp),  # pyright: ignore[reportPrivateUsage]
            )
            comparison_value = comparison_pointer.contents
            comparison_values = (
                comparison_value.arg,
                comparison_value.op,
                comparison_value.datum_a,
                comparison_value.datum_b,
            )
        rules.append((action, syscall_number, argument_count, comparison_values))
        return 0

    library.seccomp_rule_add_array.side_effect = record_rule

    def fake_cdll(*_args: object, **_kwargs: object) -> mock.Mock:
        return library

    monkeypatch.setattr(_sandbox.ctypes, "CDLL", fake_cdll)

    with _sandbox.seccomp_filter() as yielded:
        assert yielded == descriptor

    syscall_names = {1: "fork", 2: "vfork", 3: "clone3", 4: "clone"}
    assert [syscall_names[rule[1]] for rule in rules] == ["fork", "vfork", "clone3", "clone"]
    errno_action = 0x00050000 | errno.EPERM
    assert [rules[index][0] for index in (0, 1, 3)] == [errno_action] * 3
    assert rules[2][0] == (0x00050000 | errno.ENOSYS)
    assert [rule[2] for rule in rules] == [0, 0, 0, 1]
    clone_rule = rules[-1]
    assert clone_rule[2] == 1
    comparison = clone_rule[3]
    assert comparison is not None
    argument, operator, mask, value = comparison
    assert argument == 0
    assert operator == 7  # SCMP_CMP_MASKED_EQ
    assert mask == 0x00010000  # CLONE_THREAD
    clone_thread = 0x00010000
    assert (clone_thread & mask) != value  # CLONE_THREAD is allowed by the comparator.
    assert (0 & mask) == value  # A process clone is denied by the comparator.

    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_bwrap_command_is_isolated_and_uses_python_isolated_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit import _sandbox

    worker = _session / "tools" / "cadkit" / "_case_worker.py"

    def fake_which(_name: str) -> str:
        return "/fake/bwrap"

    monkeypatch.setattr(_sandbox.shutil, "which", fake_which)

    def no_dependencies() -> list[str]:
        return []

    monkeypatch.setattr(_sandbox, "_dependency_paths", no_dependencies)
    command = _sandbox.build_command(
        source_path=tmp_path / "source.py",
        brep_path=tmp_path / "case.brep",
        metadata_path=tmp_path / "metadata.json",
        worker_path=worker,
        logical_case_path="/work/case.py",
        sandbox_root=tmp_path,
        backend="bubblewrap",
        seccomp_fd=42,
    )

    assert "--unshare-net" in command
    assert command[command.index("--seccomp") + 1] == "42"
    assert "-I" in command
    assert "--bind" in command
    assert "PYTHONPATH" not in command


def test_bwrap_maps_virtualenv_interpreter_to_internal_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit import _sandbox

    monkeypatch.setattr(_sandbox.sys, "executable", "/opt/audit-venv/bin/python3")

    def fake_which(_name: str) -> str:
        return "/fake/bwrap"

    def no_dependencies() -> list[str]:
        return []

    monkeypatch.setattr(_sandbox.shutil, "which", fake_which)
    monkeypatch.setattr(_sandbox, "_dependency_paths", no_dependencies)
    command = _sandbox.build_command(
        source_path=tmp_path / "source.py",
        brep_path=tmp_path / "case.brep",
        metadata_path=tmp_path / "metadata.json",
        worker_path=_session / "tools" / "cadkit" / "_case_worker.py",
        logical_case_path="/sandbox/examples/generated/case.py",
        sandbox_root=tmp_path,
        backend="bubblewrap",
        seccomp_fd=42,
    )

    assert "/opt/audit-venv" in command
    chdir_index = command.index("--chdir")
    assert command[chdir_index + 2] == "/python-venv/bin/python3"


def test_bwrap_unshare_command_uses_private_mounts_and_worker_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit import _sandbox

    worker = _session / "tools" / "cadkit" / "_case_worker.py"

    def fake_which(name: str) -> str:
        return f"/fake/{name}"

    def no_dependencies() -> list[str]:
        return []

    monkeypatch.setattr(_sandbox.shutil, "which", fake_which)
    monkeypatch.setattr(_sandbox, "_dependency_paths", no_dependencies)
    command = _sandbox.build_command(
        source_path=tmp_path / "source.py",
        brep_path=tmp_path / "case.brep",
        metadata_path=tmp_path / "metadata.json",
        worker_path=worker,
        logical_case_path="/sandbox/examples/generated/case.py",
        sandbox_root=tmp_path,
        backend="bubblewrap-unshare",
        seccomp_fd=42,
    )

    assert command[:10] == [
        "/fake/unshare",
        "--user",
        "--map-root-user",
        "--net",
        "/fake/bwrap",
        "--die-with-parent",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-cgroup-try",
    ]
    assert command[command.index("--seccomp") + 1] == "42"
    assert "--new-session" in command
    assert command[command.index("--tmpfs") + 1] == "/tmp"  # noqa: S108
    assert command[command.index("--bind") + 1 : command.index("--bind") + 3] == [
        str(tmp_path),
        "/work",
    ]

    worker_args = command[command.index("--chdir") + 3 :]
    assert worker_args[:3] == ["-I", "-S", "/worker.py"]
    assert worker_args[worker_args.index("--source") + 1] == "/work/source.py"
    assert worker_args[worker_args.index("--brep") + 1] == "/work/case.brep"
    assert worker_args[worker_args.index("--metadata") + 1] == "/work/metadata.json"
    assert worker_args[worker_args.index("--tools") + 1] == "/sandbox/tools"


@pytest.mark.parametrize("backend", ["bubblewrap", "bubblewrap-unshare"])
def test_bwrap_command_requires_seccomp_fd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend: str
) -> None:
    from cadkit import _sandbox

    def no_dependencies() -> list[str]:
        return []

    monkeypatch.setattr(_sandbox, "_dependency_paths", no_dependencies)
    with pytest.raises(RuntimeError, match="requires a process-creation seccomp filter"):
        _sandbox.build_command(
            source_path=tmp_path / "source.py",
            brep_path=tmp_path / "case.brep",
            metadata_path=tmp_path / "metadata.json",
            worker_path=_session / "tools" / "cadkit" / "_case_worker.py",
            logical_case_path="/sandbox/examples/generated/case.py",
            sandbox_root=tmp_path,
            backend=backend,
        )


@pytest.mark.parametrize("backend", ["bubblewrap", "bubblewrap-unshare"])
def test_bwrap_worker_environment_uses_private_internal_paths(tmp_path: Path, backend: str) -> None:
    from cadkit.case_execution import _worker_environment  # pyright: ignore[reportPrivateUsage]

    environment = _worker_environment(tmp_path, backend)

    assert environment == {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/work/home",
        "TMPDIR": "/work/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }


def test_case_launcher_leaves_resource_limits_to_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit import _sandbox
    from cadkit._bounded_subprocess import BoundedProcessTimeout
    from cadkit.case_execution import CaseExecutionTimeout, execute_case_path

    case_path = tmp_path / "case.py"
    case_path.write_text("case = None\n")
    launcher_limits = mock.Mock()
    captured: dict[str, object] = {}

    def fake_command(**_kwargs: object) -> list[str]:
        return ["fake-worker"]

    def stop_worker(_command: list[str], **kwargs: object) -> object:
        captured.update(kwargs)
        raise BoundedProcessTimeout("stopped before worker launch")

    @contextmanager
    def fake_execution_filter(_backend: str) -> Generator[None, None, None]:
        yield

    monkeypatch.setattr(_sandbox, "status", lambda: (True, "bubblewrap", "available"))
    monkeypatch.setattr(_sandbox, "apply_resource_limits", launcher_limits)
    monkeypatch.setattr(_sandbox, "build_command", fake_command)
    monkeypatch.setattr(_sandbox, "execution_filter", fake_execution_filter)
    monkeypatch.setattr("cadkit.case_execution.run_bounded", stop_worker)

    with pytest.raises(CaseExecutionTimeout):
        execute_case_path(case_path)

    assert "preexec_fn" not in captured
    launcher_limits.assert_not_called()


def test_case_execution_passes_and_closes_seccomp_fd_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit.case_execution import execute_case_path

    case_path = tmp_path / "case.py"
    case_path.write_text("case = None\n")
    opened: list[int] = []
    build_kwargs: dict[str, object] = {}
    run_kwargs: dict[str, object] = {}

    @contextmanager
    def fake_filter(_backend: str):
        descriptor = os.open("/dev/null", os.O_RDONLY)
        opened.append(descriptor)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    def fake_command(**kwargs: object) -> list[str]:
        build_kwargs.update(kwargs)
        return ["fake-worker"]

    def fake_run(_command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        run_kwargs.update(kwargs)
        watched_files = kwargs["watched_files"]
        assert isinstance(watched_files, dict)
        watched_files = cast(dict[Path, int], watched_files)
        brep_path = next(path for path in watched_files if path.name == "case.brep")
        metadata_path = next(path for path in watched_files if path.name == "metadata.json")
        from build123d import Box, export_brep  # pyright: ignore[reportUnknownVariableType]

        export_brep_func = cast(Any, export_brep)
        assert export_brep_func(Box(1, 1, 1), brep_path)
        metadata_path.write_text('{"type":"Part","assembly_present":false}')
        return subprocess.CompletedProcess(["fake-worker"], 0, b"", b"")

    monkeypatch.setattr("cadkit.case_execution._sandbox.status", lambda: (True, "bubblewrap", "ok"))
    monkeypatch.setattr("cadkit.case_execution._sandbox.execution_filter", fake_filter)
    monkeypatch.setattr("cadkit.case_execution._sandbox.build_command", fake_command)
    monkeypatch.setattr("cadkit.case_execution.run_bounded", fake_run)

    part = execute_case_path(case_path)

    descriptor = opened[0]
    assert build_kwargs["seccomp_fd"] == descriptor
    assert run_kwargs["pass_fds"] == (descriptor,)
    assert abs(part.volume - 1.0) <= 1e-6
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_case_execution_closes_seccomp_fd_after_worker_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit.case_execution import CaseExecutionError, execute_case_path

    case_path = tmp_path / "case.py"
    case_path.write_text("case = None\n")
    opened: list[int] = []
    run_kwargs: dict[str, object] = {}

    @contextmanager
    def fake_filter(_backend: str):
        descriptor = os.open("/dev/null", os.O_RDONLY)
        opened.append(descriptor)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    def fake_command(**_kwargs: object) -> list[str]:
        return ["fake-worker"]

    def fake_run(_command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        run_kwargs.update(kwargs)
        return subprocess.CompletedProcess(["fake-worker"], 1, b"", b"worker failed")

    monkeypatch.setattr("cadkit.case_execution._sandbox.status", lambda: (True, "bubblewrap", "ok"))
    monkeypatch.setattr("cadkit.case_execution._sandbox.execution_filter", fake_filter)
    monkeypatch.setattr("cadkit.case_execution._sandbox.build_command", fake_command)
    monkeypatch.setattr("cadkit.case_execution.run_bounded", fake_run)

    with pytest.raises(CaseExecutionError, match="worker failed"):
        execute_case_path(case_path)

    descriptor = opened[0]
    assert run_kwargs["pass_fds"] == (descriptor,)
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_worker_applies_resource_limits_during_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    from cadkit import _sandbox

    applied: list[tuple[int, tuple[int, int]]] = []

    def record_limit(kind: int, values: tuple[int, int]) -> None:
        applied.append((kind, values))

    monkeypatch.setattr(_sandbox.resource, "setrlimit", record_limit)
    worker_path = _session / "tools" / "cadkit" / "_case_worker.py"
    runpy.run_path(str(worker_path), run_name="cadkit_worker_startup_probe")

    assert applied == [
        (resource.RLIMIT_CPU, (60, 60)),
        (resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3)),
        (resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2)),
        (resource.RLIMIT_NOFILE, (64, 64)),
    ]


def test_unavailable_sandbox_refuses_source_without_running_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit import _sandbox
    from cadkit.case_execution import SandboxUnavailable, execute_case_path

    case_path = tmp_path / "case.py"
    marker = tmp_path / "marker"
    case_path.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    monkeypatch.setattr(_sandbox, "status", lambda: (False, "bubblewrap", "probe failed"))

    with pytest.raises(SandboxUnavailable, match="sandbox_unavailable: probe failed"):
        execute_case_path(case_path)
    assert not marker.exists()


def test_worker_timeout_is_reported_as_stable_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit import _sandbox
    from cadkit._bounded_subprocess import BoundedProcessTimeout
    from cadkit.case_execution import CaseExecutionTimeout, execute_case_path

    case_path = tmp_path / "case.py"
    case_path.write_text("case = None\n")
    monkeypatch.setattr(_sandbox, "status", lambda: (True, "fake", "available"))

    def fake_command(**_kwargs: object) -> list[str]:
        return ["fake-worker"]

    monkeypatch.setattr(_sandbox, "build_command", fake_command)
    monkeypatch.setattr(
        "cadkit.case_execution.run_bounded",
        mock.Mock(side_effect=BoundedProcessTimeout("timed out")),
    )

    with pytest.raises(CaseExecutionTimeout, match="timed out after 1 seconds"):
        execute_case_path(case_path, timeout_seconds=1)


def test_manifest_requires_canonical_artifact_without_running_source(tmp_path: Path) -> None:
    from cadkit.manifest import ManifestSourceError, from_case_path

    marker = tmp_path / "marker"
    case_path = tmp_path / "case.py"
    case_path.write_text(
        f"CONTROLS = __import__('pathlib').Path({str(marker)!r}).write_text('executed')\n"
    )

    with pytest.raises(ManifestSourceError, match="canonical assembly artifact is required"):
        from_case_path(case_path)

    assert not marker.exists()


def test_manifest_rejects_source_without_canonical_artifact(tmp_path: Path) -> None:
    from cadkit.manifest import ManifestSourceError, from_case_path

    marker = tmp_path / "unrelated-marker"
    case_path = tmp_path / "case.py"
    case_path.write_text(
        f"def build():\n    open({str(marker)!r}, 'w').close()\nCASE_WIDTH_MM = 80\n"
    )

    with pytest.raises(ManifestSourceError, match="canonical assembly artifact is required"):
        from_case_path(case_path)
    assert not marker.exists()


def test_real_sandbox_denies_process_fork_but_returns_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from build123d import Part
    from cadkit import case_execution
    from cadkit.case_execution import execute_case_path, sandbox_status

    if not sandbox_status().available:
        pytest.skip("OS sandbox backend unavailable")

    sandbox_root = tmp_path / "sandbox-root"
    sandbox_root.mkdir(mode=0o700)
    (sandbox_root / "home").mkdir(mode=0o700)
    (sandbox_root / "tmp").mkdir(mode=0o700)

    def fixed_private_directory(_parent: Path | None = None) -> Path:
        return sandbox_root

    real_rmtree = shutil.rmtree
    monkeypatch.setattr(case_execution, "_private_directory", fixed_private_directory)

    def preserve_sandbox(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(case_execution.shutil, "rmtree", preserve_sandbox)
    case_path = tmp_path / "case.py"
    case_path.write_text(
        "import errno\n"
        "import os\n"
        "from pathlib import Path\n"
        "from build123d import Box\n"
        "try:\n"
        "    child_pid = os.fork()\n"
        "except OSError as exc:\n"
        "    if exc.errno != errno.EPERM:\n"
        "        raise\n"
        "else:\n"
        "    if child_pid == 0:\n"
        "        (Path.cwd() / 'descendant-marker').write_text('created')\n"
        "        os._exit(0)\n"
        "    os.waitpid(child_pid, 0)\n"
        "    raise RuntimeError('process fork unexpectedly succeeded')\n"
        "case = Box(10, 10, 10)\n"
    )

    try:
        part = execute_case_path(case_path)
        assert isinstance(part, Part)
        assert abs(part.volume - 1000.0) <= 1e-6
        assert not (sandbox_root / "descendant-marker").exists()
    finally:
        real_rmtree(sandbox_root, ignore_errors=True)


def test_real_sandbox_round_trip_contains_generated_code(tmp_path: Path) -> None:
    from build123d import Part
    from cadkit.case_execution import execute_case_path, sandbox_status

    if not sandbox_status().available:
        pytest.skip("OS sandbox backend unavailable")

    outside_canary = tmp_path / "outside-canary"
    outside_canary.write_text("secret")
    outside_write = tmp_path / "outside-write"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    accepted = threading.Event()

    def accept_once() -> None:
        listener.settimeout(2)
        try:
            connection, _address = listener.accept()
        except OSError:
            return
        accepted.set()
        connection.close()

    thread = threading.Thread(target=accept_once)
    thread.start()
    case_path = tmp_path / "case.py"
    case_path.write_text(
        "from pathlib import Path\n"
        "import socket\n"
        "try:\n"
        f"    if Path({str(outside_canary)!r}).read_text() == 'secret':\n"
        "        raise RuntimeError('outside canary was readable')\n"
        "except OSError:\n"
        "    pass\n"
        "try:\n"
        f"    Path({str(outside_write)!r}).write_text('outside')\n"
        "except OSError:\n"
        "    pass\n"
        "try:\n"
        f"    socket.create_connection(('127.0.0.1', {port}), timeout=1)\n"
        "    raise RuntimeError('network was reachable')\n"
        "except OSError:\n"
        "    pass\n"
        "from build123d import Box, BuildPart\n"
        "with BuildPart() as bp:\n"
        "    Box(10, 10, 10)\n"
        "case = bp.part\n"
    )
    try:
        part = execute_case_path(case_path)
    finally:
        listener.close()
        thread.join(timeout=3)

    assert isinstance(part, Part)
    assert abs(part.volume - 1000.0) <= 1e-6
    assert not outside_write.exists()
    assert not accepted.is_set()


def test_case_worker_serializes_a_canonical_scene_without_running_case_source() -> None:
    from cadkit._case_worker import _scene_payload  # pyright: ignore[reportPrivateUsage]
    from cadkit.assembly import build_demo_assembly

    scene = build_demo_assembly()

    assert _scene_payload(scene) == scene.to_dict()
    assert _scene_payload(scene.to_dict()) == scene.to_dict()
    assert _scene_payload(None) is None


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("x" * 257, "oversized string"),
        ({1: "not a string key"}, "invalid key"),
        ([object()], "non-JSON value"),
        ([float("inf")], "invalid number"),
        ([0] * 513, "oversized list"),
        ({str(index): index for index in range(65)}, "oversized object"),
    ],
)
def test_case_worker_rejects_unbounded_scene_json_shapes(value: object, message: str) -> None:
    from cadkit._case_worker import _validate_json_shape  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(ValueError, match=message):
        _validate_json_shape(value)


def test_case_worker_rejects_deeply_nested_scene_json() -> None:
    from cadkit._case_worker import _validate_json_shape  # pyright: ignore[reportPrivateUsage]

    nested: object = None
    for _ in range(18):
        nested = [nested]

    with pytest.raises(ValueError, match="too deep"):
        _validate_json_shape(nested)


def test_case_worker_rejects_invalid_scene_routes_and_bounds() -> None:
    from cadkit._case_worker import _scene_payload  # pyright: ignore[reportPrivateUsage]
    from cadkit.assembly import build_demo_assembly

    scene_payload = build_demo_assembly().to_dict()
    routes = scene_payload["routes"]
    assert isinstance(routes, list)
    assert isinstance(routes[0], dict)
    routes[0]["signal"] = "tampered"

    with pytest.raises(ValueError, match="invalid route signals"):
        _scene_payload(scene_payload)

    oversized = build_demo_assembly().to_dict()
    nodes = oversized["nodes"]
    routes = oversized["routes"]
    assert isinstance(nodes, list)
    assert isinstance(routes, list)
    with pytest.raises(ValueError, match="node or route limits"):
        _scene_payload({**oversized, "nodes": nodes * 129})
    with pytest.raises(ValueError, match="node or route limits"):
        _scene_payload({**oversized, "routes": routes * 257})


def test_case_execution_validates_metadata_and_canonical_assembly_artifacts(
    tmp_path: Path,
) -> None:
    import cadkit.case_execution as case_execution
    from cadkit.assembly import build_demo_assembly

    read_assembly = case_execution._read_assembly  # pyright: ignore[reportPrivateUsage]
    read_metadata = case_execution._read_metadata  # pyright: ignore[reportPrivateUsage]

    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text('{"type":"Part","assembly_present":true}')
    assert read_metadata(metadata_path)["type"] == "Part"

    assembly_path = tmp_path / "assembly.json"
    expected = build_demo_assembly().to_dict()
    assembly_path.write_text(json.dumps(expected))
    assert read_assembly(assembly_path) == expected
    assert read_assembly(tmp_path / "missing.json") is None


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[]", "not an object"),
        ('{"case_dimensions": {}}', "invalid shape"),
        ("{not-json", "valid assembly metadata"),
    ],
)
def test_case_execution_rejects_invalid_assembly_artifacts(
    tmp_path: Path, content: str, message: str
) -> None:
    import cadkit.case_execution as case_execution
    from cadkit.case_execution import CaseExecutionError

    read_assembly = case_execution._read_assembly  # pyright: ignore[reportPrivateUsage]

    artifact = tmp_path / "assembly.json"
    artifact.write_text(content)

    with pytest.raises(CaseExecutionError, match=message):
        read_assembly(artifact)


def test_case_execution_rejects_assembly_with_invalid_route_signal(tmp_path: Path) -> None:
    import cadkit.case_execution as case_execution
    from cadkit.assembly import build_demo_assembly
    from cadkit.case_execution import CaseExecutionError

    read_assembly = case_execution._read_assembly  # pyright: ignore[reportPrivateUsage]

    payload = build_demo_assembly().to_dict()
    routes = payload["routes"]
    assert isinstance(routes, list)
    assert isinstance(routes[0], dict)
    routes[0]["signal"] = "tampered"
    artifact = tmp_path / "assembly.json"
    artifact.write_text(json.dumps(payload))

    with pytest.raises(CaseExecutionError, match="validation bounds"):
        read_assembly(artifact)


# ---------------------------------------------------------------------------
# First production-export contract — placements artifact
# ---------------------------------------------------------------------------
def _install_fake_case_runner(
    monkeypatch: pytest.MonkeyPatch,
    placements_json: str | None,
) -> list[dict[Path, int]]:
    """Replace only the subprocess boundary while preserving artifact reads."""
    watched: list[dict[Path, int]] = []

    @contextmanager
    def fake_filter(_backend: str) -> Generator[int]:
        yield 42

    def fake_command(**_kwargs: object) -> list[str]:
        return ["fake-worker"]

    def fake_run(_command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        files = cast(dict[Path, int], kwargs["watched_files"])
        watched.append(files)
        brep_path = next(path for path in files if path.name == "case.brep")
        metadata_path = next(path for path in files if path.name == "metadata.json")
        from build123d import Box, export_brep  # pyright: ignore[reportUnknownVariableType]

        export_brep_func = cast(Any, export_brep)
        assert export_brep_func(Box(1, 1, 1), brep_path)
        metadata_path.write_text('{"type":"Part","assembly_present":false}')
        if placements_json is not None:
            cwd = cast(Path, kwargs["cwd"])
            (cwd / "placements.json").write_text(placements_json)
        return subprocess.CompletedProcess(["fake-worker"], 0, b"", b"")

    monkeypatch.setattr("cadkit.case_execution._sandbox.status", lambda: (True, "fake", "ok"))
    monkeypatch.setattr("cadkit.case_execution._sandbox.execution_filter", fake_filter)
    monkeypatch.setattr("cadkit.case_execution._sandbox.build_command", fake_command)
    monkeypatch.setattr("cadkit.case_execution.run_bounded", fake_run)
    return watched


def test_case_execution_returns_module_level_placements_in_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit.case_execution import execute_case_artifacts
    from cadkit.constraints import Placements

    placements = Placements(case_length_mm=131.0, case_width_mm=81.0, case_thickness_mm=35.5)
    case_path = tmp_path / "case.py"
    case_path.write_text(
        "from build123d import Box\n"
        "from cadkit.constraints import Placements\n"
        "case = Box(1, 1, 1)\n"
        "PLACEMENTS = Placements(case_length_mm=131.0, case_width_mm=81.0, case_thickness_mm=35.5)\n"
    )
    placements_json = cast(str, cast(Any, placements).to_json())
    _install_fake_case_runner(monkeypatch, placements_json)

    artifacts = execute_case_artifacts(case_path)

    assert cast(Any, artifacts).placements == placements


def test_case_execution_returns_none_for_case_without_placements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit.case_execution import execute_case_artifacts

    case_path = tmp_path / "case.py"
    case_path.write_text("from build123d import Box\ncase = Box(1, 1, 1)\n")
    _install_fake_case_runner(monkeypatch, None)

    artifacts = execute_case_artifacts(case_path)

    assert cast(Any, artifacts).placements is None


def test_case_execution_rejects_malformed_placements_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit.case_execution import CaseExecutionError, execute_case_artifacts

    case_path = tmp_path / "case.py"
    case_path.write_text("from build123d import Box\ncase = Box(1, 1, 1)\n")
    _install_fake_case_runner(monkeypatch, '{"schema":"cadkit.placements"')

    with pytest.raises(CaseExecutionError):
        execute_case_artifacts(case_path)


def test_case_execution_watches_a_bounded_placements_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit.case_execution import execute_case_artifacts

    case_path = tmp_path / "case.py"
    case_path.write_text("from build123d import Box\ncase = Box(1, 1, 1)\n")
    watched = _install_fake_case_runner(monkeypatch, None)

    execute_case_artifacts(case_path)

    placements_path = next(path for path in watched[0] if path.name == "placements.json")
    assert watched[0][placements_path] == 64 * 1024
