"""Behavioral coverage for the MIG-04 Slice-C typed-edit integration."""

from __future__ import annotations

import base64
import json
import math
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from _pytest.monkeypatch import MonkeyPatch
from flask.testing import FlaskClient

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))
_case = _session / "examples" / "6-button-gamepad" / "case.py"
_revision = "a" * 64
_positions = {
    "control.up": (35.0, 60.0),
    "control.down": (35.0, 20.0),
    "control.right": (55.0, 40.0),
    "control.left": (15.0, 40.0),
    "control.action_a": (105.0, 30.0),
    "control.action_b": (105.0, 55.0),
}


def _proposal(**overrides: object) -> str:
    value: dict[str, object] = {
        "schema": "cadkit.case-edit",
        "version": "1.0",
        "base_revision": _revision,
        "control_moves": [],
        "exterior_design": {"profile": "snes_inspired"},
    }
    value.update(overrides)
    return json.dumps(value)


@pytest.fixture
def submit_client() -> Iterator[FlaskClient]:
    from tools.editor import server

    app = server.create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def isolated_case() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(dir=_case.parent, prefix=".mig04-submit-") as directory:
        case_path = Path(directory) / "case.py"
        shutil.copy2(_case, case_path)
        original = case_path.read_bytes()
        original_mode = stat.S_IMODE(case_path.stat().st_mode)
        try:
            yield case_path
        finally:
            case_path.write_bytes(original)
            case_path.chmod(original_mode)


def _submit_case_path(case_path: Path) -> str:
    return case_path.relative_to(_session).as_posix()


def _submit_snapshot() -> str:
    from tools.editor import server

    return base64.b64encode(server.make_test_base_png(800, 600)).decode("ascii")


def _case_revision(case_path: Path) -> str:
    from tools.editor import server

    part, scene = server.__dict__["_scene_for_case"](case_path)
    assert scene is not None
    return cast(str, server.__dict__["_editor_revision"](case_path, scene, part))


def _artifact_scene(artifacts: Any) -> Any:
    from cadkit.assembly import AssemblyScene

    assembly = artifacts.assembly
    assert isinstance(assembly, dict)
    return AssemblyScene.from_dict(cast(dict[str, Any], assembly))


def _patch_model_response(monkeypatch: MonkeyPatch, response: str) -> None:
    from agent import vision as runtime_vision

    from tools.agent import vision

    def invoke_model(*_args: object, **_kwargs: object) -> str:
        return response

    monkeypatch.setattr(vision, "invoke_with_image", invoke_model)
    monkeypatch.setattr(runtime_vision, "invoke_with_image", invoke_model)


def _require_submit_sandbox() -> None:
    from cadkit.case_execution import sandbox_status

    if not sandbox_status().available:
        pytest.skip("OS sandbox backend unavailable")


