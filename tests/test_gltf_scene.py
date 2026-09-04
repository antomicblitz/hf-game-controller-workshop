"""Behavioral tests for the offline assembled GLB exporter."""

from __future__ import annotations

import json
import math
import struct
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

from build123d import Align, Box  # noqa: E402
from cadkit.assembly import build_demo_assembly  # noqa: E402
from editor import gltf_scene  # noqa: E402
from editor.gltf_scene import build_assembled_glb  # noqa: E402

from tests._pytest_helpers import approx  # noqa: E402


def _document(glb: bytes) -> dict[str, Any]:
    assert glb[:4] == b"glTF"
    _magic, version, length = struct.unpack_from("<4sII", glb)
    assert version == 2
    assert length == len(glb)
    json_length, json_type = struct.unpack_from("<II", glb, 12)
    assert json_type == 0x4E4F534A
    binary_length, binary_type = struct.unpack_from("<II", glb, 20 + json_length)
    assert binary_type == 0x004E4942
    assert binary_length > 0
    return cast(dict[str, Any], json.loads(glb[20 : 20 + json_length].decode("utf-8")))


def test_proxy_assembly_contains_named_components_routes_and_embedded_mesh_data():
    scene = build_demo_assembly()
    glb = build_assembled_glb(scene)
    document = _document(glb)
    names = {node["name"] for node in document["nodes"]}

    expected = {
        "case.shell",
        "case.top",
        "case.bottom",
        "electronics.breadboard",
        "feather",
        "feather.header_12",
        "feather.header_16",
        "usb.connector",
        "harness.header_12.connector",
        "harness.header_12.strain_relief",
        "harness.header_12.no_pinch",
        "harness.header_16.connector",
        "harness.header_16.strain_relief",
        "harness.header_16.no_pinch",
        *(
            f"control.{control_id}"
            for control_id in ("up", "down", "right", "left", "action_a", "action_b")
        ),
        *(route.id for route in scene.routes),
    }
    assert len(scene.nodes) == 21
    assert len(scene.routes) == 15
    assert expected <= names
    assert document["buffers"][0].get("uri") is None
    assert document["buffers"][0]["byteLength"] > 0
    assert all(
        "POSITION" in primitive["attributes"]
        and "NORMAL" in primitive["attributes"]
        and "indices" in primitive
        for mesh in document["meshes"]
        for primitive in mesh["primitives"]
    )
    assert all(
        all(math.isfinite(float(value)) for value in accessor[bound])
        for accessor in document["accessors"]
        for bound in ("min", "max")
    )
    triangle_count = sum(
        document["accessors"][primitive["indices"]]["count"] // 3
        for mesh in document["meshes"]
        for primitive in mesh["primitives"]
    )
    assert 0 < triangle_count < 250_000
    assert len(glb) < 8 * 1024 * 1024
    assert any(material["alphaMode"] == "BLEND" for material in document["materials"])
    assert {
        material["name"]
        for material in document["materials"]
        if material["name"].startswith("wire-")
    } >= {"wire-UP", "wire-GND"}
    assert {"breadboard-preview", "pbs33b-directional", "guuzi-action"} <= {
        material["name"] for material in document["materials"]
    }
    by_name = {node["name"]: node for node in document["nodes"]}
    breadboard_primitive = document["meshes"][by_name["electronics.breadboard"]["mesh"]][
        "primitives"
    ][0]
    breadboard_material = document["materials"][breadboard_primitive["material"]]
    assert breadboard_material["alphaMode"] == "BLEND"
    assert breadboard_material["pbrMetallicRoughness"]["baseColorFactor"][3] == approx(0.18)
    assert document["accessors"][breadboard_primitive["indices"]]["count"] == 36
    assert by_name["wire.control_up"]["extras"]["target"] == ("electronics.breadboard.signal_up")
    assert by_name["wire.control_ground_up"]["extras"]["target"] == (
        "electronics.breadboard.gnd_up"
    )
    assert by_name["wire.feather_ground_rail"]["extras"] == {
        "route_id": "wire.feather_ground_rail",
        "signal": "GND",
        "source": "feather.gnd",
        "target": "electronics.breadboard.gnd_bridge",
    }


def test_scene_uses_conversion_root_without_rebasing_named_nodes():
    scene = build_demo_assembly()
    document = _document(build_assembled_glb(scene))
    by_name = {node["name"]: node for node in document["nodes"]}
    scene_record = document["scenes"][document["scene"]]
    scene_node_indexes = scene_record["nodes"]
    assert len(scene_node_indexes) == 1
    root_index = scene_node_indexes[0]
    root = document["nodes"][root_index]

    assert root["name"] == "cadkit.z_up_to_gltf_y_up"
    assert scene_node_indexes == [root_index]
    expected_rotation = (-math.sin(math.pi / 4), 0.0, 0.0, math.cos(math.pi / 4))
    actual_rotation = root["rotation"]
    assert len(actual_rotation) == len(expected_rotation)
    assert all(
        math.isclose(float(actual), expected, rel_tol=0.0, abs_tol=1e-12)
        for actual, expected in zip(actual_rotation, expected_rotation, strict=True)
    )

    expected_child_names = {node.id for node in scene.nodes} | {route.id for route in scene.routes}
    child_names = [document["nodes"][index]["name"] for index in root["children"]]
    assert set(child_names) == expected_child_names
    assert len(child_names) == len(expected_child_names)

    for source in scene.nodes:
        assert by_name[source.id]["translation"] == list(source.transform.position)
        assert by_name[source.id]["extras"]["transform"] == source.transform.to_dict()


