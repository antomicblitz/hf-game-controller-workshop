"""Deterministic, offline glTF preview of the canonical assembly scene.

The exporter has no external mesh injection seam. The exact Build123d case
Part may be injected for ``case.shell``; every other rendered node uses the
deterministic procedural proxies in this module. The asset registry remains
research/intake metadata for a future trusted importer. The exporter writes
GLB directly so the editor does not need a network service or a glTF
dependency at runtime.
"""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, TypeAlias, cast

from cadkit.assembly import (
    MAX_ASSEMBLY_WAYPOINTS,
    AssemblyNode,
    AssemblyScene,
    WireRoute,
)

Point3: TypeAlias = tuple[float, float, float]
Triangle: TypeAlias = tuple[int, int, int]
AccessorBounds: TypeAlias = tuple[Sequence[float] | float, Sequence[float] | float]
MAX_ROUTE_SEGMENTS = 4_096
MAX_TOTAL_VERTICES = 200_000
MAX_TOTAL_TRIANGLES = 300_000
MAX_GLB_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class _MeshData:
    """A small renderer-neutral triangle mesh in millimetres.

    Vertices are local to the glTF node. This type is intentionally private:
    callers may inject only the exact Build123d case Part, never
    renderer-neutral mesh data.
    """

    vertices: tuple[Point3, ...]
    triangles: tuple[Triangle, ...]
    normals: tuple[Point3, ...] | None = None

    def __post_init__(self) -> None:
        if not self.vertices or not self.triangles:
            raise ValueError("mesh must contain vertices and triangles")
        if any(
            len(point) != 3 or not all(math.isfinite(value) for value in point)
            for point in self.vertices
        ):
            raise ValueError("mesh vertices must be finite 3D points")
        if any(
            len(triangle) != 3
            or any(index < 0 or index >= len(self.vertices) for index in triangle)
            for triangle in self.triangles
        ):
            raise ValueError("mesh triangle index out of range")
        if self.normals is not None and len(self.normals) != len(self.vertices):
            raise ValueError("mesh normals must match the vertex count")
        if self.normals is not None and any(
            len(point) != 3 or not all(math.isfinite(value) for value in point)
            for point in self.normals
        ):
            raise ValueError("mesh normals must be finite 3D points")


@dataclass(frozen=True)
class _Material:
    name: str
    color: tuple[float, float, float, float]
    alpha_mode: str = "OPAQUE"
    metallic: float = 0.0
    roughness: float = 0.7


@dataclass
class _MeshBudget:
    vertices: int = 0
    triangles: int = 0

    def add(self, mesh: _MeshData, label: str) -> None:
        self.vertices += len(mesh.vertices)
        self.triangles += len(mesh.triangles)
        if self.vertices > MAX_TOTAL_VERTICES:
            raise ValueError(f"GLB vertex limit exceeded while rendering {label}")
        if self.triangles > MAX_TOTAL_TRIANGLES:
            raise ValueError(f"GLB triangle limit exceeded while rendering {label}")


def _check_mesh_size(mesh: _MeshData, label: str) -> None:
    if len(mesh.vertices) > MAX_TOTAL_VERTICES:
        raise ValueError(f"GLB vertex limit exceeded while rendering {label}")
    if len(mesh.triangles) > MAX_TOTAL_TRIANGLES:
        raise ValueError(f"GLB triangle limit exceeded while rendering {label}")


