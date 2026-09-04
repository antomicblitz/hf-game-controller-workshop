"""Canonical editor move-validation contract tests."""

from __future__ import annotations

import ast
import base64
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

from tests._cad_helpers import exact_six_case_part  # noqa: E402


@pytest.fixture
def client(monkeypatch: MonkeyPatch):
    from cadkit.assembly import build_demo_assembly, placements_from_assembly
    from cadkit.case_execution import CaseArtifacts

    from tools.editor import server
    from tools.editor.server import create_app

    scene = build_demo_assembly()
    artifacts = CaseArtifacts(
        part=exact_six_case_part(scene),
        assembly=scene.to_dict(),
        placements=placements_from_assembly(scene),
    )

    def case_artifacts(path: Path) -> CaseArtifacts:
        if path.parent.name.startswith("hf-editor-move-preview-"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            controls = next(
                statement.value
                for statement in tree.body
                if (
                    isinstance(statement, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "CONTROLS"
                        for target in statement.targets
                    )
                )
                or (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id == "CONTROLS"
                )
            )
            assert controls is not None
            moved_scene = build_demo_assembly(buttons=ast.literal_eval(controls))
            return CaseArtifacts(
                part=exact_six_case_part(moved_scene),
                assembly=moved_scene.to_dict(),
                placements=placements_from_assembly(moved_scene),
            )
        return artifacts

    monkeypatch.setattr(server, "_case_artifacts", case_artifacts)

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def _move(client: FlaskClient, element_id: str, position: list[float]) -> TestResponse:
    from cadkit.assembly import build_demo_assembly

    scene = build_demo_assembly()
    expected = (
        list(scene.node(element_id).transform.position[:2])
        if element_id in {node.id for node in scene.nodes}
        else list(scene.node("control.up").transform.position[:2])
    )
    return client.post(
        "/validate-move",
        json={
            "case_path": "examples/6-button-gamepad/case.py",
            "moves": [
                {
                    "element_id": element_id,
                    "new_position": position,
                    "expected_position": expected,
                }
            ],
        },
    )


def test_scene_move_application_is_sequential_and_pure():
    from cadkit.assembly import build_demo_assembly

    from tools.editor.server import SceneMoveError, apply_scene_moves

    scene = build_demo_assembly(
        buttons=[
            {"id": "up", "x": 50.0, "y": 10.0},
            {"id": "down", "x": 90.0, "y": 10.0},
            {"id": "right", "x": 110.0, "y": 40.0},
            {"id": "left", "x": 90.0, "y": 40.0},
            {"id": "action_a", "x": 110.0, "y": 70.0},
            {"id": "action_b", "x": 30.0, "y": 70.0},
        ]
    )
    original = scene.to_json()

    with pytest.raises(SceneMoveError, match="must be at least 20 mm apart"):
        apply_scene_moves(
            scene,
            [
                {
                    "element_id": "control.up",
                    "expected_position": [50.0, 10.0],
                    "new_position": [61.0, 10.0],
                },
                {
                    "element_id": "control.down",
                    "expected_position": [90.0, 10.0],
                    "new_position": [79.0, 10.0],
                },
            ],
        )
    final, accepted = apply_scene_moves(
        scene,
        [
            {
                "element_id": "control.up",
                "expected_position": [50.0, 10.0],
                "new_position": [61.0, 10.0],
            },
        ],
    )

    assert accepted == [{"element_id": "control.up", "new_position": [61.0, 10.0]}]
    assert final.node("control.up").transform.position[:2] == (61.0, 10.0)
    assert scene.to_json() == original


@pytest.mark.parametrize(
    "move",
    [
        {
            "element_id": "case.shell",
            "expected_position": [65.0, 45.0],
            "new_position": [35.0, 20.0],
        },
        {
            "element_id": "not-a-node",
            "expected_position": [35.0, 20.0],
            "new_position": [35.0, 20.0],
        },
        {
            "element_id": "control.up",
            "expected_position": [35.0, 20.0],
            "new_position": [float("nan"), 20.0],
        },
    ],
)
def test_scene_move_application_rejects_fixed_unknown_and_malformed_moves(
    move: dict[str, object],
) -> None:
    from cadkit.assembly import build_demo_assembly

    from tools.editor.server import SceneMoveError, apply_scene_moves

    with pytest.raises(SceneMoveError):
        apply_scene_moves(build_demo_assembly(), [move])


def test_scene_move_application_rejects_malformed_batches_and_stale_positions() -> None:
    from cadkit.assembly import build_demo_assembly

    from tools.editor.server import SceneMoveError, apply_scene_moves

    scene = build_demo_assembly()
    with pytest.raises(SceneMoveError) as malformed:
        apply_scene_moves(scene, {"element_id": "control.up"})
    assert malformed.value.index == 0
    assert malformed.value.result["violations"][0]["rule"] == "malformed_move"

    with pytest.raises(SceneMoveError) as stale:
        apply_scene_moves(
            scene,
            [
                {
                    "element_id": "control.up",
                    "expected_position": [0.0, 0.0],
                    "new_position": [34.9, 20.0],
                }
            ],
        )
    assert stale.value.result["violations"][0]["rule"] == "stale_move"


@pytest.mark.parametrize("expected", [None, [float("nan"), 20.0], [35.0, float("inf")]])
def test_scene_move_application_requires_finite_expected_position(expected: object) -> None:
    from cadkit.assembly import build_demo_assembly

    from tools.editor.server import SceneMoveError, apply_scene_moves

    move: dict[str, object] = {
        "element_id": "control.up",
        "new_position": [34.9, 20.0],
    }
    if expected is not None:
        move["expected_position"] = expected
    with pytest.raises(SceneMoveError) as failure:
        apply_scene_moves(build_demo_assembly(), [move])
    assert failure.value.result["violations"][0]["rule"] == "stale_move"


@pytest.mark.parametrize(
    ("node_id", "kind", "mobility"),
    [
        ("feather", "feather_board", "constrained_xy"),
        ("feather", "pbs33b_button", "constrained_xy"),
        ("control.action_a", "feather_board", "constrained_xy"),
    ],
)
def test_scene_move_application_rejects_forged_movable_identity(
    node_id: str, kind: str, mobility: str
) -> None:
    from cadkit.assembly import AssemblyScene, build_demo_assembly

    from tools.editor.server import SceneMoveError, apply_scene_moves

    payload = build_demo_assembly().to_dict()
    forged = next(node for node in payload["nodes"] if node["id"] == node_id)
    forged["kind"] = kind
    forged["mobility"] = mobility
    scene = AssemblyScene.from_dict(payload)
    with pytest.raises(SceneMoveError) as failure:
        apply_scene_moves(
            scene,
            [
                {
                    "element_id": node_id,
                    "expected_position": list(scene.node(node_id).transform.position[:2]),
                    "new_position": list(scene.node(node_id).transform.position[:2]),
                }
            ],
        )
    assert failure.value.result["violations"][0]["rule"] == "mobility"


def test_canonical_validation_rejects_extra_movable_node() -> None:
    from cadkit.assembly import (
        AssemblyNode,
        AssemblyScene,
        Dimensions,
        Transform,
        build_demo_assembly,
    )

    from tools.editor.server import _canonical_scene_error  # pyright: ignore[reportPrivateUsage]

    scene = build_demo_assembly()
    extra = AssemblyNode(
        id="rogue",
        kind="pbs33b_button",
        transform=Transform((20.0, 20.0, 10.0)),
        physical_dimensions=Dimensions(14.0, 14.0, 20.0),
        keep_out_dimensions=None,
        parent_id="case.top",
        mobility="constrained_xy",
        provenance="test",
        status="provisional",
        layer=3,
        z_order=99,
        visual_ref="",
        proxy_ref="",
    )
    assert "unauthorized movable" in (
        _canonical_scene_error(
            AssemblyScene(scene.case_dimensions, (*scene.nodes, extra), scene.routes)
        )
        or ""
    )


@pytest.mark.parametrize("route_id", ["wire.control_up", "wire.feather_ground_rail"])
def test_canonical_validation_rejects_same_signal_route_with_wrong_endpoints(
    route_id: str,
) -> None:
    from cadkit.assembly import AssemblyScene, build_demo_assembly

    from tools.editor.server import _canonical_scene_error  # pyright: ignore[reportPrivateUsage]

    payload = build_demo_assembly().to_dict()
    route = next(item for item in payload["routes"] if item["id"] == route_id)
    route["source"], route["target"] = route["target"], route["source"]
    scene = AssemblyScene.from_dict(payload)
    assert "unexpected endpoints" in (_canonical_scene_error(scene) or "")


def test_canonical_validation_rejects_missing_feather_ground_bridge() -> None:
    from cadkit.assembly import AssemblyScene, build_demo_assembly

    from tools.editor.server import _canonical_scene_error  # pyright: ignore[reportPrivateUsage]

    payload = build_demo_assembly().to_dict()
    payload["routes"] = [
        route for route in payload["routes"] if route["id"] != "wire.feather_ground_rail"
    ]
    scene = AssemblyScene.from_dict(payload)
    assert "missing required routes: wire.feather_ground_rail" in (
        _canonical_scene_error(scene) or ""
    )


def test_validate_move_endpoint_rejects_legacy_single_element_payload_without_revision(
    client: FlaskClient,
) -> None:
    response = client.post(
        "/validate-move",
        json={
            "case_path": "examples/6-button-gamepad/case.py",
            "element_id": "control.up",
            "new_position": [34.9, 20.0],
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["violations"][0]["rule"] == "malformed_move"


def test_feedback_moves_requires_a_canonical_scene_for_move_requests() -> None:
    from tools.editor import server

    with server.create_app().app_context():
        feedback_scene, accepted, error = server._feedback_moves(  # pyright: ignore[reportPrivateUsage]
            None,
            {
                "moves": [
                    {
                        "element_id": "control.up",
                        "expected_position": [35.0, 20.0],
                        "new_position": [35.0, 20.0],
                    }
                ]
            },
        )

    assert feedback_scene is None
    assert accepted == []
    assert error is not None
    response, status = error
    assert status == 409
    assert response.get_json()["error"] == "submit moves require a canonical ASSEMBLY_SPEC scene"


def test_validate_move_accepts_constrained_button_and_preserves_tenth_mm(
    client: FlaskClient,
) -> None:
    response = _move(client, "control.up", [34.9, 60.0])

    assert response.status_code == 200
    body = response.get_json()
    assert body["valid"] is True
    assert body["new_position"] == [34.9, 60.0]


def test_moved_case_source_rewrites_only_accepted_control_coordinates(
    tmp_path: Path,
) -> None:
    from cadkit.assembly import build_demo_assembly

    from tools.editor.server import _moved_case_source  # pyright: ignore[reportPrivateUsage]

    case_path = tmp_path / "case.py"
    original = """CONTROLS = [
    {"id": "up", "role": "UP", "component": "pbs33b_directional", "x": 35.0, "y": 60.0, "size_mm": 12.4, "type": "pbs33b"},
    {"id": "down", "role": "DOWN", "component": "pbs33b_directional", "x": 35.0, "y": 20.0, "size_mm": 12.4, "type": "pbs33b"},
    {"id": "right", "role": "RIGHT", "component": "pbs33b_directional", "x": 55.0, "y": 40.0, "size_mm": 12.4, "type": "pbs33b"},
    {"id": "left", "role": "LEFT", "component": "pbs33b_directional", "x": 15.0, "y": 40.0, "size_mm": 12.4, "type": "pbs33b"},
    {"id": "action_a", "role": "ACTION_A", "component": "guuzi_b09bmrdptn_action", "x": 105.0, "y": 30.0, "size_mm": 12.4, "type": "guuzi"},
    {"id": "action_b", "role": "ACTION_B", "component": "guuzi_b09bmrdptn_action", "x": 105.0, "y": 55.0, "size_mm": 12.4, "type": "guuzi"},
]
MARKER = {"x": 7.0, "y": 8.0}
"""
    case_path.write_text(original, encoding="utf-8")
    scene = build_demo_assembly(
        buttons=[
            {"id": "up", "x": 34.9, "y": 61.25, "size_mm": 12.4},
            {"id": "down", "x": 35.0, "y": 20.0, "size_mm": 12.4},
            {"id": "right", "x": 55.0, "y": 40.0, "size_mm": 12.4},
            {"id": "left", "x": 15.0, "y": 40.0, "size_mm": 12.4},
            {"id": "action_a", "x": 105.0, "y": 30.0, "size_mm": 12.4},
            {"id": "action_b", "x": 105.0, "y": 55.0, "size_mm": 12.4},
        ]
    )

    moved = _moved_case_source(
        case_path,
        scene,
        [{"element_id": "control.up", "new_position": [34.9, 61.25]}],
    )
    moved_tree = ast.parse(moved)
    controls_statement = moved_tree.body[0]
    marker_statement = moved_tree.body[1]
    assert isinstance(controls_statement, ast.Assign)
    assert isinstance(marker_statement, ast.Assign)
    controls = ast.literal_eval(controls_statement.value)

    assert case_path.read_text(encoding="utf-8") == original
    assert controls[0] == {
        "id": "up",
        "role": "UP",
        "component": "pbs33b_directional",
        "x": 34.9,
        "y": 61.25,
        "size_mm": 12.4,
        "type": "pbs33b",
    }
    assert controls[1]["x"] == 35.0
    assert controls[1]["role"] == "DOWN"
    assert controls[1]["component"] == "pbs33b_directional"
    assert len(controls) == 6
    assert ast.literal_eval(marker_statement.value) == {"x": 7.0, "y": 8.0}


@pytest.mark.parametrize(
    ("controls", "message"),
    [
        ("make_controls()", "literal list"),
        ('[{"id": "up", "x": 35.0}]', "contain literal id, x, and y"),
        ('[{"id": CONTROL_ID, "x": 35.0, "y": 20.0}]', "literal control IDs"),
        ('[{"id": "down", "x": 35.0, "y": 60.0}]', "missing moved controls"),
    ],
)
def test_moved_case_source_fails_closed_for_invalid_or_missing_controls(
    tmp_path: Path, controls: str, message: str
) -> None:
    from cadkit.assembly import build_demo_assembly

    from tools.editor.server import _moved_case_source  # pyright: ignore[reportPrivateUsage]

    case_path = tmp_path / "case.py"
    case_path.write_text(f"CONTROLS = {controls}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _moved_case_source(
            case_path,
            build_demo_assembly(),
            [{"element_id": "control.up", "new_position": [34.9, 20.0]}],
        )


def test_preview_executes_temporary_case_and_returns_its_part(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit.assembly import build_demo_assembly
    from cadkit.case_execution import CaseArtifacts

    from tools.editor import server

    case_path = tmp_path / "case.py"
    case_path.write_text(
        'CONTROLS = [{"id": "up", "x": 35.0, "y": 60.0}, '
        '{"id": "down", "x": 35.0, "y": 20.0}, '
        '{"id": "right", "x": 55.0, "y": 40.0}, '
        '{"id": "left", "x": 15.0, "y": 40.0}, '
        '{"id": "action_a", "x": 105.0, "y": 30.0}, '
        '{"id": "action_b", "x": 105.0, "y": 55.0}]\n',
        encoding="utf-8",
    )
    moved_scene, _ = server.apply_scene_moves(
        build_demo_assembly(),
        [
            {
                "element_id": "control.up",
                "expected_position": [35.0, 60.0],
                "new_position": [34.9, 60.0],
            }
        ],
    )
    from cadkit.assembly import placements_from_assembly

    moved_part = exact_six_case_part(moved_scene)
    preview_paths: list[Path] = []

    def execute_preview(preview_path: Path) -> CaseArtifacts:
        preview_paths.append(preview_path)
        assert preview_path.name == "case.py"
        assert preview_path != case_path
        assert "34.9" in preview_path.read_text(encoding="utf-8")
        return CaseArtifacts(
            part=moved_part,
            assembly=moved_scene.to_dict(),
            placements=placements_from_assembly(moved_scene),
        )

    monkeypatch.setattr(server, "_case_artifacts", execute_preview)

    preview_part, preview_scene = server._preview_part_for_moves(  # pyright: ignore[reportPrivateUsage]
        case_path,
        moved_scene,
        [{"element_id": "control.up", "new_position": [34.9, 60.0]}],
    )

    assert preview_part is moved_part
    assert preview_scene.to_dict() == moved_scene.to_dict()
    assert len(preview_paths) == 1


def test_preview_rejects_a_canonical_scene_that_ignored_an_accepted_move(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    from cadkit.assembly import build_demo_assembly
    from cadkit.case_execution import CaseArtifacts

    from tools.editor import server

    case_path = tmp_path / "case.py"
    case_path.write_text(
        'CONTROLS = [{"id": "up", "x": 35.0, "y": 60.0}, '
        '{"id": "down", "x": 35.0, "y": 20.0}, '
        '{"id": "right", "x": 55.0, "y": 40.0}, '
        '{"id": "left", "x": 15.0, "y": 40.0}, '
        '{"id": "action_a", "x": 105.0, "y": 30.0}, '
        '{"id": "action_b", "x": 105.0, "y": 55.0}]\n',
        encoding="utf-8",
    )
    canonical_scene = build_demo_assembly()

    def ignored_preview(_path: Path) -> CaseArtifacts:
        return CaseArtifacts(part=object(), assembly=canonical_scene.to_dict())

    monkeypatch.setattr(server, "_case_artifacts", ignored_preview)
    moved_scene = build_demo_assembly()
    moved_scene, _ = server.apply_scene_moves(  # pyright: ignore[reportPrivateUsage]
        moved_scene,
        [
            {
                "element_id": "control.up",
                "expected_position": [35.0, 60.0],
                "new_position": [34.9, 60.0],
            }
        ],
    )

    with pytest.raises(ValueError, match="ignored accepted move"):
        server._preview_part_for_moves(  # pyright: ignore[reportPrivateUsage]
            case_path,
            moved_scene,
            [{"element_id": "control.up", "new_position": [34.9, 60.0]}],
        )


def test_validate_move_renders_preview_part_for_moves_and_canonical_part_for_empty_batch(
    client: FlaskClient, monkeypatch: MonkeyPatch
) -> None:
    from tools.editor import server

    canonical_part = server._case_artifacts(Path("case.py")).part  # pyright: ignore[reportPrivateUsage]
    moved_part = object()
    rendered_parts: list[Any] = []
    rendered_scenes: list[Any] = []

    def preview(_path: Path, scene: Any, _accepted: list[dict[str, Any]]) -> tuple[Any, Any]:
        return moved_part, scene

    def render(part: Any, scene: Any | None) -> bytes:
        rendered_parts.append(part)
        rendered_scenes.append(scene)
        return b"glTF-moved" if part is moved_part else b"glTF-canonical"

    def revision(_path: Path, _scene: Any, _part: Any) -> str:
        return "1" * 64

    monkeypatch.setattr(server, "_preview_part_for_moves", preview)
    monkeypatch.setattr(server, "_render_case_glb", render)
    monkeypatch.setattr(server, "_editor_revision", revision)

    moved_response = _move(client, "control.up", [34.9, 60.0])
    empty_response = client.post(
        "/validate-move",
        json={"case_path": "examples/6-button-gamepad/case.py", "moves": []},
    )

    assert moved_response.status_code == 200
    assert empty_response.status_code == 200
    assert rendered_parts == [moved_part, canonical_part]
    assert rendered_scenes[0].to_dict() == moved_response.get_json()["scene"]
    assert rendered_scenes[1].to_dict() == empty_response.get_json()["scene"]
    assert base64.b64decode(moved_response.get_json()["glb_b64"]) == b"glTF-moved"
    assert base64.b64decode(empty_response.get_json()["glb_b64"]) == b"glTF-canonical"


def test_validate_move_returns_and_renders_the_candidate_scene_with_updated_route_endpoint(
    client: FlaskClient, monkeypatch: MonkeyPatch
) -> None:
    from tools.editor import server

    rendered_scenes: list[Any] = []

    def render(_part: Any, scene: Any | None) -> bytes:
        assert scene is not None
        rendered_scenes.append(scene)
        return b"glTF-candidate"

    monkeypatch.setattr(server, "_render_case_glb", render)

    response = _move(client, "control.up", [34.9, 60.0])

    assert response.status_code == 200
    body = response.get_json()
    route = next(item for item in body["scene"]["routes"] if item["id"] == "wire.control_up")
    assert route["waypoints_mm"][0][0] == 34.9
    assert route["target"] == "electronics.breadboard.signal_up"
    live_route = next(item for item in rendered_scenes[0].routes if item.id == "wire.control_up")
    live_target = rendered_scenes[0].port_position(live_route.target)
    assert live_target is not None
    assert rendered_scenes[0].route_waypoints(live_route)[-1] == live_target
    from cadkit.assembly import build_demo_assembly

    expected_target = build_demo_assembly().port_position(live_route.target)
    assert expected_target == live_target
    assert len(rendered_scenes) == 1
    assert rendered_scenes[0].to_dict() == body["scene"]


def test_validate_move_accepts_empty_batch_with_canonical_scene_and_glb(
    client: FlaskClient,
) -> None:
    from cadkit.assembly import build_demo_assembly

    response = client.post(
        "/validate-move",
        json={"case_path": "examples/6-button-gamepad/case.py", "moves": []},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["valid"] is True
    assert body["accepted_moves"] == []
    assert body["revision_moves"] == []
    assert body["scene"] == build_demo_assembly().to_dict()
    assert (
        body["editor_revision"]
        == client.get(
            "/manifest", query_string={"case_path": "examples/6-button-gamepad/case.py"}
        ).get_json()["editor_revision"]
    )
    assert base64.b64decode(body["glb_b64"], validate=True)[:4] == b"glTF"


def test_validate_move_accepts_xy_source_and_returns_rendered_glb(client: FlaskClient) -> None:
    response = _move(client, "control.up", [34.9, 60.0])

    assert response.status_code == 200
    body = response.get_json()
    rendered = base64.b64decode(body["glb_b64"], validate=True)
    assert body["valid"] is True
    assert body["accepted_moves"] == [{"element_id": "control.up", "new_position": [34.9, 60.0]}]
    assert body["revision_moves"] == [
        {
            "element_id": "control.up",
            "expected_position": [35.0, 60.0],
            "new_position": [34.9, 60.0],
        }
    ]
    assert len(body["editor_revision"]) == 64
    assert rendered[:4] == b"glTF"


def test_moved_revision_downloads_both_prototype_shells(client: FlaskClient) -> None:
    moves = [
        {
            "element_id": "control.up",
            "new_position": [34.9, 60.0],
            "expected_position": [35.0, 60.0],
        }
    ]
    validation = client.post(
        "/validate-move",
        json={"case_path": "examples/6-button-gamepad/case.py", "moves": moves},
    )
    assert validation.status_code == 200
    validation_body = validation.get_json()
    revision = validation_body["editor_revision"]
    revision_moves = validation_body["revision_moves"]

    for part_name in ("top", "bottom"):
        response = client.get(
            f"/stl/{part_name}",
            query_string={
                "case_path": "examples/6-button-gamepad/case.py",
                "revision": revision,
                "moves": json.dumps(revision_moves),
            },
        )
        assert response.status_code == 200
        assert response.data
        assert response.headers["X-HF-Editor-Revision"] == revision


def test_manifest_exposes_revision_bound_non_production_shell_downloads(
    client: FlaskClient,
) -> None:
    manifest_response = client.get(
        "/manifest", query_string={"case_path": "examples/6-button-gamepad/case.py"}
    )
    assert manifest_response.status_code == 200
    manifest = manifest_response.get_json()
    revision = manifest["editor_revision"]
    assert len(revision) == 64

    for part_name in ("top", "bottom"):
        response = client.get(
            f"/stl/{part_name}",
            query_string={
                "case_path": "examples/6-button-gamepad/case.py",
                "revision": revision,
            },
        )
        assert response.status_code == 200
        assert response.data
        assert f"filename=case-{part_name}.stl" in response.headers["Content-Disposition"]
        assert response.headers["X-HF-Editor-Revision"] == revision
        assert response.headers["X-HF-Artifact-Status"] == "validated"


def test_prototype_stl_rejects_invalid_parts_and_stale_revisions(client: FlaskClient) -> None:
    invalid_part = client.get(
        "/stl/panel",
        query_string={"case_path": "examples/6-button-gamepad/case.py", "revision": "0" * 64},
    )
    assert invalid_part.status_code == 404

    stale = client.get(
        "/stl/top",
        query_string={"case_path": "examples/6-button-gamepad/case.py", "revision": "0" * 64},
    )
    assert stale.status_code == 409
    assert stale.get_json()["status"] == "stale_revision"

    malformed = client.get(
        "/stl/top",
        query_string={
            "case_path": "examples/6-button-gamepad/case.py",
            "revision": "not-a-revision",
        },
    )
    assert malformed.status_code == 400


def test_revision_moves_compact_repeated_drag_history_and_reject_tampering(
    client: FlaskClient,
) -> None:
    history = [
        {
            "element_id": "control.up",
            "expected_position": [35.0, 60.0],
            "new_position": [34.9, 60.0],
        },
        {
            "element_id": "control.up",
            "expected_position": [34.9, 60.0],
            "new_position": [34.8, 60.0],
        },
    ]
    validation = client.post(
        "/validate-move",
        json={"case_path": "examples/6-button-gamepad/case.py", "moves": history},
    )
    assert validation.status_code == 200
    body = validation.get_json()
    assert body["revision_moves"] == [
        {
            "element_id": "control.up",
            "expected_position": [35.0, 60.0],
            "new_position": [34.8, 60.0],
        }
    ]

    compact = client.get(
        "/stl/top",
        query_string={
            "case_path": "examples/6-button-gamepad/case.py",
            "revision": body["editor_revision"],
            "moves": json.dumps(body["revision_moves"]),
        },
    )
    assert compact.status_code == 200
    assert compact.headers["X-HF-Editor-Revision"] == body["editor_revision"]

    duplicate = client.get(
        "/stl/top",
        query_string={
            "case_path": "examples/6-button-gamepad/case.py",
            "revision": body["editor_revision"],
            "moves": json.dumps(history),
        },
    )
    assert duplicate.status_code == 400

    stale_source = [dict(body["revision_moves"][0], expected_position=[0.0, 0.0])]
    stale = client.get(
        "/stl/top",
        query_string={
            "case_path": "examples/6-button-gamepad/case.py",
            "revision": body["editor_revision"],
            "moves": json.dumps(stale_source),
        },
    )
    assert stale.status_code == 400


def test_validate_move_accepts_down_outward_and_rejects_spacing_collision(
    client: FlaskClient,
) -> None:
    accepted = _move(client, "control.down", [36.0, 20.0]).get_json()
    rejected = _move(client, "control.down", [55.0, 40.0]).get_json()

    assert accepted["valid"] is True
    assert rejected["valid"] is False
    assert any(
        violation["rule"] == "control_control_spacing" for violation in rejected["violations"]
    )
    assert "got 0.0 mm" in next(
        violation["message"]
        for violation in rejected["violations"]
        if violation["rule"] == "control_control_spacing"
    )


@pytest.mark.parametrize(
    ("element_id", "position", "rule"),
    [
        ("control.up", [1.0, 1.0], "case_bounds_keepout"),
        ("control.up", [45.0, 40.0], "control_control_spacing"),
        ("case.shell", [35.0, 20.0], "mobility"),
        ("not-a-node", [35.0, 20.0], "unknown_element"),
    ],
)
def test_validate_move_rejects_each_canonical_rule(
    client: FlaskClient, element_id: str, position: list[float], rule: str
) -> None:
    response = _move(client, element_id, position)

    assert response.status_code == 200
    body = response.get_json()
    assert body["valid"] is False
    assert rule in {violation["rule"] for violation in body["violations"]}
    assert all(violation["message"] for violation in body["violations"])


def test_validate_move_ignores_provisional_feather_and_usb_preview_geometry(
    client: FlaskClient,
) -> None:
    body = _move(client, "control.up", [35.0, 40.0]).get_json()

    assert body["valid"] is True


def test_frontend_reverts_a_rejected_move_before_recording_it(client: FlaskClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "validateMoveOnServer" in html
    assert "Move rejected:" in html
    assert "nodePosition(drag.element)[0] = drag.oldPosition[0]" in html
    assert "const cumulativeMoves = candidateMoves.map(candidate => ({" in html
    assert "body: JSON.stringify({ case_path: CASE_PATH, moves: cumulativeMoves })" in html
    assert "expected_position: candidate.expected_position" in html


def test_frontend_refreshes_viewer_from_accepted_move_glb(client: FlaskClient) -> None:
    html = client.get("/").get_data(as_text=True)

    assert "if (validation.glb_b64)" in html
    assert "glbB64 = validation.glb_b64" in html
    assert "beginViewerLoad(glbB64)" in html


def test_frontend_only_advertises_revision_bound_prototype_shell_downloads(
    client: FlaskClient,
) -> None:
    html = client.get("/").get_data(as_text=True)

    assert "Download shell STLs" in html
    assert "Downloads are validated" in html
    assert "/stl/${part}" in html
    assert "case-${part}.stl" in html


def test_frontend_initializes_model_viewer_with_blob_before_src_and_keeps_2d_ready(
    client: FlaskClient,
) -> None:
    html = client.get("/").get_data(as_text=True)

    assert "new Blob([decodeBase64Bytes(encodedGlb)]" in html
    assert "URL.createObjectURL(blob)" in html
    assert "URL.revokeObjectURL(previousUrl)" in html
    assert html.index('VIEWER.addEventListener("load"') < html.index("VIEWER.src = nextUrl")
    assert html.index('VIEWER.addEventListener("error"') < html.index("VIEWER.src = nextUrl")
    assert "2D ready · 3D loading" in html
    assert "#viewer { display: none" not in html
    assert "#viewer { visibility: hidden; opacity: 0" in html
    assert "#stage.view-3d #viewer { visibility: visible; opacity: 1" in html
    assert 'id="viewer" alt="controller case" loading="eager"' in html
    assert 'VIEWER.classList.add("model-ready")' in html


def test_frontend_stage_ratio_sidebar_scroll_and_route_tooltips_are_contractual(
    client: FlaskClient,
) -> None:
    html = client.get("/").get_data(as_text=True)

    assert "aspect-ratio: 13 / 9" in html
    assert 'document.getElementById("stage").style.aspectRatio = `${width} / ${height}`' in html
    assert "max-height: calc(100vh - 4rem)" in html
    assert "#sidebar" in html and "overflow: auto" in html
    assert 'data-route-id="${escapeXml(route.id)}"' in html
    assert "<title>${escapeXml(title)}</title>" in html
    assert "layout-wire-label" not in html
    assert 'LAYOUT.setAttribute("viewBox", `0 0 ${width} ${height}`)' in html


def test_frontend_resolves_wire_endpoints_from_live_node_positions(client: FlaskClient) -> None:
    html = client.get("/").get_data(as_text=True)

    assert "function attachedRoutePoints(route)" in html
    assert "resolvePortPosition(route.source)" in html
    assert "resolvePortPosition(route.target)" in html
    assert "const points = attachedRoutePoints(route)" in html
