"""Regression contracts for the canonical 65 mm editor case envelope."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Any, cast

_SESSION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SESSION / "tools"))

from cadkit.assembly import (  # noqa: E402
    Dimensions,
    build_demo_assembly,
    placements_from_assembly,
)
from cadkit.component_proxies import (  # noqa: E402
    build_scene_proxies,  # pyright: ignore[reportUnknownVariableType]
)
from cadkit.constraints import validate  # noqa: E402
from cadkit.parametric import gamepad_body  # noqa: E402
from editor.gltf_scene import build_assembled_glb  # noqa: E402

from tests._pytest_helpers import approx  # noqa: E402


def _glb_document(glb: bytes) -> dict[str, Any]:
    """Read the JSON document from an embedded-buffer GLB artifact."""
    assert glb[:4] == b"glTF"
    json_length, json_type = struct.unpack_from("<II", glb, 12)
    assert json_type == 0x4E4F534A
    return cast(dict[str, Any], json.loads(glb[20 : 20 + json_length].decode("utf-8")))


def _node_mesh_bounds(
    document: dict[str, Any], node_name: str
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return the node's rendered bounds in the scene's millimetre frame."""
    node = next(node for node in document["nodes"] if node["name"] == node_name)
    translation = node.get("translation", [0.0, 0.0, 0.0])
    mesh = document["meshes"][node["mesh"]]
    bounds = [
        document["accessors"][primitive["attributes"]["POSITION"]]
        for primitive in mesh["primitives"]
    ]
    minimum = tuple(
        min(float(accessor["min"][axis]) for accessor in bounds) + float(translation[axis])
        for axis in range(3)
    )
    maximum = tuple(
        max(float(accessor["max"][axis]) for accessor in bounds) + float(translation[axis])
        for axis in range(3)
    )
    return cast(tuple[float, float, float], minimum), cast(tuple[float, float, float], maximum)


def _node_z_bounds(node: Any) -> tuple[float, float]:
    centre_z = node.transform.position[2]
    half_height = node.physical_dimensions.z / 2.0
    return centre_z - half_height, centre_z + half_height


def _part_z_bounds(part: Any) -> tuple[float, float]:
    bounds = part.bounding_box()
    return float(bounds.min.Z), float(bounds.max.Z)


def test_canonical_editor_scene_uses_65mm_shell_with_17_75mm_lower_cap() -> None:
    scene = build_demo_assembly()

    assert scene.case_dimensions == Dimensions(130.0, 90.0, 65.0)
    bottom = scene.node("case.bottom")
    top = scene.node("case.top")
    assert bottom.physical_dimensions == Dimensions(130.0, 90.0, 17.75)
    assert top.physical_dimensions == Dimensions(130.0, 90.0, 47.25)
    assert _node_z_bounds(bottom) == approx((0.0, 17.75))
    assert _node_z_bounds(top) == approx((17.75, 65.0))


def test_canonical_electronics_stack_and_clear_z_corridor_are_exact() -> None:
    scene = build_demo_assembly()
    breadboard = scene.node("electronics.breadboard")
    assert _node_z_bounds(breadboard) == approx((0.0, 20.0))

    placements = placements_from_assembly(scene)
    clear_z = next(
        volume for volume in placements.volumes if volume.name == "electronics.internal_clear_z"
    )
    assert (clear_z.aabb.min_z, clear_z.aabb.max_z) == (24.0, 61.0)
    assert clear_z.aabb.max_z - clear_z.aabb.min_z == 37.0

    violations = validate(
        gamepad_body(length_mm=130.0, width_mm=90.0, thickness_mm=65.0),
        placements=placements,
        require_export_verification=True,
    )
    clear_violations = [
        violation for violation in violations if violation.rule.startswith("internal_clear_z")
    ]
    assert {violation.rule for violation in clear_violations} == {"internal_clear_z_insufficient"}
    assert clear_violations[0].severity == "error"


def test_proxy_glb_renders_the_canonical_shell_at_65mm_high() -> None:
    scene = build_demo_assembly()
    document = _glb_document(build_assembled_glb(scene))

    minimum, maximum = _node_mesh_bounds(document, "case.shell")
    assert minimum == approx((0.0, 0.0, 0.0))
    assert tuple(maximum[axis] - minimum[axis] for axis in range(3)) == approx((130.0, 90.0, 65.0))


def test_control_proxies_reach_z67_in_the_taller_shell() -> None:
    scene = build_demo_assembly()
    proxies: dict[str, Any] = cast(dict[str, Any], build_scene_proxies(scene))

    for control_id in ("up", "down", "right", "left", "action_a", "action_b"):
        button = proxies[f"control.{control_id}"]
        assert _part_z_bounds(button) == approx((47.0, 67.0))


def test_control_proxies_do_not_intersect_installed_electronics_solids() -> None:
    scene = build_demo_assembly()
    proxies: dict[str, Any] = cast(dict[str, Any], build_scene_proxies(scene))
    assert scene.node("electronics.breadboard").metadata["measurement_scope"] == "installed_stack"
    breadboard = proxies["electronics.breadboard"]
    feather = proxies["feather"]

    for control_id in ("up", "down", "right", "left", "action_a", "action_b"):
        button = proxies[f"control.{control_id}"]
        assert not (button & breadboard).solids()
        assert not (button & feather).solids()