def _render_node(
    node: AssemblyNode,
    scene: AssemblyScene,
    injected: Mapping[str, Any],
    material_id: Callable[[_Material], int],
    budget: _MeshBudget,
    tolerance: float,
    include_keepouts: bool,
) -> tuple[str, dict[str, Any], list[tuple[_MeshData, int]]]:
    if node.id in {"case.bottom", "case.top", "usb.opening"}:
        return node.id, _node_json(node), []
    supplied = injected.get(node.id)
    if supplied is None:
        meshes = _proxy_meshes(node)
    else:
        tessellated = _tessellate_part(supplied, tolerance=tolerance)
        _check_mesh_size(tessellated, node.id)
        meshes = [_case_mesh_to_node_local(tessellated, node, scene)]
    for mesh in meshes:
        _check_mesh_size(mesh, node.id)
        budget.add(mesh, node.id)
    parts = [(mesh, material_id(_material_for_node(node))) for mesh in meshes]
    if include_keepouts and node.keep_out_dimensions is not None:
        keepout = _box_mesh(_dimensions(node.keep_out_dimensions))
        budget.add(keepout, f"{node.id} keep-out")
        parts.append((keepout, material_id(_keepout_material())))
    return node.id, _node_json(node), parts


def _render_route(
    route: WireRoute,
    scene: AssemblyScene,
    material_id: Callable[[_Material], int],
    budget: _MeshBudget,
) -> tuple[str, dict[str, Any], list[tuple[_MeshData, int]]]:
    waypoints = scene.route_waypoints(route)
    if len(waypoints) > MAX_ASSEMBLY_WAYPOINTS:
        raise ValueError(f"route {route.id} exceeds the waypoint limit")
    segments = len(waypoints) - 1
    if budget.vertices + segments * 16 > MAX_TOTAL_VERTICES:
        raise ValueError(f"GLB vertex limit exceeded while rendering {route.id}")
    if budget.triangles + segments * 16 > MAX_TOTAL_TRIANGLES:
        raise ValueError(f"GLB triangle limit exceeded while rendering {route.id}")
    route_mesh = _wire_mesh(waypoints)
    budget.add(route_mesh, route.id)
    return route.id, _route_json(route), [(route_mesh, material_id(_wire_material(route.signal)))]


def build_assembled_glb(
    scene: AssemblyScene,
    *,
    case_part: Any | None = None,
    case_parts: Mapping[str, Any] | Sequence[Any] | Any | None = None,
    tolerance: float = 0.2,
    include_keepouts: bool = False,
) -> bytes:
    """Return an embedded-buffer GLB containing every scene node and route.

    ``case_part``/``case_parts`` are exact Build123d case-Part injections. A
    mapping is keyed by canonical case node id; a sequence is assigned to the
    canonical case nodes in scene order. All non-case nodes use the internal
    deterministic proxy fallback.
    """
    injected = _case_injections(scene, case_part, case_parts)
    route_segments = sum(len(route.waypoints) - 1 for route in scene.routes)
    if route_segments > MAX_ROUTE_SEGMENTS:
        raise ValueError("GLB route segment limit exceeded")
    materials: list[_Material] = []
    material_ids: dict[_Material, int] = {}
    node_records: list[tuple[str, dict[str, Any], list[tuple[_MeshData, int]]]] = []
    budget = _MeshBudget()

    def material_id(material: _Material) -> int:
        index = material_ids.get(material)
        if index is None:
            index = len(materials)
            material_ids[material] = index
            materials.append(material)
        return index

    for node in scene.nodes:
        node_records.append(
            _render_node(node, scene, injected, material_id, budget, tolerance, include_keepouts)
        )

    for route in scene.routes:
        node_records.append(_render_route(route, scene, material_id, budget))

    payload = _encode_glb(node_records, materials, scene)
    if len(payload) > MAX_GLB_BYTES:
        raise ValueError(f"GLB exceeds the {MAX_GLB_BYTES} byte limit")
    return payload


def write_assembled_glb(
    scene: AssemblyScene,
    path: str | Path,
    **kwargs: Any,
) -> bytes:
    """Write :func:`build_assembled_glb` to ``path`` and return its bytes."""
    payload = build_assembled_glb(scene, **kwargs)
    Path(path).write_bytes(payload)
    return payload


# These names make the exporter easy to discover without creating a second
# implementation for callers that use "export" terminology.
export_assembly_glb = build_assembled_glb
assemble_glb = build_assembled_glb


