"""Small OS-sandbox backends used by :mod:`cadkit.case_execution`.

This module deliberately contains no source execution.  The backend probe is
fail-closed: a present binary is not sufficient unless the requested network
namespace capability can actually be created.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import resource
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import cast

ProbeRunner = Callable[..., subprocess.CompletedProcess[str]]
_MACOS_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
_SECCOMP_LIBRARY = "libseccomp.so.2"
_SECCOMP_ACT_ALLOW = 0x7FFF0000
_SECCOMP_ACT_ERRNO = 0x00050000
_SECCOMP_CMP_MASKED_EQ = 7
_CLONE_THREAD = 0x00010000


class SeccompUnavailable(RuntimeError):
    """The Linux process-creation filter could not be generated."""


class _ScmpArgCmp(ctypes.Structure):
    """ctypes representation of libseccomp's scmp_arg_cmp structure."""

    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_uint),
        ("datum_a", ctypes.c_ulonglong),
        ("datum_b", ctypes.c_ulonglong),
    ]


def _configure_seccomp_library(library: ctypes.CDLL) -> None:
    """Declare the small libseccomp ABI surface used by the trusted launcher."""
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(_ScmpArgCmp),
    ]
    library.seccomp_rule_add_array.restype = ctypes.c_int
    library.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.seccomp_export_bpf.restype = ctypes.c_int
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    library.seccomp_release.restype = None


def _add_seccomp_rule(
    library: ctypes.CDLL,
    context: ctypes.c_void_p,
    syscall_name: bytes,
    action: int,
) -> None:
    syscall_number = library.seccomp_syscall_resolve_name(syscall_name)
    if syscall_number < 0:
        raise SeccompUnavailable(f"unable to resolve seccomp syscall {syscall_name.decode()}")
    result = library.seccomp_rule_add_array(context, action, syscall_number, 0, None)
    if result != 0:
        raise SeccompUnavailable(f"unable to add seccomp rule for {syscall_name.decode()}")


@contextmanager
def seccomp_filter() -> Generator[int, None, None]:
    """Export the process-creation filter into a private inherited memfd."""
    descriptor = -1
    context: ctypes.c_void_p | None = None
    library: ctypes.CDLL | None = None
    try:
        create_memfd = cast(Callable[[str, int], int], getattr(os, "memfd_create", None))
        if not callable(create_memfd):
            raise SeccompUnavailable("memfd_create is unavailable")
        try:
            descriptor = create_memfd("cadkit-seccomp", int(getattr(os, "MFD_CLOEXEC", 0)))
        except (OSError, TypeError, ValueError) as exc:
            raise SeccompUnavailable("memfd creation failed") from exc
        try:
            library = ctypes.CDLL(_SECCOMP_LIBRARY, use_errno=True)
            _configure_seccomp_library(library)
            context = library.seccomp_init(_SECCOMP_ACT_ALLOW)
            if not context:
                raise SeccompUnavailable("seccomp filter initialization failed")
            errno_action = _SECCOMP_ACT_ERRNO | errno.EPERM
            for syscall_name in (b"fork", b"vfork"):
                _add_seccomp_rule(library, context, syscall_name, errno_action)
            # glibc uses clone3 for pthreads when available but only falls back
            # to clone when clone3 reports ENOSYS; clone remains thread-only.
            _add_seccomp_rule(
                library,
                context,
                b"clone3",
                _SECCOMP_ACT_ERRNO | errno.ENOSYS,
            )
            clone_number = library.seccomp_syscall_resolve_name(b"clone")
            if clone_number < 0:
                raise SeccompUnavailable("unable to resolve seccomp syscall clone")
            clone_comparison = _ScmpArgCmp(
                0,
                _SECCOMP_CMP_MASKED_EQ,
                _CLONE_THREAD,
                0,
            )
            result = library.seccomp_rule_add_array(
                context,
                errno_action,
                clone_number,
                1,
                ctypes.byref(clone_comparison),
            )
            if result != 0:
                raise SeccompUnavailable("unable to add seccomp rule for clone")
            if library.seccomp_export_bpf(context, descriptor) != 0:
                raise SeccompUnavailable("unable to export seccomp filter")
            os.lseek(descriptor, 0, os.SEEK_SET)
        except SeccompUnavailable:
            raise
        except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError) as exc:
            raise SeccompUnavailable("libseccomp filter generation failed") from exc
        yield descriptor
    finally:
        if context is not None and library is not None:
            with suppress(OSError):
                library.seccomp_release(context)
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)


