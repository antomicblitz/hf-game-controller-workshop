"""End-to-end contracts for integrated editor scene correctness."""

from __future__ import annotations

import json
import struct
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

from cadkit.assembly import Dimensions, build_demo_assembly  # noqa: E402
from cadkit.manifest import (  # noqa: E402
    _manifest_from_assembly,  # pyright: ignore[reportPrivateUsage]
)
from editor.gltf_scene import build_assembled_glb  # noqa: E402

from tools.editor.server import (  # noqa: E402
    _render_case_glb,  # pyright: ignore[reportPrivateUsage]
    validate_scene_move,
)


def _document(glb: bytes) -> dict[str, Any]:
    json_length, json_type = struct.unpack_from("<II", glb, 12)
    assert json_type == 0x4E4F534A
    return cast(dict[str, Any], json.loads(glb[20 : 20 + json_length].decode("utf-8")))


def test_scene_parameters_drive_manifest_nodes_routes_and_glb_transform():
    buttons = [
        {"id": "up", "x": 92.5, "y": 28.0, "size_mm": 10.0, "type": "pbs33b"},
        {"id": "down", "x": 113.0, "y": 28.0, "size_mm": 12.0, "type": "pbs33b"},
        {"id": "right", "x": 113.0, "y": 50.0, "size_mm": 12.0, "type": "pbs33b"},
        {"id": "left", "x": 92.5, "y": 50.0, "size_mm": 12.0, "type": "pbs33b"},
        {"id": "action_a", "x": 120.0, "y": 70.0, "size_mm": 12.0, "type": "guuzi"},
        {"id": "action_b", "x": 70.0, "y": 70.0, "size_mm": 12.0, "type": "guuzi"},
    ]
    scene = build_demo_assembly((150.0, 90.0, 38.0), buttons=buttons)
    manifest = _manifest_from_assembly(scene.to_dict())
    document = _document(build_assembled_glb(scene))
    glb_nodes = {node["name"]: node for node in document["nodes"]}

    assert scene.case_dimensions == Dimensions(150.0, 90.0, 38.0)
    assert scene.node("control.up").transform.position[:2] == (92.5, 28.0)
    assert [
        scene.node(f"control.{control_id}").visual["diameter_mm"]
        for control_id in (
            "up",
            "down",
            "right",
            "left",
            "action_a",
            "action_b",
        )
    ] == [12.4] * 6
    manifest_button = next(
        node for node in manifest["assembly"]["nodes"] if node["id"] == "control.up"
    )
    assert manifest_button["transform"]["position_mm"][:2] == [92.5, 28.0]
    assert manifest_button["visual"]["diameter_mm"] == 12.4
    assert glb_nodes["control.up"]["translation"][:2] == [92.5, 28.0]
    signal = next(route for route in scene.routes if route.id == "wire.control_up")
    ground = next(route for route in scene.routes if route.id == "wire.control_ground_up")
    assert scene.route_waypoints(signal)[0][:2] == (92.5, 28.0)
    assert scene.route_waypoints(ground)[0][:2] == (92.5, 28.0)
    assert signal.target == "electronics.breadboard.signal_up"
    assert ground.target == "electronics.breadboard.gnd_up"


def test_canonical_scene_parameters_drive_assembled_glb_without_inline_execution():
    scene = build_demo_assembly()
    from build123d import Align, Box

    case = Box(
        scene.case_dimensions.x,
        scene.case_dimensions.y,
        scene.case_dimensions.z,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    document = _document(_render_case_glb(case, scene))
    glb_nodes = {node["name"]: node for node in document["nodes"]}

    assert scene.node("control.up").transform.position[:2] == (35.0, 60.0)
    assert scene.node("control.down").transform.position[:2] == (35.0, 20.0)
    assert glb_nodes["control.up"]["translation"][:2] == [35.0, 60.0]
    assert glb_nodes["control.down"]["translation"][:2] == [35.0, 20.0]


def test_move_routes_follow_live_button_and_reject_does_not_mutate_scene():
    scene = build_demo_assembly()
    button = scene.node("control.down")
    moved_position = (35.0, 21.0)
    moved = replace(
        button,
        transform=replace(
            button.transform, position=(*moved_position, button.transform.position[2])
        ),
    )
    moved_scene = replace(
        scene, nodes=tuple(moved if node.id == button.id else node for node in scene.nodes)
    )
    signal = next(route for route in scene.routes if route.id == "wire.control_down")
    ground = next(route for route in scene.routes if route.id == "wire.control_ground_down")

    assert validate_scene_move(scene, "control.down", list(moved_position))["valid"] is True
    assert moved_scene.route_waypoints(signal)[0][:2] == moved_position
    assert moved_scene.route_waypoints(ground)[0][:2] == moved_position
    rejected = validate_scene_move(scene, "control.down", [55.0, 40.0])
    assert rejected["valid"] is False
    assert rejected["violations"][0]["rule"] == "control_control_spacing"
    assert scene.route_waypoints(signal)[0][:2] == (35.0, 20.0)


def test_finished_glb_has_meshless_voids_and_real_control_geometry():
    scene = build_demo_assembly()
    document = _document(build_assembled_glb(scene))
    nodes = {node["name"]: node for node in document["nodes"]}
    materials = {material["name"] for material in document["materials"]}

    assert "mesh" in nodes["case.shell"]
    assert all("mesh" not in nodes[name] for name in ("case.bottom", "case.top", "usb.opening"))
    assert "keepout-xray" not in materials
    assert {"pbs33b-directional", "guuzi-action", "feather-pcb"} <= materials
    assert "mesh" in nodes["control.action_a"]
    assert len(document["meshes"][nodes["control.up"]["mesh"]]["primitives"]) == 3
    assert (
        scene.node("feather").transform.position[2]
        + scene.node("feather").physical_dimensions.z / 2
        < scene.case_dimensions.z
    )
    assert scene.node("control.action_a").metadata["component_id"] == "guuzi_b09bmrdptn_action"
    assert scene.node("control.action_b").metadata["component_id"] == "guuzi_b09bmrdptn_action"