def _case_injections(
    scene: AssemblyScene,
    case_part: Any | None,
    case_parts: Mapping[str, Any] | Sequence[Any] | Any | None,
) -> dict[str, Any]:
    if case_part is not None and case_parts is not None:
        raise TypeError("provide case_part or case_parts, not both")
    if case_part is not None:
        return {"case.shell": _require_case_part(case_part)}
    if case_parts is None:
        return {}
    case_nodes = [
        node for node in scene.nodes if node.id in {"case.shell", "case.bottom", "case.top"}
    ]
    if isinstance(case_parts, Mapping):
        return _mapping_case_injections(cast(Mapping[str, Any], case_parts))
    if isinstance(case_parts, Sequence) and not isinstance(case_parts, (str, bytes, bytearray)):
        return _sequence_case_injections(case_nodes, cast(Sequence[Any], case_parts))
    return {"case.shell": _require_case_part(case_parts)}


def _mapping_case_injections(case_parts: Mapping[str, Any]) -> dict[str, Any]:
    aliases = {
        "case": "case.shell",
        "shell": "case.shell",
        "bottom": "case.bottom",
        "top": "case.top",
        "case.shell": "case.shell",
        "case.bottom": "case.bottom",
        "case.top": "case.top",
    }
    injections: dict[str, Any] = {}
    for key, value in case_parts.items():
        canonical = aliases.get(str(key))
        if canonical is None:
            raise ValueError(f"unknown canonical case node: {key!r}")
        injections[canonical] = _require_case_part(value)
    return injections


def _sequence_case_injections(
    case_nodes: Sequence[AssemblyNode], case_parts: Sequence[Any]
) -> dict[str, Any]:
    if len(case_parts) > len(case_nodes):
        raise ValueError("more case parts than case nodes")
    return {
        node.id: _require_case_part(value)
        for node, value in zip(case_nodes, case_parts, strict=False)
    }


def _require_case_part(value: Any) -> Any:
    from build123d import Part

    if not isinstance(value, Part):
        raise TypeError("case injection must be an exact Build123d Part")
    return value


def _tessellate_part(part: Any, *, tolerance: float) -> _MeshData:
    from cadkit.three_mf import tessellate_part_to_vertices_and_triangles

    tessellation = tessellate_part_to_vertices_and_triangles(part, tolerance=tolerance)
    return _MeshData(tessellation.vertices, tessellation.triangles)


def _case_mesh_to_node_local(
    mesh: _MeshData, node: AssemblyNode, scene: AssemblyScene
) -> _MeshData:
    """Map Build123d's centred XY case frame into a node-local frame."""
    length, width = scene.case_dimensions.x, scene.case_dimensions.y
    tx, ty, tz = node.transform.position
    vertices = tuple(
        (x + length / 2.0 - tx, y + width / 2.0 - ty, z - tz) for x, y, z in mesh.vertices
    )
    return _MeshData(vertices, mesh.triangles, mesh.normals)


def _point3(value: Sequence[float]) -> Point3:
    if len(value) != 3:
        raise ValueError("mesh point requires three values")
    return (float(value[0]), float(value[1]), float(value[2]))


def _dimensions(value: Any) -> Point3:
    return (float(value.x), float(value.y), float(value.z))


def _box_mesh(dimensions: Point3) -> _MeshData:
    x, y, z = (value / 2.0 for value in dimensions)
    vertices: tuple[Point3, ...] = (
        (-x, -y, -z),
        (x, -y, -z),
        (x, y, -z),
        (-x, y, -z),
        (-x, -y, z),
        (x, -y, z),
        (x, y, z),
        (-x, y, z),
    )
    triangles: tuple[Triangle, ...] = (
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    )
    return _MeshData(vertices, triangles)


