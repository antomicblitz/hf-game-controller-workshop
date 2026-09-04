"""Fail-closed execution boundary for generated Build123d cases."""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from . import _sandbox
from ._bounded_subprocess import (
    BoundedOutputLimitExceeded,
    BoundedProcessTimeout,
    run_bounded,
)
from ._case_limits import MAX_SOURCE_BYTES
from .assembly import (
    MAX_ASSEMBLY_BYTES,
    MAX_ASSEMBLY_DEPTH,
    MAX_ASSEMBLY_LIST_ITEMS,
    MAX_ASSEMBLY_NODES,
    MAX_ASSEMBLY_NUMBER_ABS,
    MAX_ASSEMBLY_OBJECT_KEYS,
    MAX_ASSEMBLY_ROUTES,
    MAX_ASSEMBLY_STRING,
)
from .constraints import MAX_PLACEMENTS_BYTES, Placements

MAX_BREP_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024
JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
JsonObject = dict[str, JSONValue]
DEFAULT_TIMEOUT_SECONDS = 60


def _part_flag(part: object, name: str) -> bool:
    """Read a Build123d flag across the method/property API transition."""
    value = getattr(part, name)
    return bool(value() if callable(value) else value)


@dataclass(frozen=True)
class SandboxStatus:
    """Safe, non-sensitive description of the active OS backend."""

    available: bool
    backend: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Return the public health representation."""
        return {
            "available": self.available,
            "backend": self.backend,
            "reason": self.reason,
        }


class CaseExecutionError(RuntimeError):
    """The sandboxed case failed or produced an invalid artifact."""


class CaseExecutionTimeout(CaseExecutionError):
    """The sandbox worker exceeded its wall-clock budget."""


class SandboxUnavailable(CaseExecutionError):
    """No supported, capability-probed OS sandbox is available."""


@dataclass(frozen=True)
class CaseArtifacts:
    """The bounded runtime artifacts emitted by one sandbox worker."""

    part: Any
    assembly: dict[str, Any] | None
    placements: Placements | None = None


def sandbox_status() -> SandboxStatus:
    """Probe the host backend and return only stable, safe status data."""
    available, backend, reason = _sandbox.status()
    return SandboxStatus(available=available, backend=backend, reason=reason)


def require_sandbox() -> SandboxStatus:
    """Require a capability-probed OS sandbox before any case work."""
    status = sandbox_status()
    if not status.available:
        raise SandboxUnavailable(f"sandbox_unavailable: {status.reason}")
    return status


def _private_directory(parent: Path | None = None) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cadkit-case-", dir=parent))
    path.chmod(stat.S_IRWXU)
    home = path / "home"
    home.mkdir(mode=stat.S_IRWXU)
    (path / "tmp").mkdir(mode=stat.S_IRWXU)
    return path


def _read_source_bytes(path: Path) -> bytes:
    if path.name != "case.py" or path.is_symlink():
        raise CaseExecutionError("case path must be a regular case.py file")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source_file:
            info = os.fstat(source_file.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise CaseExecutionError("case path must be a regular case.py file")
            source_bytes = source_file.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise CaseExecutionError(f"unable to inspect case source: {exc}") from exc
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise CaseExecutionError("case source exceeds 256 KiB")
    return source_bytes


def _logical_case_path(path: Path, backend: str) -> str:
    if not backend.startswith("bubblewrap"):
        return str(path)
    session = Path(__file__).resolve().parents[2]
    try:
        relative = path.resolve().relative_to(session)
    except ValueError:
        return "/sandbox/examples/generated/case.py"
    return f"/sandbox/{relative.as_posix()}"


def _worker_environment(root: Path, backend: str) -> dict[str, str]:
    """Return the exact environment contract for a worker."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/work/home" if backend.startswith("bubblewrap") else str(root / "home"),
        "TMPDIR": "/work/tmp" if backend.startswith("bubblewrap") else str(root / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise CaseExecutionError("worker metadata exceeds 16 KiB")
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except CaseExecutionError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseExecutionError("worker did not emit valid metadata") from exc
    if not isinstance(metadata, dict):
        raise CaseExecutionError("worker metadata does not describe a Part")
    metadata = cast(dict[str, Any], metadata)
    if metadata.get("type") != "Part":
        raise CaseExecutionError("worker metadata does not describe a Part")
    return metadata


def _read_assembly(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        if path.stat().st_size > MAX_ASSEMBLY_BYTES:
            raise CaseExecutionError("worker assembly exceeds 256 KiB")
        value = json.loads(path.read_text(encoding="utf-8"))
    except CaseExecutionError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseExecutionError("worker did not emit valid assembly metadata") from exc
    if not isinstance(value, dict):
        raise CaseExecutionError("worker assembly metadata is not an object")
    value = cast(JsonObject, value)
    _validate_assembly_shape(value)
    from .assembly import AssemblyScene

    try:
        scene = AssemblyScene.from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise CaseExecutionError("worker assembly metadata has an invalid shape") from exc
    if (
        len(scene.nodes) > MAX_ASSEMBLY_NODES
        or len(scene.routes) > MAX_ASSEMBLY_ROUTES
        or scene.route_signal_errors()
    ):
        raise CaseExecutionError("worker assembly metadata exceeds validation bounds")
    return scene.to_dict()


def _read_placements(path: Path) -> Placements | None:
    """Read and strictly validate the optional placements artifact."""
    if path.is_symlink():
        raise CaseExecutionError("worker placements artifact is not a regular file")
    if not path.exists():
        return None
    if not path.is_file():
        raise CaseExecutionError("worker placements artifact is not a regular file")
    try:
        if path.stat().st_size > MAX_PLACEMENTS_BYTES:
            raise CaseExecutionError("worker placements exceeds 64 KiB")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json,
        )
        placements = Placements.from_dict(value)
    except CaseExecutionError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CaseExecutionError("worker placements metadata has an invalid shape") from exc
    return placements


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r}")


