"""Isolated Build123d worker.

This is the only module in ``tools`` allowed to execute generated source.  It
is launched by :mod:`cadkit.case_execution` through a mandatory OS sandbox.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast


def _bootstrap_import_paths() -> None:
    """Expose only the trusted launcher-provided import roots before package imports."""
    for index, argument in enumerate(sys.argv[:-1]):
        if argument in {"--tools", "--site-packages"}:
            path = sys.argv[index + 1]
            if path not in sys.path:
                sys.path.insert(0, path)


_bootstrap_import_paths()

from cadkit._sandbox import apply_resource_limits  # noqa: E402

apply_resource_limits()

MAX_METADATA_BYTES = 16 * 1024
from cadkit.assembly import (  # noqa: E402
    MAX_ASSEMBLY_BYTES,
    MAX_ASSEMBLY_DEPTH,
    MAX_ASSEMBLY_LIST_ITEMS,
    MAX_ASSEMBLY_NODES,
    MAX_ASSEMBLY_NUMBER_ABS,
    MAX_ASSEMBLY_OBJECT_KEYS,
    MAX_ASSEMBLY_ROUTES,
    MAX_ASSEMBLY_STRING,
)
from cadkit.constraints import MAX_PLACEMENTS_BYTES, Placements  # noqa: E402

JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
JsonObject = dict[str, JSONValue]


def _part_flag(part: object, name: str) -> bool:
    """Read a Build123d flag across the method/property API transition."""
    value = getattr(part, name)
    return bool(value() if callable(value) else value)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--brep", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--assembly", type=Path)
    parser.add_argument("--placements", type=Path)
    parser.add_argument("--logical-path", required=True)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--site-packages", action="append", default=[])
    return parser.parse_args()


def _load_part(
    source_path: Path, logical_path: str, tools_path: Path
) -> tuple[Any, Any, Placements | None]:
    """Run the source and find its documented Part result."""
    from build123d import Part

    if str(tools_path) not in sys.path:
        sys.path.insert(0, str(tools_path))
    namespace: dict[str, Any] = {
        "__name__": "__case_exec__",
        "__file__": logical_path,
    }
    source = source_path.read_text(encoding="utf-8")
    exec(compile(source, logical_path, "exec"), namespace)  # noqa: S102
    candidate = namespace.get("case") or namespace.get("part")
    build_part = namespace.get("bp")
    if build_part is not None and hasattr(build_part, "part"):
        candidate = build_part.part
    if not isinstance(candidate, Part):
        raise TypeError("generated case did not produce a Build123d Part")
    if _part_flag(candidate, "is_null") or not _part_flag(candidate, "is_valid"):
        raise ValueError("generated case produced an invalid Build123d Part")
    placements: Placements | None = None
    if "PLACEMENTS" in namespace:
        value = namespace["PLACEMENTS"]
        if type(value) is not Placements:
            raise TypeError("PLACEMENTS must be an exact cadkit.constraints.Placements instance")
        placements = value
    return candidate, namespace.get("ASSEMBLY_SPEC"), placements


def _scene_payload(value: Any) -> dict[str, Any] | None:
    """Convert only a bounded canonical scene record into JSON data."""
    if value is None:
        return None
    payload = value.to_dict() if hasattr(value, "to_dict") else value
    if not isinstance(payload, dict):
        raise TypeError("ASSEMBLY_SPEC must be a scene object or dictionary")
    payload = cast(JsonObject, payload)
    nodes = payload.get("nodes")
    routes = payload.get("routes")
    if not isinstance(nodes, list) or not isinstance(routes, list):
        raise ValueError("ASSEMBLY_SPEC must contain nodes and routes lists")
    nodes = cast(list[JSONValue], nodes)
    routes = cast(list[JSONValue], routes)
    if len(nodes) > MAX_ASSEMBLY_NODES or len(routes) > MAX_ASSEMBLY_ROUTES:
        raise ValueError("ASSEMBLY_SPEC exceeds node or route limits")
    _validate_json_shape(payload)
    from cadkit.assembly import AssemblyScene

    scene = AssemblyScene.from_dict(payload)
    if scene.route_signal_errors():
        raise ValueError("ASSEMBLY_SPEC contains invalid route signals")
    return scene.to_dict()


def _validate_json_shape(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_ASSEMBLY_DEPTH:
        raise ValueError("ASSEMBLY_SPEC nesting is too deep")
    if isinstance(value, bool):
        return
    if isinstance(value, str):
        _validate_json_string(value)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_json_number(value)
    elif isinstance(value, dict):
        _validate_json_object(cast(dict[object, Any], value), depth)
    elif isinstance(value, list):
        _validate_json_list(cast(list[Any], value), depth)
    elif value is not None:
        raise ValueError("ASSEMBLY_SPEC contains a non-JSON value")


def _validate_json_string(value: str) -> None:
    if len(value) > MAX_ASSEMBLY_STRING:
        raise ValueError("ASSEMBLY_SPEC contains an oversized string")


def _validate_json_number(value: int | float) -> None:
    import math

    if not math.isfinite(float(value)) or abs(float(value)) > MAX_ASSEMBLY_NUMBER_ABS:
        raise ValueError("ASSEMBLY_SPEC contains an invalid number")


def _validate_json_object(value: dict[object, Any], depth: int) -> None:
    if len(value) > MAX_ASSEMBLY_OBJECT_KEYS:
        raise ValueError("ASSEMBLY_SPEC contains an oversized object")
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > MAX_ASSEMBLY_STRING:
            raise ValueError("ASSEMBLY_SPEC contains an invalid key")
        _validate_json_shape(item, depth=depth + 1)


def _validate_json_list(value: list[Any], depth: int) -> None:
    if len(value) > MAX_ASSEMBLY_LIST_ITEMS:
        raise ValueError("ASSEMBLY_SPEC contains an oversized list")
    for item in value:
        _validate_json_shape(item, depth=depth + 1)


def _write_result(
    part: Any,
    assembly: Any,
    brep_path: Path,
    metadata_path: Path,
    assembly_path: Path | None,
    placements: Placements | None = None,
    placements_path: Path | None = None,
) -> None:
    from build123d import export_brep  # pyright: ignore[reportUnknownVariableType]

    export_brep_func: Any = cast(Any, export_brep)
    if not export_brep_func(part, brep_path):
        raise OSError("failed to write BREP artifact")
    metadata = {
        "format": "build123d-brep",
        "type": "Part",
        "volume_mm3": float(part.volume),
        "assembly_present": assembly is not None,
    }
    encoded = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValueError("worker metadata exceeds size limit")
    metadata_path.write_bytes(encoded)
    if assembly_path is not None and assembly is not None:
        payload = _scene_payload(assembly)
        if payload is not None:
            encoded_scene = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if len(encoded_scene) > MAX_ASSEMBLY_BYTES:
                raise ValueError("ASSEMBLY_SPEC artifact exceeds size limit")
            assembly_path.write_bytes(encoded_scene)
    if placements_path is not None and placements is not None:
        encoded_placements = placements.to_json().encode("utf-8")
        if len(encoded_placements) > MAX_PLACEMENTS_BYTES:
            raise ValueError("PLACEMENTS artifact exceeds size limit")
        placements_path.write_bytes(encoded_placements)


def main() -> int:
    args = _parse_args()
    for path in args.site_packages:
        if path not in sys.path:
            sys.path.insert(0, path)
    part, assembly, placements = _load_part(args.source, args.logical_path, args.tools)
    _write_result(
        part,
        assembly,
        args.brep,
        args.metadata,
        args.assembly,
        placements=placements,
        placements_path=args.placements,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