@contextmanager
def execution_filter(backend: str) -> Generator[int | None, None, None]:
    """Yield the filter descriptor required by a Linux worker backend."""
    if backend.startswith("bubblewrap"):
        with seccomp_filter() as descriptor:
            yield descriptor
    else:
        yield None


def _resolved_python_host() -> Path:
    """Return the canonical executable Seatbelt will authorize and launch."""
    return Path(sys.executable).resolve()


def _macos_python_exec_rules(python_host: Path) -> str:
    """Allow the Python.org launcher and its framework application binary."""
    executables = [python_host]
    framework_python = Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python"
    if framework_python.is_file() and framework_python.resolve() != python_host:
        executables.append(framework_python.resolve())
    return "".join(
        f' (allow process-exec (literal "{_seatbelt_path(path)}"))' for path in executables
    )


def _macos_metadata_rules(*paths: Path) -> str:
    """Allow path traversal metadata without exposing unrelated file contents."""
    ancestors = {parent for path in paths for parent in path.parents}
    literals = "".join(
        f' (literal "{_seatbelt_path(path)}")'
        for path in sorted(ancestors, key=lambda item: (len(item.parts), str(item)))
    )
    return f" (allow file-read-metadata{literals})"


def _path_variants(path: Path) -> tuple[Path, ...]:
    """Return lexical and canonical forms used by macOS path matching."""
    resolved = path.resolve()
    return (path,) if resolved == path else (path, resolved)


def _trusted_macos_sandbox_exec() -> str | None:
    """Return Seatbelt only when the platform-provided binary is executable."""
    if _MACOS_SANDBOX_EXEC.is_file() and os.access(_MACOS_SANDBOX_EXEC, os.X_OK):
        return str(_MACOS_SANDBOX_EXEC)
    return None


def _linux_probe_command(bwrap: str) -> list[str]:
    """Return the minimal bubblewrap capability probe."""
    command = [
        bwrap,
        "--die-with-parent",
        "--unshare-all",
        "--unshare-net",
        "--ro-bind",
        "/",
        "/",
        "/usr/bin/true",
    ]
    return command