def _cylinder_mesh(radius: float, height: float, segments: int = 12) -> _MeshData:
    vertices: list[Point3] = [(0.0, 0.0, -height / 2.0), (0.0, 0.0, height / 2.0)]
    for z in (-height / 2.0, height / 2.0):
        vertices.extend(
            (
                radius * math.cos(2.0 * math.pi * index / segments),
                radius * math.sin(2.0 * math.pi * index / segments),
                z,
            )
            for index in range(segments)
        )
    triangles: list[Triangle] = []
    bottom, top = 2, 2 + segments
    for index in range(segments):
        nxt = (index + 1) % segments
        triangles.extend(
            (
                (0, bottom + nxt, bottom + index),
                (1, top + index, top + nxt),
                (bottom + index, bottom + nxt, top + nxt),
                (bottom + index, top + nxt, top + index),
            )
        )
    return _MeshData(tuple(vertices), tuple(triangles))


def _combine_meshes(meshes: Sequence[_MeshData]) -> _MeshData:
    vertices: list[Point3] = []
    triangles: list[Triangle] = []
    for mesh in meshes:
        offset = len(vertices)
        vertices.extend(mesh.vertices)
        triangles.extend((a + offset, b + offset, c + offset) for a, b, c in mesh.triangles)
    return _MeshData(tuple(vertices), tuple(triangles))


def _proxy_meshes(node: AssemblyNode) -> list[_MeshData]:
    shape = str(node.visual.get("shape", "box"))
    x, y, z = _dimensions(node.physical_dimensions)
    if shape == "circle" or node.kind == "pbs33b_button":
        body_height = z * 0.8
        return [
            _translated_mesh(_cylinder_mesh(min(x, y, 12.0) / 2.0, body_height), (0, 0, -z * 0.1)),
            _translated_mesh(_cylinder_mesh(min(x, y) / 2.0, z * 0.1), (0, 0, z * 0.3)),
            _translated_mesh(_cylinder_mesh(min(x, y, 10.0) / 2.0, z * 0.2), (0, 0, z * 0.4)),
        ]
    if node.kind == "guuzi_action_button":
        # The action part is intentionally box-based rather than the three
        # concentric cylinders used by the PBS proxy.  Distinct proxy geometry
        # makes a mixed-component scene unambiguous in the 3D review.
        return [
            _box_mesh((x, y, z * 0.72)),
            _translated_mesh(_cylinder_mesh(min(x, y) * 0.34, z * 0.28), (0.0, 0.0, z * 0.32)),
        ]
    if node.kind == "half_size_breadboard":
        if node.metadata.get("measurement_scope") == "installed_stack":
            return [_box_mesh((x, y, z))]
        return [
            _translated_mesh(_box_mesh((x, y, z * 0.55)), (0.0, 0.0, -z * 0.225)),
            _translated_mesh(_box_mesh((x * 0.74, y * 0.58, z * 0.45)), (0.0, 0.0, z * 0.275)),
            _translated_mesh(_box_mesh((x * 0.9, y * 0.11, z * 0.45)), (0.0, -y * 0.41, z * 0.275)),
            _translated_mesh(_box_mesh((x * 0.9, y * 0.11, z * 0.45)), (0.0, y * 0.41, z * 0.275)),
        ]
    if node.kind in {"m2_5_fastener", "heat_shrink", "strain_relief"}:
        return [_cylinder_mesh(min(x, y) / 2.0, z, segments=10)]
    return [_box_mesh((x, y, z))]


def _translated_mesh(mesh: _MeshData, offset: Point3) -> _MeshData:
    return _MeshData(
        tuple(
            (point[0] + offset[0], point[1] + offset[1], point[2] + offset[2])
            for point in mesh.vertices
        ),
        mesh.triangles,
        mesh.normals,
    )


def _wire_mesh(waypoints: Sequence[Point3]) -> _MeshData:
    meshes: list[_MeshData] = []
    for start, end in pairwise(waypoints):
        meshes.append(_tube_segment(start, end, radius=0.65, segments=8))
    return _combine_meshes(meshes)


