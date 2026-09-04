"""Project canonical assembly metadata without executing case source.

A canonical assembly is accepted only as a bounded, already-validated artifact
emitted by the sandboxed case worker; this module never executes generated
source or patches CAD globals.  :func:`has_assembly_spec` only parses source to
detect the required declaration.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, cast

from ._case_limits import MAX_SOURCE_BYTES
from .assembly import (
    CONTROL_COMPONENT_IDS,
    CONTROL_IDS,
    CONTROL_KINDS,
    CONTROL_ROLES,
)


class ManifestSourceError(ValueError):
    """The canonical editor metadata is unsafe or has an invalid shape."""


def _validate_assembly_header(assembly: dict[str, Any]) -> None:
    expected = {
        "schema": "cadkit.assembly",
        "schema_version": "1.0",
        "version": "1.0",
        "units": "mm",
        "coordinate_system": "case_local_lower_left_xyz",
    }
    if any(assembly.get(name) != value for name, value in expected.items()):
        raise ManifestSourceError("canonical assembly header is invalid")


def _assembly_nodes(
    assembly: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    nodes_value = assembly.get("nodes")
    if not isinstance(nodes_value, list):
        raise ManifestSourceError("canonical assembly nodes must be a list")
    nodes: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for raw_node in cast(list[Any], nodes_value):
        raw_node_value: Any = raw_node
        if not isinstance(raw_node_value, dict):
            raise ManifestSourceError("canonical assembly nodes must have string IDs")
        node = cast(dict[str, Any], raw_node_value)
        node_id_value: Any = node.get("id")
        if not isinstance(node_id_value, str):
            raise ManifestSourceError("canonical assembly nodes must have string IDs")
        node_id = node_id_value
        if node_id in by_id:
            raise ManifestSourceError(f"duplicate canonical assembly node: {node_id}")
        nodes.append(node)
        by_id[node_id] = node
    return nodes, by_id


def _validate_control_identity(
    nodes: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]
) -> tuple[str, ...]:
    expected_ids = tuple(f"control.{control_id}" for control_id in CONTROL_IDS)
    movable_ids = tuple(node["id"] for node in nodes if node.get("mobility") == "constrained_xy")
    if movable_ids != expected_ids:
        raise ManifestSourceError("canonical manifest requires the six ordered movable controls")
    for node_id, kind, role, component_id in zip(
        expected_ids,
        CONTROL_KINDS,
        CONTROL_ROLES,
        CONTROL_COMPONENT_IDS,
        strict=True,
    ):
        node = by_id[node_id]
        metadata_value = node.get("metadata")
        if node.get("kind") != kind or not isinstance(metadata_value, dict):
            raise ManifestSourceError(f"canonical control identity is invalid: {node_id}")
        metadata = cast(dict[str, Any], metadata_value)
        if metadata.get("control_role") != role or metadata.get("component_id") != component_id:
            raise ManifestSourceError(f"canonical control identity is invalid: {node_id}")
    return expected_ids


def _manifest_element(node: dict[str, Any], control_ids: tuple[str, ...]) -> dict[str, Any]:
    dimensions = cast(dict[str, Any], node["physical_dimensions"])
    transform = cast(dict[str, Any], node["transform"])
    mobility = str(node["mobility"])
    visual = dict(cast(dict[str, Any], node.get("visual", {})))
    is_control = node["id"] in control_ids
    metadata = dict(cast(dict[str, Any], node.get("metadata", {})))
    element: dict[str, Any] = {
        "id": node["id"],
        "kind": node["kind"],
        "position": list(transform["position_mm"]),
        "size": visual.get("diameter_mm", dimensions["x_mm"]),
        "rotation": transform["rotation_deg"][2],
        "mobility": "movable" if is_control else mobility,
        "canonical_mobility": mobility,
        "visual": visual,
        "parent_id": node.get("parent_id"),
        "provenance": node["provenance"],
        "status": node["status"],
        "physical_dimensions": dimensions,
        "keep_out_dimensions": node.get("keep_out_dimensions"),
        "visual_ref": node.get("visual_ref", ""),
        "proxy_ref": node.get("proxy_ref", ""),
        "metadata": metadata,
    }
    if is_control:
        element["control_role"] = metadata["control_role"]
        element["component_id"] = metadata["component_id"]
    return element


def _manifest_from_assembly(assembly: dict[str, Any]) -> dict[str, Any]:
    _validate_assembly_header(assembly)
    nodes, by_id = _assembly_nodes(assembly)
    control_ids = _validate_control_identity(nodes, by_id)
    elements = [_manifest_element(node, control_ids) for node in nodes]
    return {
        "elements": elements,
        "coordinate_system": "build123d_origin",
        "units": assembly.get("units", "mm"),
        "layout": {
            "coordinate_system": "case_local_xy",
            "origin": "lower_left",
            "scale": "uniform_mm",
            "dimensions_source": "assembly_spec",
        },
        "assembly": assembly,
        "provisional": any(node.get("status") == "provisional" for node in nodes),
        "case_dimensions": assembly["case_dimensions"],
    }


def has_assembly_spec(path: Path) -> bool:
    """Return whether source declares the runtime-only canonical scene name."""
    source = Path(path).read_text(encoding="utf-8", errors="strict")
    tree = ast.parse(source, filename=str(path))
    return any(isinstance(node, ast.Name) and node.id == "ASSEMBLY_SPEC" for node in ast.walk(tree))


def from_case_path(path: Path, assembly: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a manifest from the validated canonical sandbox artifact."""
    source_path = Path(path)
    if source_path.name != "case.py" or source_path.is_symlink() or not source_path.is_file():
        raise FileNotFoundError(f"case.py not found: {path}")
    if source_path.stat().st_size > MAX_SOURCE_BYTES:
        raise ManifestSourceError("case source exceeds 256 KiB")
    if assembly is None:
        raise ManifestSourceError("canonical assembly artifact is required")
    return _manifest_from_assembly(assembly)


def from_source(path: Path) -> dict[str, Any]:
    """Compatibility shim for callers that must provide a canonical artifact."""
    return from_case_path(path)


__all__ = ["ManifestSourceError", "from_case_path", "from_source", "has_assembly_spec"]