def _validate_assembly_shape(value: Any, *, depth: int = 0) -> None:
    """Validate protocol shape before any scene object is constructed."""
    if depth > MAX_ASSEMBLY_DEPTH:
        raise CaseExecutionError("worker assembly metadata is too deeply nested")
    if isinstance(value, bool):
        return
    if isinstance(value, str):
        _validate_assembly_string(value)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_assembly_number(value)
    elif isinstance(value, dict):
        _validate_assembly_object(cast(dict[object, Any], value), depth)
    elif isinstance(value, list):
        _validate_assembly_list(cast(list[Any], value), depth)
    elif value is not None:
        raise CaseExecutionError("worker assembly metadata contains a non-JSON value")


def _validate_assembly_string(value: str) -> None:
    if len(value) > MAX_ASSEMBLY_STRING:
        raise CaseExecutionError("worker assembly metadata contains an oversized string")


def _validate_assembly_number(value: int | float) -> None:
    if not math.isfinite(float(value)) or abs(float(value)) > MAX_ASSEMBLY_NUMBER_ABS:
        raise CaseExecutionError("worker assembly metadata contains an invalid number")


def _validate_assembly_object(value: dict[object, Any], depth: int) -> None:
    if len(value) > MAX_ASSEMBLY_OBJECT_KEYS:
        raise CaseExecutionError("worker assembly metadata contains an oversized object")
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > MAX_ASSEMBLY_STRING:
            raise CaseExecutionError("worker assembly metadata contains an invalid key")
        _validate_assembly_shape(item, depth=depth + 1)


def _validate_assembly_list(value: list[Any], depth: int) -> None:
    if len(value) > MAX_ASSEMBLY_LIST_ITEMS:
        raise CaseExecutionError("worker assembly metadata contains an oversized list")
    for item in value:
        _validate_assembly_shape(item, depth=depth + 1)