def _tube_segment(start: Point3, end: Point3, *, radius: float, segments: int) -> _MeshData:
    axis = _point3(tuple(end[index] - start[index] for index in range(3)))
    length = math.sqrt(sum(value * value for value in axis))
    if length == 0.0:
        raise ValueError("wire route contains a zero-length segment")
    direction = _point3(tuple(value / length for value in axis))
    reference = (0.0, 0.0, 1.0) if abs(direction[2]) < 0.9 else (0.0, 1.0, 0.0)
    u = _normalize(_cross(direction, reference))
    v = _cross(direction, u)
    vertices: list[Point3] = []
    for centre in (start, end):
        vertices.extend(
            _point3(
                tuple(
                    centre[index]
                    + radius
                    * (
                        u[index] * math.cos(2.0 * math.pi * ring / segments)
                        + v[index] * math.sin(2.0 * math.pi * ring / segments)
                    )
                    for index in range(3)
                )
            )
            for ring in range(segments)
        )
    triangles: list[Triangle] = []
    for ring in range(segments):
        nxt = (ring + 1) % segments
        triangles.extend(((ring, nxt, segments + nxt), (ring, segments + nxt, segments + ring)))
    return _MeshData(tuple(vertices), tuple(triangles))


def _cross(left: Point3, right: Point3) -> Point3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _normalize(value: Point3) -> Point3:
    length = math.sqrt(sum(part * part for part in value))
    if length == 0.0:
        raise ValueError("cannot normalize zero vector")
    return tuple(part / length for part in value)  # type: ignore[return-value]


def _node_json(node: AssemblyNode) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": node.id,
        "translation": list(node.transform.position),
        "extras": {
            "assembly_id": node.id,
            "kind": node.kind,
            "parent_id": node.parent_id,
            "proxy_ref": node.proxy_ref,
            "transform": node.transform.to_dict(),
        },
    }
    if any(abs(value) > 1e-12 for value in node.transform.rotation_deg):
        record["rotation"] = list(_euler_xyz_quaternion(node.transform.rotation_deg))
    return record


def _route_json(route: WireRoute) -> dict[str, Any]:
    cable = _button_signal_cable_metadata(route)
    return {
        "name": route.id,
        "extras": {
            "route_id": route.id,
            "signal": route.signal,
            "source": route.source,
            "target": route.target,
            **cable,
        },
    }


def _button_signal_cable_metadata(route: WireRoute) -> dict[str, str]:
    """Describe button-to-breadboard signal routes without adding geometry."""
    if route.source.startswith("control.") and route.target.startswith(
        "electronics.breadboard.signal_"
    ):
        return {"cable_kind": "Dupont", "termination_kind": "alligator clips"}
    return {}


def _euler_xyz_quaternion(rotation_deg: Point3) -> Point3 | tuple[float, float, float, float]:
    rx, ry, rz = (math.radians(value) / 2.0 for value in rotation_deg)
    sx, cx, sy, cy, sz, cz = (
        math.sin(rx),
        math.cos(rx),
        math.sin(ry),
        math.cos(ry),
        math.sin(rz),
        math.cos(rz),
    )
    return (
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    )


def _material_for_node(node: AssemblyNode) -> _Material:
    if node.kind in {"case_shell", "case_top"}:
        alpha = 0.24 if node.kind == "case_shell" else 0.16
        return _Material(node.kind, (0.22, 0.48, 0.72, alpha), "BLEND", roughness=0.35)
    if node.kind == "pbs33b_button":
        return _Material("pbs33b-directional", (0.94, 0.56, 0.12, 1.0), roughness=0.45)
    if node.kind == "guuzi_action_button":
        return _Material("guuzi-action", (0.17, 0.55, 0.85, 1.0), roughness=0.45)
    if node.kind == "feather_board":
        return _Material("feather-pcb", (0.08, 0.42, 0.20, 1.0), roughness=0.6)
    if node.kind == "half_size_breadboard":
        if node.metadata.get("measurement_scope") == "installed_stack":
            return _Material(
                "breadboard-preview", (0.92, 0.92, 0.88, 0.18), "BLEND", roughness=0.72
            )
        return _Material("breadboard-preview", (0.92, 0.92, 0.88, 1.0), roughness=0.72)
    if "header" in node.kind:
        return _Material("header-plastic", (0.08, 0.08, 0.09, 1.0), roughness=0.55)
    return _Material("component-proxy", (0.58, 0.62, 0.68, 1.0), roughness=0.65)