def _linux_unshare_probe_command(unshare: str, bwrap: str) -> list[str]:
    """Probe network isolation when seccomp blocks bubblewrap's loopback setup."""
    return [
        unshare,
        "--user",
        "--map-root-user",
        "--net",
        bwrap,
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


def _probe_linux(runner: ProbeRunner | None = None) -> tuple[bool, str]:
    """Probe bubblewrap and its network namespace support."""
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return False, "bubblewrap is not installed"
    runner = runner or subprocess.run
    try:
        result = runner(
            _linux_probe_command(bwrap),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"bubblewrap capability probe failed: {type(exc).__name__}"
    if result.returncode != 0:
        return False, "bubblewrap capability probe failed"
    return True, "bubblewrap network namespace available"


def _probe_linux_unshare(runner: ProbeRunner | None = None) -> tuple[bool, str]:
    """Probe a util-linux network namespace wrapped around bubblewrap."""
    bwrap = shutil.which("bwrap")
    unshare = shutil.which("unshare")
    if bwrap is None:
        return False, "bubblewrap is not installed"
    if unshare is None:
        return False, "unshare is not installed"
    runner = runner or subprocess.run
    try:
        result = runner(
            _linux_unshare_probe_command(unshare, bwrap),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"wrapped bubblewrap capability probe failed: {type(exc).__name__}"
    if result.returncode != 0:
        return False, "wrapped bubblewrap capability probe failed"
    return True, "wrapped bubblewrap network namespace available"


_MACOS_PROBE_SENTINEL = "cadkit-seatbelt-probe:network-denied=1:outside-write-denied=1"
_MACOS_PROBE_SCRIPT = """
import pathlib
import socket
import sys

try:
    socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1)
except OSError:
    network_denied = True
else:
    network_denied = False

try:
    pathlib.Path(sys.argv[2]).write_text("probe", encoding="utf-8")
except OSError:
    outside_write_denied = True
else:
    outside_write_denied = False

print(
    "cadkit-seatbelt-probe:"
    f"network-denied={int(network_denied)}:"
    f"outside-write-denied={int(outside_write_denied)}"
)
raise SystemExit(int(not (network_denied and outside_write_denied)))
""".strip()


def _seatbelt_probe_profile(*, python_host: Path, sandbox_root: Path) -> str:
    """Return a harmless Seatbelt profile for the capability probe."""
    sandbox_paths = _path_variants(sandbox_root)
    profile = (
        "(version 1)"
        " (deny default)"
        ' (import "system.sb")'
        f"{_macos_python_exec_rules(python_host)}"
        " (allow signal (target self))"
        " (allow sysctl-read)"
        f"{_macos_metadata_rules(python_host, *sandbox_paths)}"
        f' (allow file-read* (literal "{_seatbelt_path(python_host)}"))'
        " (deny network*)"
    )
    for path in sandbox_paths:
        profile += f' (allow file-write* (subpath "{_seatbelt_path(path)}"))'
    for runtime_path in (
        Path("/usr"),
        Path("/System/Library"),
        Path("/Library/Frameworks"),
        python_host.parent.parent,
    ):
        if runtime_path.is_dir():
            profile += f' (allow file-read* (subpath "{_seatbelt_path(runtime_path)}"))'
    return profile


def _macos_probe_command(
    *, sandbox_exec: str, python_host: Path, sandbox_root: Path, port: int, outside_path: Path
) -> list[str]:
    """Return the command that proves Seatbelt network and write isolation."""
    args = [
        sandbox_exec,
        "-p",
        _seatbelt_probe_profile(python_host=python_host, sandbox_root=sandbox_root),
        str(python_host),
        "-I",
        "-S",
        "-c",
        _MACOS_PROBE_SCRIPT,
        str(port),
        str(outside_path),
    ]
    return args


def _probe_macos(runner: ProbeRunner | None = None) -> tuple[bool, str]:
    """Probe Seatbelt's network and out-of-sandbox write denial."""
    sandbox_exec = _trusted_macos_sandbox_exec()
    if sandbox_exec is None:
        return False, "sandbox-exec is not available"
    runner = runner or subprocess.run
    python_host = _resolved_python_host()
    try:
        with tempfile.TemporaryDirectory(prefix="cadkit-seatbelt-probe-") as directory:
            sandbox_root = Path(directory) / "inside"
            sandbox_root.mkdir()
            outside_path = Path(directory) / "outside-write"
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                result = runner(
                    _macos_probe_command(
                        sandbox_exec=sandbox_exec,
                        python_host=python_host,
                        sandbox_root=sandbox_root,
                        port=listener.getsockname()[1],
                        outside_path=outside_path,
                    ),
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"sandbox-exec capability probe failed: {type(exc).__name__}"
    stdout = cast(object, getattr(result, "stdout", None))
    if (
        result.returncode != 0
        or not isinstance(stdout, str)
        or stdout.strip() != _MACOS_PROBE_SENTINEL
    ):
        return False, "sandbox-exec capability probe failed"
    return True, "sandbox-exec network and write isolation available"


def status() -> tuple[bool, str, str]:
    """Return ``(available, backend, safe reason)`` for this host."""

    def with_seccomp(backend: str, reason: str) -> tuple[bool, str, str]:
        try:
            with seccomp_filter():
                pass
        except SeccompUnavailable:
            return False, backend, "libseccomp process-creation filter unavailable"
        return True, backend, reason

    system = platform.system()
    if system == "Linux":
        available, reason = _probe_linux()
        if available:
            return with_seccomp("bubblewrap", reason)
        wrapped_available, wrapped_reason = _probe_linux_unshare()
        if wrapped_available:
            return with_seccomp("bubblewrap-unshare", wrapped_reason)
        return False, "bubblewrap", f"{reason}; {wrapped_reason}"
    if system == "Darwin":
        available, reason = _probe_macos()
        return available, "sandbox-exec", reason
    return False, "unavailable", f"unsupported operating system: {system}"


def apply_resource_limits() -> None:
    """Apply conservative limits in the worker before it runs user code."""
    limits = (
        (resource.RLIMIT_CPU, (60, 60)),
        (resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3)),
        (resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2)),
        (resource.RLIMIT_NOFILE, (64, 64)),
    )
    for kind, values in limits:
        try:
            resource.setrlimit(kind, values)
        except (OSError, ValueError):
            continue


def _dependency_paths() -> list[str]:
    """Return existing site-package roots needed by the isolated worker."""
    paths: list[str] = []
    for candidate in sys.path:
        if not candidate.endswith("site-packages"):
            continue
        if Path(candidate).is_dir():
            paths.append(candidate)
    return paths


def _interpreter_mount() -> tuple[Path | None, str]:
    """Map a virtualenv interpreter to a stable in-sandbox path."""
    executable = Path(sys.executable)
    if executable.parent.name == "bin" and executable.parent.parent not in {
        Path("/usr"),
        Path("/usr/local"),
    }:
        return executable.parent.parent, f"/python-venv/bin/{executable.name}"
    return None, str(executable)


def _seatbelt_path(path: Path | str) -> str:
    """Quote a trusted path for a Seatbelt profile literal."""
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _worker_args(
    *,
    source_path: Path,
    brep_path: Path,
    metadata_path: Path,
    assembly_path: Path | None,
    worker_path: Path,
    logical_case_path: str,
    backend: str,
    placements_path: Path | None = None,
) -> list[str]:
    isolated = backend.startswith("bubblewrap")
    args = [
        "-I",
        "-S",
        "/worker.py" if isolated else str(worker_path),
        "--source",
        "/work/source.py" if isolated else str(source_path),
        "--brep",
        "/work/case.brep" if isolated else str(brep_path),
        "--metadata",
        "/work/metadata.json" if isolated else str(metadata_path),
        "--logical-path",
        logical_case_path,
        "--tools",
        "/sandbox/tools" if isolated else str(worker_path.parents[1]),
    ]
    if assembly_path is not None:
        args.extend(["--assembly", "/work/assembly.json" if isolated else str(assembly_path)])
    if placements_path is not None:
        args.extend(["--placements", "/work/placements.json" if isolated else str(placements_path)])
    return args


def _bubblewrap_command(
    *,
    bwrap: str,
    worker_path: Path,
    sandbox_root: Path,
    venv_root: Path | None,
    python_internal: str,
    args: list[str],
    dependency_paths: list[str],
    seccomp_fd: int,
    unshare: str | None = None,
) -> list[str]:
    if unshare is None:
        command = [bwrap, "--die-with-parent", "--unshare-all", "--unshare-net"]
    else:
        command = [
            unshare,
            "--user",
            "--map-root-user",
            "--net",
            bwrap,
            "--die-with-parent",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-cgroup-try",
        ]
    command.extend(
        [
            "--seccomp",
            str(seccomp_fd),
            "--new-session",
            "--dir",
            "/sandbox",
            "--ro-bind",
            str(worker_path),
            "/worker.py",
            "--ro-bind",
            str(worker_path.parents[1]),
            "/sandbox/tools",
            "--bind",
            str(sandbox_root),
            "/work",
            "--tmpfs",
            "/tmp",  # noqa: S108  # sandbox-private tmpfs mount point.
            "--dir",
            "/deps",
        ]
    )
    for runtime_path in (Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64"), Path("/etc")):
        if runtime_path.is_dir():
            command.extend(["--ro-bind", str(runtime_path), str(runtime_path)])
    if venv_root is not None:
        command.extend(["--ro-bind", str(venv_root), "/python-venv"])
    for index, path in enumerate(dependency_paths):
        command.extend(["--ro-bind", path, f"/deps/{index}"])
        args.extend(["--site-packages", f"/deps/{index}"])
    command.extend(["--chdir", "/work", python_internal, *args])
    return command


def _seatbelt_profile(
    *,
    python_host: Path,
    worker_path: Path,
    sandbox_root: Path,
    venv_root: Path | None,
    dependency_paths: list[str],
) -> str:
    sandbox_paths = _path_variants(sandbox_root)
    profile = (
        "(version 1)"
        " (deny default)"
        ' (import "system.sb")'
        f"{_macos_python_exec_rules(python_host)}"
        " (allow signal (target self))"
        " (allow sysctl-read)"
        f"{_macos_metadata_rules(python_host, worker_path, *sandbox_paths, *map(Path, dependency_paths))}"
        f' (allow file-read* (literal "{_seatbelt_path(python_host)}"))'
        f' (allow file-read* (subpath "{_seatbelt_path(worker_path.parents[1])}"))'
        " (deny network*)"
    )
    for path in sandbox_paths:
        profile += f' (allow file-read* (subpath "{_seatbelt_path(path)}"))'
        profile += f' (allow file-write* (subpath "{_seatbelt_path(path)}"))'
    for runtime_path in (
        Path("/usr"),
        Path("/System/Library"),
        Path("/Library/Frameworks"),
        python_host.parent.parent,
    ):
        if runtime_path.is_dir():
            profile += f' (allow file-read* (subpath "{_seatbelt_path(runtime_path)}"))'
    if venv_root is not None:
        profile += f' (allow file-read* (subpath "{_seatbelt_path(venv_root)}"))'
    for path in dependency_paths:
        profile += f' (allow file-read* (subpath "{_seatbelt_path(path)}"))'
    return profile


def build_command(
    *,
    source_path: Path,
    brep_path: Path,
    metadata_path: Path,
    assembly_path: Path | None = None,
    placements_path: Path | None = None,
    worker_path: Path,
    logical_case_path: str,
    sandbox_root: Path,
    backend: str,
    seccomp_fd: int | None = None,
) -> list[str]:
    """Build the command for an already-probed OS backend."""
    python_host = _resolved_python_host()
    _venv_root, python_internal = _interpreter_mount()
    dependency_paths = _dependency_paths()
    if backend.startswith("bubblewrap"):
        if seccomp_fd is None:
            raise RuntimeError("Linux sandbox requires a process-creation seccomp filter")
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            raise FileNotFoundError("bubblewrap is not installed")
        unshare = None
        if backend == "bubblewrap-unshare":
            unshare = shutil.which("unshare")
            if unshare is None:
                raise FileNotFoundError("unshare is not installed")
        return _bubblewrap_command(
            bwrap=bwrap,
            unshare=unshare,
            worker_path=worker_path,
            sandbox_root=sandbox_root,
            venv_root=_venv_root,
            python_internal=python_internal,
            args=_worker_args(
                source_path=source_path,
                brep_path=brep_path,
                metadata_path=metadata_path,
                assembly_path=assembly_path,
                placements_path=placements_path,
                worker_path=worker_path,
                logical_case_path=logical_case_path,
                backend=backend,
            ),
            dependency_paths=dependency_paths,
            seccomp_fd=seccomp_fd,
        )
    if backend == "sandbox-exec":
        sandbox_exec = _trusted_macos_sandbox_exec()
        if sandbox_exec is None:
            raise FileNotFoundError("sandbox-exec is not installed")
        args = _worker_args(
            source_path=source_path,
            brep_path=brep_path,
            metadata_path=metadata_path,
            assembly_path=assembly_path,
            placements_path=placements_path,
            worker_path=worker_path,
            logical_case_path=logical_case_path,
            backend=backend,
        )
        for path in dependency_paths:
            args.extend(["--site-packages", path])
        profile = _seatbelt_profile(
            python_host=python_host,
            worker_path=worker_path,
            sandbox_root=sandbox_root,
            venv_root=_venv_root,
            dependency_paths=dependency_paths,
        )
        # Runtime macOS qualification is required before enabling this path.
        return [sandbox_exec, "-p", profile, str(python_host), *args]
    raise ValueError(f"unsupported sandbox backend: {backend}")
