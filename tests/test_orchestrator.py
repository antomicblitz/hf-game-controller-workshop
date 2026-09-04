"""Orchestrator test — Slice 11's iterate_with_visual_feedback().

We mock the opencode subprocess so this test runs without network
or a real opencode install. The contract:
  - regenerate the case.py from feedback + snapshot
  - validate the regenerated code
  - return a result dict with status + violations

We also test the failure modes: missing feedback.json, exec failure.

The test fixtures use the exact-six control contract with the
procurement-plan defaults so the validator passes the canonical 12.4 mm
cutouts for all six controls and 130 × 90 × 65 mm preview envelope. The 65 mm height is an
operator-requested component-fit margin for the preview only, not
ruler-observed receiving evidence or a cleared production gate. Any 98 mm
dimensions in tests are synthetic fixtures, not the canonical preview.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# Make tools/ importable.
_here = Path(__file__).resolve().parent
_session = _here.parent
sys.path.insert(0, str(_session / "tools"))


@pytest.fixture
def case_path(tmp_path: Path) -> Path:
    p = tmp_path / "case.py"
    worked_case = _session / "examples" / "6-button-gamepad" / "case.py"
    p.write_text(worked_case.read_text())
    return p


@pytest.fixture(autouse=True)
def sandbox_backend_available() -> None:
    from cadkit.case_execution import sandbox_status

    if not sandbox_status().available:
        pytest.skip("OS sandbox backend unavailable")


@pytest.fixture
def submit_dir(tmp_path: Path) -> Path:
    d = tmp_path / "submits" / "uuid"
    d.mkdir(parents=True)
    feedback = {
        "moves": [],
        "annotations": [{"id": 1, "kind": "text", "text": "round this edge"}],
        "notes": [],
    }
    (d / "feedback.json").write_text(json.dumps(feedback))
    from PIL import Image

    Image.new("RGB", (10, 10), (255, 255, 255)).save(d / "snapshot.png")
    return d


def _typed_response(case_path: Path, target: list[float]) -> str:
    from tools.editor import server

    part, scene = server.__dict__["_scene_for_case"](case_path)
    assert scene is not None
    return json.dumps(
        {
            "schema": "cadkit.case-edit",
            "version": "1.0",
            "base_revision": server.__dict__["_editor_revision"](case_path, scene, part),
            "control_moves": [{"id": "control.action_a", "target": target}],
            "exterior_design": None,
        }
    )


def test_iterate_returns_ok_status(case_path: Path, submit_dir: Path) -> None:
    from tools.agent.loop import iterate_with_visual_feedback

    original = case_path.read_text()
    with mock.patch(
        "tools.agent.vision.invoke_with_image",
        return_value=_typed_response(case_path, [104.0, 30.0]),
    ):
        result = iterate_with_visual_feedback(case_path, submit_dir, max_iterations=1)

    assert result["status"] == "ok"
    assert result["iterations"] == 1
    assert result["case_path"] == str(case_path)
    assert result["llm_accepted_moves"] == [
        {"element_id": "control.action_a", "new_position": [104.0, 30.0]}
    ]
    assert case_path.read_text() == original.replace('"x": 105.0,', '"x": 104.0,', 1)


def test_iterate_returns_vision_error_on_missing_files(tmp_path: Path) -> None:
    from tools.agent.loop import iterate_with_visual_feedback

    empty_case = tmp_path / "case.py"
    empty_case.write_text("# empty")
    bad_submit = tmp_path / "bad"
    bad_submit.mkdir()
    # No feedback.json, no snapshot.png.
    result = iterate_with_visual_feedback(empty_case, bad_submit, max_iterations=1)
    assert result["status"] == "vision_error"
    assert "feedback.json" in result["error"] or "snapshot.png" in result["error"]


def test_iterate_handles_runtime_error_from_opencode(case_path: Path, submit_dir: Path) -> None:
    from tools.agent.loop import iterate_with_visual_feedback

    with mock.patch(
        "tools.agent.vision.invoke_with_image", side_effect=RuntimeError("opencode not installed")
    ):
        result = iterate_with_visual_feedback(case_path, submit_dir, max_iterations=1)

    assert result["status"] == "vision_error"
    assert "opencode not installed" in result["error"]


def test_iterate_writes_regenerated_source(case_path: Path, submit_dir: Path) -> None:
    from tools.agent.loop import iterate_with_visual_feedback

    with mock.patch(
        "tools.agent.vision.invoke_with_image",
        return_value=_typed_response(case_path, [104.0, 30.0]),
    ):
        result = iterate_with_visual_feedback(case_path, submit_dir, max_iterations=1)

    assert result["status"] == "ok"
    # The case.py on disk should now have the new source.
    assert '"x": 104.0' in case_path.read_text()


def test_iterate_rejects_unsafe_typed_move_with_structured_feedback(
    submit_dir: Path, tmp_path: Path
) -> None:
    from tools.agent.loop import iterate_with_visual_feedback

    worked_case = _session / "examples" / "6-button-gamepad" / "case.py"
    output_path = tmp_path / "candidate" / "case.py"
    output_path.parent.mkdir()

    with mock.patch(
        "tools.agent.vision.invoke_with_image",
        return_value=_typed_response(worked_case, [55.0, 40.0]),
    ):
        result = iterate_with_visual_feedback(
            worked_case,
            submit_dir,
            max_iterations=1,
            output_path=output_path,
        )

    assert result["status"] == "vision_error"
    assert result["violations"][0]["rule"] == "llm_move_validation"
    assert not output_path.exists()


def test_iterate_retries_malformed_proposal_with_structured_feedback(
    submit_dir: Path, tmp_path: Path
) -> None:
    from tools.agent.loop import iterate_with_visual_feedback

    worked_case = _session / "examples" / "6-button-gamepad" / "case.py"
    output_path = tmp_path / "candidate" / "case.py"
    output_path.parent.mkdir()
    original = worked_case.read_bytes()
    corrected = _typed_response(worked_case, [104.0, 30.0])
    model_outputs = ["not JSON", corrected]

    with mock.patch("tools.agent.vision.invoke_with_image", side_effect=model_outputs) as invoke:
        result = iterate_with_visual_feedback(
            worked_case,
            submit_dir,
            max_iterations=2,
            output_path=output_path,
        )

    assert result["status"] == "ok"
    assert invoke.call_count == 2
    assert "malformed_json" in invoke.call_args_list[1].args[0]
    assert worked_case.read_bytes() == original
    assert '"x": 104.0' in output_path.read_text()


def test_iterate_retries_when_generated_source_has_invalid_python(
    submit_dir: Path, tmp_path: Path
) -> None:
    from tools.agent.loop import iterate_with_visual_feedback

    worked_case = _session / "examples" / "6-button-gamepad" / "case.py"
    output_path = tmp_path / "candidate" / "case.py"
    output_path.parent.mkdir()

    with mock.patch("tools.agent.vision.invoke_with_image", return_value="case = 'unterminated"):
        result = iterate_with_visual_feedback(
            worked_case,
            submit_dir,
            max_iterations=1,
            output_path=output_path,
        )

    assert result["status"] == "vision_error"
    assert result["violations"][0]["rule"] == "malformed_json"
    assert not output_path.exists()


def test_iterate_in_place_retries_invalid_python_from_last_valid_source(
    case_path: Path, submit_dir: Path
) -> None:
    from tools.agent.loop import iterate_with_visual_feedback

    original = case_path.read_bytes()
    with mock.patch("tools.agent.vision.invoke_with_image", return_value="case = 'unterminated"):
        result = iterate_with_visual_feedback(case_path, submit_dir, max_iterations=1)

    assert result["status"] == "vision_error"
    assert result["violations"][0]["rule"] == "malformed_json"
    assert case_path.read_bytes() == original


def test_iterate_returns_structured_syntax_error_after_retry_budget(
    case_path: Path, submit_dir: Path
) -> None:
    from tools.agent.loop import iterate_with_visual_feedback

    with (
        mock.patch("tools.agent.vision.invoke_with_image", return_value="case = 'unterminated"),
        mock.patch("cadkit.case_execution.execute_case_artifacts") as execute,
    ):
        result = iterate_with_visual_feedback(case_path, submit_dir, max_iterations=1)

    assert result["status"] == "vision_error"
    assert result["iterations"] == 0
    assert "assembly metadata" in result["error"]
    assert execute.call_count == 1


def test_iterate_keeps_sandbox_timeout_as_execution_error(
    case_path: Path, submit_dir: Path
) -> None:
    from cadkit.case_execution import CaseExecutionTimeout

    from tools.agent.loop import iterate_with_visual_feedback

    with (
        mock.patch("tools.agent.vision.invoke_with_image", return_value="case = 'unterminated"),
        mock.patch(
            "cadkit.case_execution.execute_case_artifacts",
            side_effect=CaseExecutionTimeout("case execution timed out"),
        ),
    ):
        result = iterate_with_visual_feedback(case_path, submit_dir, max_iterations=2)

    assert result["status"] == "vision_error"
    assert result["iterations"] == 0
    assert "CaseExecutionTimeout" in result["error"]