def test_submit_publishes_exterior_only_model_proposal(
    submit_client: FlaskClient,
    isolated_case: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _require_submit_sandbox()
    from cadkit.case_execution import execute_case_artifacts

    before = execute_case_artifacts(isolated_case)
    before_scene = _artifact_scene(before)
    from tools.editor import server

    revision = server.__dict__["_editor_revision"](isolated_case, before_scene, before.part)
    response = _proposal(
        base_revision=revision,
        exterior_design={"profile": "snes_inspired"},
    )
    _patch_model_response(monkeypatch, response)

    result = submit_client.post(
        "/submit",
        json={
            "case_path": _submit_case_path(isolated_case),
            "feedback": {"notes": ["make the case more curved and SNES-inspired"]},
            "snapshot_b64": _submit_snapshot(),
        },
    )

    assert result.status_code == 200, result.get_json()
    body = result.get_json()
    assert body["status"] == "ok"
    assert body["manifest"]["editor_revision"]
    assert base64.b64decode(body["glb_b64"]).startswith(b"glTF")
    source = isolated_case.read_text(encoding="utf-8")
    assert 'ExteriorDesignSpec(profile="snes_inspired")' in source

    after = execute_case_artifacts(isolated_case)
    after_scene = _artifact_scene(after)
    assert body["assembly"] == after.assembly
    assert after_scene.to_dict() == before_scene.to_dict()
    assert not math.isclose(before.part.volume, after.part.volume, abs_tol=1e-6)

    assert body["manifest"]["editor_revision"] == server.__dict__["_editor_revision"](
        isolated_case, after_scene, after.part
    )


@pytest.mark.parametrize(
    "design",
    [
        {"profile": "custom", "melt_depth_mm": 1.0},
        {"profile": "custom", "engraved_text": "PLAYER ONE"},
        {
            "profile": "custom",
            "side_grip_length_mm": 20.0,
            "side_grip_splay_deg": 10.0,
        },
        {"profile": "custom", "groove_count": 4, "groove_depth_mm": 1.0},
        {"profile": "playstation_inspired"},
        {"profile": "xbox_inspired"},
        {"profile": "switch_inspired"},
        {"profile": "custom", "taper_deg": 18.0},
        {
            "profile": "custom",
            "length_proportion": 0.98,
            "thickness_proportion": 0.97,
        },
        {
            "profile": "custom",
            "emblem_svg": '<svg viewBox="0 0 20 20">'
            '<path d="M 10 1 L 19 10 L 10 19 L 1 10 Z"/></svg>',
            "emblem_size_mm": 12.0,
        },
    ],
)
def test_model_exterior_designs_round_trip_through_parser_and_source_rewriter(
    design: dict[str, object],
) -> None:
    from cadkit import ExteriorDesignSpec
    from cadkit.case_source import (
        inspect_exterior_design_source,
        rewrite_exterior_design_source,
        source_changes_outside_editable,
    )

    from tools.agent.case_edits import validate_case_edit

    expected = ExteriorDesignSpec.from_dict(design)
    proposal = validate_case_edit(
        _proposal(exterior_design=design),
        current_revision=_revision,
        current_positions=_positions,
        current_exterior=ExteriorDesignSpec(profile="rounded"),
    )
    source = _case.read_text(encoding="utf-8")
    accepted_source = rewrite_exterior_design_source(
        source, ExteriorDesignSpec(profile="custom", melt_depth_mm=1.0)
    )
    rewritten = rewrite_exterior_design_source(accepted_source, expected)

    assert proposal.exterior_design == expected
    assert inspect_exterior_design_source(accepted_source) == ExteriorDesignSpec(
        profile="custom", melt_depth_mm=1.0
    )
    assert inspect_exterior_design_source(rewritten) == expected
    assert source_changes_outside_editable(accepted_source, rewritten) is False


def test_submit_retries_failed_exterior_and_keeps_canonical_base_until_success(
    submit_client: FlaskClient,
    isolated_case: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _require_submit_sandbox()
    from agent import vision as runtime_vision
    from cadkit.case_execution import execute_case_artifacts

    from tools.agent import vision

    original = isolated_case.read_bytes()
    revision = _case_revision(isolated_case)
    responses = iter(
        (
            _proposal(
                base_revision=revision,
                exterior_design={
                    "profile": "custom",
                    "roundness_mm": 16.0,
                    "shoulder_inset_mm": 0.0,
                    "side_grip_length_mm": 8.0,
                    "side_grip_splay_deg": 0.0,
                    "center_grip_length_mm": None,
                },
            ),
            _proposal(base_revision=revision, exterior_design={"profile": "snes_inspired"}),
        )
    )
    calls = 0

    def invoke_model(*_args: object, **_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            assert isolated_case.read_bytes() == original
        return next(responses)

    monkeypatch.setattr(vision, "invoke_with_image", invoke_model)
    monkeypatch.setattr(runtime_vision, "invoke_with_image", invoke_model)
    result = submit_client.post(
        "/submit",
        json={
            "case_path": _submit_case_path(isolated_case),
            "feedback": {"notes": ["make an impossible protected exterior change"]},
            "snapshot_b64": _submit_snapshot(),
        },
    )

    assert result.status_code == 200
    assert calls == 2
    assert 'ExteriorDesignSpec(profile="snes_inspired")' in isolated_case.read_text(
        encoding="utf-8"
    )
    assert execute_case_artifacts(isolated_case).part.volume > 0.0


def test_exterior_parser_uses_authoritative_cadkit_value_object() -> None:
    from cadkit import ExteriorDesignSpec

    from tools.agent.case_edits import parse_case_edit

    proposal = parse_case_edit(_proposal())

    assert type(proposal.exterior_design) is ExteriorDesignSpec
    assert proposal.exterior_design == ExteriorDesignSpec(profile="snes_inspired")


def test_exterior_only_changes_part_without_control_scene_changes() -> None:
    from cadkit import ExteriorDesignSpec, gamepad_body

    from tools.agent import vision
    from tools.editor import server

    scene = server.__dict__["_scene_for_case"](_case)[1]
    assert scene is not None
    final_scene, accepted, exterior = vision._apply_llm_proposal(  # pyright: ignore[reportPrivateUsage]
        scene,
        _proposal(),
        revision=_revision,
        current_exterior=ExteriorDesignSpec(profile="rounded"),
    )
    rounded = gamepad_body(exterior_design=ExteriorDesignSpec(profile="rounded"))
    snes = gamepad_body(exterior_design=exterior)

    assert accepted == []
    assert final_scene.to_dict() == scene.to_dict()
    assert snes.volume != rounded.volume


def test_mixed_n64_proposal_preserves_exterior_and_action_b_move() -> None:
    from cadkit import ExteriorDesignSpec
    from cadkit.assembly import build_demo_assembly

    from tools.agent import vision

    scene = build_demo_assembly()
    response = _proposal(
        control_moves=[{"id": "control.action_b", "target": [105.0, 60.0]}],
        exterior_design={"profile": "n64_inspired"},
    )
    final_scene, accepted, exterior = vision._apply_llm_proposal(  # pyright: ignore[reportPrivateUsage]
        scene,
        response,
        revision=_revision,
        current_exterior=ExteriorDesignSpec(profile="rounded"),
    )

    assert accepted == [{"element_id": "control.action_b", "new_position": [105.0, 60.0]}]
    assert final_scene.node("control.action_b").transform.position[:2] == (105.0, 60.0)
    assert exterior == ExteriorDesignSpec(profile="n64_inspired")


@pytest.mark.parametrize(
    "design",
    [
        {"profile": "rounded"},
        {"profile": "custom", "roundness_mm": 6.0, "shoulder_inset_mm": 4.0},
    ],
)
def test_safe_named_and_custom_exterior_designs_are_accepted(design: dict[str, object]) -> None:
    from cadkit import ExteriorDesignSpec

    from tools.agent.case_edits import validate_case_edit

    proposal = validate_case_edit(
        _proposal(exterior_design=design),
        current_revision=_revision,
        current_positions=_positions,
        current_exterior=ExteriorDesignSpec(profile="snes_inspired"),
    )

    assert proposal.exterior_design is not None


@pytest.mark.parametrize(
    "response",
    [
        _proposal(unsupported_reason="resize the USB opening"),
        _proposal(exterior_design={"profile": "custom", "points": [[0, 0], [1, 1]]}),
        _proposal(exterior_design={"profile": "custom", "roundness_mm": "6"}),
        _proposal(exterior_design={"profile": "custom", "roundness_mm": 6.0, "z_mm": 1.0}),
        _proposal(exterior_design={"profile": "custom", "snap_clearance_mm": 0.3}),
        _proposal(exterior_design={"profile": "custom", "usb_width_mm": 14.0}),
        "from build123d import Box\ncase = Box(1, 1, 1)",
    ],
)
def test_unsupported_raw_source_and_unbounded_exterior_requests_fail(response: str) -> None:
    from cadkit import ExteriorDesignSpec

    from tools.agent.case_edits import CaseEditError, validate_case_edit

    with pytest.raises(CaseEditError):
        validate_case_edit(
            response,
            current_revision=_revision,
            current_positions=_positions,
            current_exterior=ExteriorDesignSpec(profile="rounded"),
        )


def test_stale_and_noop_exterior_requests_are_rejected() -> None:
    from cadkit import ExteriorDesignSpec

    from tools.agent.case_edits import CaseEditError, validate_case_edit

    with pytest.raises(CaseEditError, match="stale"):
        validate_case_edit(
            _proposal(base_revision="b" * 64),
            current_revision=_revision,
            current_positions=_positions,
            current_exterior=ExteriorDesignSpec(profile="rounded"),
        )
    with pytest.raises(CaseEditError, match="does not change"):
        validate_case_edit(
            _proposal(exterior_design={"profile": "rounded"}),
            current_revision=_revision,
            current_positions=_positions,
            current_exterior=ExteriorDesignSpec(profile="rounded"),
        )
    with pytest.raises(CaseEditError, match="combined"):
        validate_case_edit(
            _proposal(
                control_moves=[{"id": "control.action_a", "target": [104.0, 30.0]}],
                unsupported_reason="change the silhouette",
            ),
            current_revision=_revision,
            current_positions=_positions,
            current_exterior=ExteriorDesignSpec(profile="rounded"),
        )


def test_control_target_that_rounds_to_current_position_is_rejected() -> None:
    from cadkit import ExteriorDesignSpec
    from cadkit.assembly import build_demo_assembly

    from tools.agent import vision
    from tools.agent.case_edits import CaseEditError

    scene = build_demo_assembly()
    response = _proposal(
        control_moves=[{"id": "control.action_a", "target": [104.96, 30.0]}],
        exterior_design=None,
    )

    with pytest.raises(CaseEditError, match="round to their current position"):
        vision._apply_llm_proposal(  # pyright: ignore[reportPrivateUsage]
            scene,
            response,
            revision=_revision,
            current_exterior=ExteriorDesignSpec(profile="rounded"),
        )


def test_source_rewriter_preserves_every_byte_outside_trusted_bindings() -> None:
    from cadkit import ExteriorDesignSpec
    from cadkit.case_source import (
        inspect_exterior_design_source,
        rewrite_exterior_design_source,
        source_changes_outside_editable,
    )

    source = _case.read_text(encoding="utf-8")
    target = ExteriorDesignSpec(profile="n64_inspired")
    changed = rewrite_exterior_design_source(source, target)

    assert inspect_exterior_design_source(changed) == target
    assert source_changes_outside_editable(source, changed) is False
    assert "CASE_THICKNESS_MM = 65.0" in changed
    assert "exterior_design=EXTERIOR_DESIGN" in changed

    altered = changed.replace("FEATHER_VARIANT =", "FEATHER_VARIANT = # altered\n#")
    assert source_changes_outside_editable(source, altered) is True


def test_builtin_exteriors_pass_all_derived_protected_regions() -> None:
    from cadkit import ExteriorDesignSpec, gamepad_body, validate_exterior_geometry

    from tools.editor import server

    _base_part, scene = server.__dict__["_scene_for_case"](_case)
    assert scene is not None
    regions = server.__dict__["_protected_exterior_regions"](scene)
    baseline = gamepad_body(exterior_design=ExteriorDesignSpec(profile="rounded"))
    for profile in ("snes_inspired", "n64_inspired"):
        candidate = gamepad_body(exterior_design=ExteriorDesignSpec(profile=profile))
        assert (
            validate_exterior_geometry(
                baseline,
                candidate,
                protected_regions=regions,
                expected_bounds=baseline.bounding_box(),
            )
            == []
        )


def test_moved_control_protection_is_not_dropped_from_candidate_regions() -> None:
    from build123d import Align, Box, Location, Part
    from cadkit import validate_exterior_geometry

    from tools.editor import server

    _base_part, scene = server.__dict__["_scene_for_case"](_case)
    assert scene is not None
    moved_scene, _accepted = server.__dict__["_apply_revision_moves"](
        scene,
        [
            {
                "element_id": "control.action_b",
                "expected_position": [105.0, 55.0],
                "new_position": [105.0, 60.0],
            }
        ],
    )
    regions = server.__dict__["_protected_exterior_regions"](moved_scene)
    action_region = next(
        region for region in regions if region.name == "control.action_b.land_opening_ligament"
    )

    frame = Box(130.0, 90.0, 65.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    moved_control_notch = Location((40.0, 15.0, 64.0)) * Box(2.0, 2.0, 2.0, align=Align.CENTER)
    unrelated_notch = Location((-40.0, -15.0, 64.0)) * Box(2.0, 2.0, 2.0, align=Align.CENTER)
    baseline = cast(Part, frame - moved_control_notch)
    candidate = cast(Part, frame - unrelated_notch)

    assert validate_exterior_geometry(baseline, candidate, protected_regions=[]) == []
    violations = validate_exterior_geometry(
        baseline,
        candidate,
        protected_regions=[action_region],
        expected_bounds=baseline.bounding_box(),
    )
    assert any(violation.rule == "exterior_protected_occupancy_changed" for violation in violations)


def test_only_unchanged_preview_exceptions_are_grandfathered() -> None:
    from tools.agent import loop
    from tools.editor import server

    baseline = [
        SimpleNamespace(
            category="print",
            rule="keepout_overlap",
            severity="error",
            message="same",
            location="x",
        )
    ]
    same = list(baseline)
    changed = [SimpleNamespace(**{**baseline[0].__dict__, "location": "y"})]

    assert server.__dict__["_prototype_errors_are_grandfathered"](same, baseline) is True
    assert server.__dict__["_prototype_errors_are_grandfathered"](changed, baseline) is False
    assert server.__dict__["_prototype_errors_are_grandfathered"]([], baseline) is False
    assert loop.__dict__["_has_blocking_candidate_violations"](same, baseline) is False
    assert loop.__dict__["_has_blocking_candidate_violations"](changed, baseline) is True
    assert loop.__dict__["_has_blocking_candidate_violations"]([], baseline) is True


def test_visual_candidate_geometry_detector_rejects_an_identical_part() -> None:
    from build123d import Align, Box, Location, Part

    from tools.agent import loop

    baseline = Box(20.0, 20.0, 4.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    rebuilt = Box(20.0, 20.0, 4.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    changed = cast(
        Part,
        rebuilt - Location((0.0, 0.0, 3.5)) * Box(2.0, 2.0, 1.0, align=Align.CENTER),
    )

    assert loop.__dict__["_candidate_geometry_changed"](baseline, rebuilt) is False
    assert loop.__dict__["_candidate_geometry_changed"](baseline, changed) is True


def test_reset_restores_default_exterior_and_control_positions_transactionally(
    submit_client: FlaskClient,
    isolated_case: Path,
) -> None:
    _require_submit_sandbox()
    from cadkit import ExteriorDesignSpec
    from cadkit.case_execution import execute_case_artifacts
    from cadkit.case_source import (
        inspect_exterior_design_source,
        rewrite_controls_source,
        rewrite_exterior_design_source,
    )

    source = rewrite_controls_source(
        isolated_case.read_text(encoding="utf-8"),
        {"control.action_a": (95.0, 30.0)},
    )
    source = rewrite_exterior_design_source(
        source,
        ExteriorDesignSpec(profile="custom", engraved_text="TEST"),
    )
    isolated_case.write_text(source, encoding="utf-8")

    result = submit_client.post(
        "/reset",
        json={
            "case_path": _submit_case_path(isolated_case),
            "editor_revision": _case_revision(isolated_case),
        },
    )

    assert result.status_code == 200, result.get_json()
    body = result.get_json()
    assert body["status"] == "reset"
    assert base64.b64decode(body["glb_b64"]).startswith(b"glTF")
    assert inspect_exterior_design_source(
        isolated_case.read_text(encoding="utf-8")
    ) == ExteriorDesignSpec(profile="rounded")
    scene = _artifact_scene(execute_case_artifacts(isolated_case))
    assert scene.node("control.action_a").transform.position[:2] == _positions["control.action_a"]
    assert body["assembly"] == scene.to_dict()


def test_reset_rejects_a_stale_revision_without_changing_source(
    submit_client: FlaskClient,
    isolated_case: Path,
) -> None:
    before = isolated_case.read_bytes()

    result = submit_client.post(
        "/reset",
        json={
            "case_path": _submit_case_path(isolated_case),
            "editor_revision": "0" * 64,
        },
    )

    assert result.status_code == 409
    assert result.get_json()["status"] == "stale_revision"
    assert isolated_case.read_bytes() == before


def test_active_example_is_sandboxed_and_splits_to_two_solids() -> None:
    from cadkit import orient_case_halves_for_print, snap_fit_pair
    from cadkit.case_execution import execute_case_artifacts, sandbox_status

    if not sandbox_status().available:
        pytest.skip("OS sandbox backend unavailable")
    artifacts = execute_case_artifacts(_case)
    top, bottom = snap_fit_pair(artifacts.part)
    print_top, print_bottom = orient_case_halves_for_print(top, bottom)

    assert artifacts.assembly is not None
    assert len(top.solids()) == len(bottom.solids()) == 1
    assert len(print_top.solids()) == len(print_bottom.solids()) == 1