def _keepout_material() -> _Material:
    return _Material("keepout-xray", (0.96, 0.40, 0.08, 0.12), "BLEND", roughness=0.5)


_WIRE_COLORS: dict[str, tuple[float, float, float, float]] = {
    "UP": (0.90, 0.12, 0.12, 1.0),
    "DOWN": (0.12, 0.72, 0.20, 1.0),
    "RIGHT": (0.12, 0.35, 0.92, 1.0),
    "LEFT": (0.95, 0.78, 0.08, 1.0),
    "ACTION_A": (0.17, 0.55, 0.85, 1.0),
    "ACTION_B": (0.17, 0.55, 0.85, 1.0),
    "GND": (0.10, 0.10, 0.12, 1.0),
}


def _wire_material(signal: str) -> _Material:
    color = _WIRE_COLORS.get(signal, (0.72, 0.72, 0.72, 1.0))
    return _Material(f"wire-{signal}", color, roughness=0.5)


class _Binary:
    def __init__(self) -> None:
        self.data = bytearray()

    def append(self, payload: bytes) -> tuple[int, int]:
        while len(self.data) % 4:
            self.data.append(0)
        offset = len(self.data)
        self.data.extend(payload)
        if len(self.data) > MAX_GLB_BYTES:
            raise ValueError(f"GLB binary payload exceeds the {MAX_GLB_BYTES} byte limit")
        return offset, len(payload)


def _encode_glb(
    records: Sequence[tuple[str, dict[str, Any], list[tuple[_MeshData, int]]]],
    materials: Sequence[_Material],
    scene: AssemblyScene,
) -> bytes:
    binary = _Binary()
    budget = _MeshBudget()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    for _name, node_record, parts in records:
        primitives: list[dict[str, Any]] = []
        for mesh, material_index in parts:
            _check_mesh_size(mesh, node_record["name"])
            budget.add(mesh, node_record["name"])
            normals = mesh.normals or _vertex_normals(mesh)
            position_bytes = struct.pack(
                "<" + "f" * len(mesh.vertices) * 3,
                *(value for point in mesh.vertices for value in point),
            )
            normal_bytes = struct.pack(
                "<" + "f" * len(normals) * 3, *(value for point in normals for value in point)
            )
            indices = tuple(index for triangle in mesh.triangles for index in triangle)
            index_bytes = struct.pack("<" + "I" * len(indices), *indices)
            position_offset, position_length = binary.append(position_bytes)
            normal_offset, normal_length = binary.append(normal_bytes)
            index_offset, index_length = binary.append(index_bytes)
            position_view = len(buffer_views)
            buffer_views.append(
                {
                    "buffer": 0,
                    "byteOffset": position_offset,
                    "byteLength": position_length,
                    "target": 34962,
                }
            )
            normal_view = len(buffer_views)
            buffer_views.append(
                {
                    "buffer": 0,
                    "byteOffset": normal_offset,
                    "byteLength": normal_length,
                    "target": 34962,
                }
            )
            index_view = len(buffer_views)
            buffer_views.append(
                {
                    "buffer": 0,
                    "byteOffset": index_offset,
                    "byteLength": index_length,
                    "target": 34963,
                }
            )
            position_accessor = len(accessors)
            accessors.append(
                _accessor(position_view, 5126, len(mesh.vertices), "VEC3", _bounds(mesh.vertices))
            )
            normal_accessor = len(accessors)
            accessors.append(_accessor(normal_view, 5126, len(normals), "VEC3", _bounds(normals)))
            index_accessor = len(accessors)
            accessors.append(
                _accessor(index_view, 5125, len(indices), "SCALAR", (min(indices), max(indices)))
            )
            primitives.append(
                {
                    "attributes": {"POSITION": position_accessor, "NORMAL": normal_accessor},
                    "indices": index_accessor,
                    "material": material_index,
                }
            )
        node_record = dict(node_record)
        if primitives:
            mesh_index = len(meshes)
            meshes.append({"name": node_record["name"], "primitives": primitives})
            node_record["mesh"] = mesh_index
        nodes.append(node_record)

    assembly_node_indexes = list(range(len(nodes)))
    root_index = len(nodes)
    nodes.append(
        {
            "name": "cadkit.z_up_to_gltf_y_up",
            "rotation": [-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)],
            "children": assembly_node_indexes,
            "extras": {"coordinate_conversion": "Z-up to glTF Y-up"},
        }
    )
    document: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "hf-il3-assembly-preview"},
        "scene": 0,
        "scenes": [{"name": "assembly", "nodes": [root_index]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": [_material_json(material) for material in materials],
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(binary.data)}],
        "extras": {
            "schema": "cadkit.assembly",
            "schema_version": scene.version,
            "units": scene.units,
            "offline_proxy_assets": True,
        },
    }
    json_chunk = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    bin_chunk = bytes(binary.data) + b"\0" * ((4 - len(binary.data) % 4) % 4)
    total_length = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    payload = (
        struct.pack("<4sII", b"glTF", 2, total_length)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(bin_chunk), 0x004E4942)
        + bin_chunk
    )
    if len(payload) > MAX_GLB_BYTES:
        raise ValueError(f"GLB exceeds the {MAX_GLB_BYTES} byte limit")
    return payload