def execute_case_path(path: Path | str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    """Execute a regular ``case.py`` through the mandatory OS sandbox."""
    return execute_case_artifacts(path, timeout_seconds=timeout_seconds).part


def execute_case_artifacts(
    path: Path | str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> CaseArtifacts:
    """Execute a case and return its Part plus bounded canonical-scene data."""
    status = require_sandbox()
    source_path = Path(path)
    source_bytes = _read_source_bytes(source_path)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive finite number")

    worker_path = Path(__file__).with_name("_case_worker.py")
    root = _private_directory()
    try:
        private_source = root / "source.py"
        private_source.write_bytes(source_bytes)
        private_source.chmod(stat.S_IRUSR | stat.S_IWUSR)
        brep_path = root / "case.brep"
        metadata_path = root / "metadata.json"
        assembly_path = root / "assembly.json"
        placements_path = root / "placements.json"
        with _sandbox.execution_filter(status.backend) as seccomp_fd:
            command = _sandbox.build_command(
                source_path=private_source,
                brep_path=brep_path,
                metadata_path=metadata_path,
                assembly_path=assembly_path,
                placements_path=placements_path,
                worker_path=worker_path,
                logical_case_path=_logical_case_path(source_path, status.backend),
                sandbox_root=root,
                backend=status.backend,
                seccomp_fd=seccomp_fd,
            )
            environment = _worker_environment(root, status.backend)
            try:
                result = run_bounded(
                    command,
                    cwd=root,
                    env=environment,
                    timeout_seconds=timeout_seconds,
                    stdout_limit=64 * 1024,
                    stderr_limit=64 * 1024,
                    watched_files={
                        brep_path: MAX_BREP_BYTES,
                        metadata_path: MAX_METADATA_BYTES,
                        assembly_path: MAX_ASSEMBLY_BYTES,
                        placements_path: MAX_PLACEMENTS_BYTES,
                    },
                    pass_fds=(seccomp_fd,) if seccomp_fd is not None else (),
                )
            except BoundedProcessTimeout as exc:
                raise CaseExecutionTimeout(
                    f"case execution timed out after {timeout_seconds:g} seconds"
                ) from exc
            except BoundedOutputLimitExceeded as exc:
                raise CaseExecutionError(
                    f"sandbox worker exceeded a bounded output: {exc}"
                ) from exc
            except OSError as exc:
                raise CaseExecutionError(f"sandbox worker could not start: {exc}") from exc
        if result.returncode != 0:
            detail = (
                (result.stderr or result.stdout)
                .decode("utf-8", errors="replace")
                .strip()
                .splitlines()
            )
            message = detail[-1][:400] if detail else "worker exited unsuccessfully"
            raise CaseExecutionError(f"sandbox case failed: {message}")
        if not brep_path.is_file():
            raise CaseExecutionError("worker did not emit a bounded BREP artifact")
        brep_size = brep_path.stat().st_size
        if brep_size == 0:
            raise CaseExecutionError("worker emitted an empty BREP artifact")
        if brep_size > MAX_BREP_BYTES:
            raise CaseExecutionError("worker did not emit a bounded BREP artifact")
        _read_metadata(metadata_path)
        from build123d import Part, import_brep  # pyright: ignore[reportUnknownVariableType]

        imported = cast(Any, import_brep(brep_path))
        part = Part(imported)
        if _part_flag(part, "is_null") or not _part_flag(part, "is_valid"):
            raise CaseExecutionError("worker BREP is not a valid Build123d Part")
        metadata = _read_metadata(metadata_path)
        assembly = _read_assembly(assembly_path) if metadata.get("assembly_present") else None
        placements = _read_placements(placements_path)
        return CaseArtifacts(part=part, assembly=assembly, placements=placements)
    finally:
        shutil.rmtree(root, ignore_errors=True)


__all__ = [
    "CaseArtifacts",
    "CaseExecutionError",
    "CaseExecutionTimeout",
    "SandboxStatus",
    "SandboxUnavailable",
    "execute_case_artifacts",
    "execute_case_path",
    "require_sandbox",
    "sandbox_status",
]
