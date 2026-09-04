"""Behavioral tests for the strict MIG-04 Slice-A edit boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))


REVISION = "a" * 64
CURRENT = {
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
        "base_revision": REVISION,
        "control_moves": [{"id": "control.action_a", "target": [97.0, 30.0]}],
        "exterior_design": None,
    }
    value.update(overrides)
    return json.dumps(value, allow_nan=True)


def test_case_edit_parses_canonical_move_and_adapts_to_editor_record() -> None:
    from tools.agent.case_edits import proposal_move_records, validate_case_edit

    proposal = validate_case_edit(_proposal(), current_revision=REVISION, current_positions=CURRENT)

    assert proposal.control_moves[0].control_id == "control.action_a"
    assert proposal.control_moves[0].target == (97.0, 30.0)
    assert proposal_move_records(proposal, CURRENT) == [
        {
            "element_id": "control.action_a",
            "expected_position": [105.0, 30.0],
            "new_position": [97.0, 30.0],
        }
    ]


def test_case_edit_normalizes_unique_moves_to_canonical_order() -> None:
    from tools.agent.case_edits import proposal_move_records, validate_case_edit

    proposal = validate_case_edit(
        _proposal(
            control_moves=[
                {"id": "control.action_b", "target": [105.0, 60.0]},
                {"id": "control.up", "target": [35.0, 65.0]},
            ]
        ),
        current_revision=REVISION,
        current_positions=CURRENT,
    )

    assert [move.control_id for move in proposal.control_moves] == [
        "control.up",
        "control.action_b",
    ]
    assert proposal_move_records(proposal, CURRENT) == [
        {
            "element_id": "control.up",
            "expected_position": [35.0, 60.0],
            "new_position": [35.0, 65.0],
        },
        {
            "element_id": "control.action_b",
            "expected_position": [105.0, 55.0],
            "new_position": [105.0, 60.0],
        },
    ]


@pytest.mark.parametrize(
    "response",
    [
        _proposal(extra="forged"),
        _proposal(control_moves=[{"id": "control.rogue", "target": [1.0, 1.0]}]),
        _proposal(
            control_moves=[
                {"id": "control.action_a", "target": [97.0, 30.0]},
                {"id": "control.action_a", "target": [96.0, 30.0]},
            ]
        ),
        _proposal(control_moves=[{"id": "control.action_a", "target": [float("nan"), 30.0]}]),
        "```python\ncase = object()\n```",
        "case = object()",
    ],
)
def test_case_edit_rejects_forged_duplicate_nonfinite_and_source_outputs(
    response: str,
) -> None:
    from tools.agent.case_edits import CaseEditError, validate_case_edit

    with pytest.raises(CaseEditError):
        validate_case_edit(response, current_revision=REVISION, current_positions=CURRENT)


def test_case_edit_rejects_stale_and_accepts_exterior_with_current_baseline() -> None:
    from cadkit import ExteriorDesignSpec

    from tools.agent.case_edits import CaseEditError, validate_case_edit

    with pytest.raises(CaseEditError, match="stale"):
        validate_case_edit(
            _proposal(base_revision="b" * 64),
            current_revision=REVISION,
            current_positions=CURRENT,
        )
    with pytest.raises(CaseEditError, match="does not change"):
        validate_case_edit(
            _proposal(control_moves=[{"id": "control.action_a", "target": [105.0, 30.0]}]),
            current_revision=REVISION,
            current_positions=CURRENT,
        )
    proposal = validate_case_edit(
        _proposal(exterior_design={"profile": "snes_inspired"}),
        current_revision=REVISION,
        current_positions=CURRENT,
        current_exterior=ExteriorDesignSpec(profile="rounded"),
    )
    assert proposal.exterior_design == ExteriorDesignSpec(profile="snes_inspired")


def test_case_edit_reports_an_explicitly_unsupported_request() -> None:
    from tools.agent.case_edits import CaseEditError, validate_case_edit

    with pytest.raises(CaseEditError, match="not applicable"):
        validate_case_edit(
            _proposal(control_moves=[], unsupported_reason="resize the USB opening"),
            current_revision=REVISION,
            current_positions=CURRENT,
        )


def test_case_edit_exterior_shape_is_strict_and_slice_c_ready() -> None:
    from tools.agent.case_edits import parse_case_edit

    proposal = parse_case_edit(
        _proposal(
            control_moves=[],
            exterior_design={
                "profile": "custom",
                "roundness_mm": 6.0,
                "shoulder_inset_mm": 12.0,
                "side_grip_length_mm": 20.0,
                "side_grip_splay_deg": 10.0,
                "center_grip_length_mm": 8.0,
            },
            unsupported_reason="reserved for Slice C",
        )
    )
    assert proposal.exterior_design is not None
    assert proposal.exterior_design.to_dict() == {
        "profile": "custom",
        "roundness_mm": 6.0,
        "shoulder_inset_mm": 12.0,
        "side_grip_length_mm": 20.0,
        "side_grip_splay_deg": 10.0,
        "center_grip_length_mm": 8.0,
        "melt_depth_mm": 0.0,
        "engraved_text": None,
        "groove_count": 0,
        "groove_depth_mm": 0.0,
        "chamfer_mm": 0.0,
        "taper_deg": 0.0,
        "length_proportion": 1.0,
        "width_proportion": 1.0,
        "thickness_proportion": 1.0,
        "emblem_svg": None,
        "emblem_size_mm": 0.0,
    }


def test_case_source_rewrites_only_requested_literal_xy_fields() -> None:
    from tools.cadkit.case_source import rewrite_controls_source

    source = (
        "# preserve this comment\n"
        "CONTROLS = [\n"
        '    {"id": "action_a", "role": "ACTION_A", "x": 105.0, "y": 30.0},\n'
        '    {"id": "action_b", "role": "ACTION_B", "x": 105.0, "y": 55.0},\n'
        "]\n"
        "OTHER = {'x': 105.0, 'y': 30.0}\n"
    )
    rewritten = rewrite_controls_source(source, {"control.action_a": (97.0, 30.0)})

    assert rewritten == source.replace('"x": 105.0', '"x": 97.0', 1)
    assert '"y": 30.0' in rewritten
    assert "OTHER = {'x': 105.0, 'y': 30.0}" in rewritten