def test_route_mesh_uses_live_breadboard_contact_position() -> None:
    scene = build_demo_assembly()
    breadboard = scene.node("electronics.breadboard")
    moved_breadboard = replace(
        breadboard,
        transform=replace(
            breadboard.transform,
            position=(
                breadboard.transform.position[0] + 10.0,
                breadboard.transform.position[1],
                breadboard.transform.position[2],
            ),
        ),
    )
    moved_scene = replace(
        scene,
        nodes=tuple(moved_breadboard if node.id == breadboard.id else node for node in scene.nodes),
    )
    route = next(route for route in moved_scene.routes if route.id == "wire.control_up")
    live_target = moved_scene.port_position(route.target)
    assert live_target is not None
    assert live_target[0] > max(point[0] for point in route.waypoints) + 5.0

    document = _document(build_assembled_glb(moved_scene))
    route_node = next(node for node in document["nodes"] if node["name"] == route.id)
    primitive = document["meshes"][route_node["mesh"]]["primitives"][0]
    position = document["accessors"][primitive["attributes"]["POSITION"]]

    assert all(
        position["min"][axis] <= live_target[axis] <= position["max"][axis] for axis in range(3)
    )


def test_exact_case_part_uses_the_canonical_case_injection_seam():
    scene = build_demo_assembly()
    case = Box(
        scene.case_dimensions.x,
        scene.case_dimensions.y,
        scene.case_dimensions.z,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    document = _document(build_assembled_glb(scene, case_part=case))
    shell = next(node for node in document["nodes"] if node["name"] == "case.shell")
    action = next(node for node in document["nodes"] if node["name"] == "control.action_a")
    assert shell["extras"]["proxy_ref"] == scene.node("case.shell").proxy_ref
    assert action["mesh"] != shell["mesh"]
    assert document["accessors"][0]["min"] == [-65.0, -45.0, -32.5]
    assert document["accessors"][0]["max"] == [65.0, 45.0, 32.5]


def test_external_mesh_injection_is_not_part_of_the_glb_api():
    scene = build_demo_assembly()
    with pytest.raises(TypeError):
        build_assembled_glb(scene, case_parts={"case.shell": {"vertices": []}})
    with pytest.raises(ValueError):
        build_assembled_glb(scene, case_parts={"control.action_a": object()})


def test_glb_rejects_excessive_route_segments_before_tube_generation() -> None:
    scene = build_demo_assembly()
    source = scene.routes[0]
    waypoints = tuple((float(index), 20.0, 3.0) for index in range(128))
    routes = tuple(replace(source, id=f"route-{index}", waypoints=waypoints) for index in range(40))
    oversized = replace(scene, routes=routes)

    with pytest.raises(ValueError, match="route segment"):
        build_assembled_glb(oversized)


def test_glb_rejects_excessive_tessellated_mesh_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = build_demo_assembly()
    vertices = tuple((float(index), 0.0, 0.0) for index in range(gltf_scene.MAX_TOTAL_VERTICES + 1))
    oversized = gltf_scene._MeshData(vertices, ((0, 1, 2),))  # pyright: ignore[reportPrivateUsage]

    def oversized_tessellate(*_args: Any, **_kwargs: Any) -> Any:
        return oversized

    monkeypatch.setattr(gltf_scene, "_tessellate_part", oversized_tessellate)

    with pytest.raises(ValueError, match="vertex limit"):
        build_assembled_glb(scene, case_part=Box(10, 10, 10))


def test_glb_rejects_excessive_tessellated_triangle_count(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = build_demo_assembly()
    triangles = tuple((0, 1, 2) for _ in range(gltf_scene.MAX_TOTAL_TRIANGLES + 1))
    oversized = gltf_scene._MeshData(  # pyright: ignore[reportPrivateUsage]
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)), triangles
    )

    def oversized_tessellate(*_args: Any, **_kwargs: Any) -> Any:
        return oversized

    monkeypatch.setattr(gltf_scene, "_tessellate_part", oversized_tessellate)

    with pytest.raises(ValueError, match="triangle limit"):
        build_assembled_glb(scene, case_part=Box(10, 10, 10))


def test_glb_rejects_final_encoded_output_backstop(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = build_demo_assembly()

    def oversized_encode(*_args: Any, **_kwargs: Any) -> bytes:
        return b"x" * (gltf_scene.MAX_GLB_BYTES + 1)

    monkeypatch.setattr(gltf_scene, "_encode_glb", oversized_encode)

    with pytest.raises(ValueError, match="GLB exceeds"):
        build_assembled_glb(scene)