def _vertex_normals(mesh: _MeshData) -> tuple[Point3, ...]:
    sums = [[0.0, 0.0, 0.0] for _ in mesh.vertices]
    for first, second, third in mesh.triangles:
        a, b, c = mesh.vertices[first], mesh.vertices[second], mesh.vertices[third]
        normal = _cross(
            _point3(tuple(b[index] - a[index] for index in range(3))),
            _point3(tuple(c[index] - a[index] for index in range(3))),
        )
        for vertex in (first, second, third):
            for axis in range(3):
                sums[vertex][axis] += normal[axis]
    return tuple(
        _normalize(_point3(tuple(value)) if any(value) else (0.0, 0.0, 1.0)) for value in sums
    )


def _bounds(points: Sequence[Point3]) -> tuple[list[float], list[float]]:
    return (
        [min(point[index] for point in points) for index in range(3)],
        [max(point[index] for point in points) for index in range(3)],
    )


def _accessor(
    view: int, component_type: int, count: int, kind: str, bounds: AccessorBounds
) -> dict[str, Any]:
    return {
        "bufferView": view,
        "componentType": component_type,
        "count": count,
        "type": kind,
        "min": list(bounds[0]) if isinstance(bounds[0], (list, tuple)) else [bounds[0]],
        "max": list(bounds[1]) if isinstance(bounds[1], (list, tuple)) else [bounds[1]],
    }


def _material_json(material: _Material) -> dict[str, Any]:
    return {
        "name": material.name,
        "pbrMetallicRoughness": {
            "baseColorFactor": list(material.color),
            "metallicFactor": material.metallic,
            "roughnessFactor": material.roughness,
        },
        "alphaMode": material.alpha_mode,
        "doubleSided": material.alpha_mode == "BLEND",
    }


__all__ = [
    "MAX_GLB_BYTES",
    "MAX_ROUTE_SEGMENTS",
    "MAX_TOTAL_TRIANGLES",
    "MAX_TOTAL_VERTICES",
    "assemble_glb",
    "build_assembled_glb",
    "export_assembly_glb",
    "write_assembled_glb",
]
