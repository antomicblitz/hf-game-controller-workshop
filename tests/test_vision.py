"""Vision module tests — Slice 11.

We can't make a real opencode CLI call from tests, so we test:
  1. The user-message builder produces well-formed input.
  2. The submission queue round-trips through /submit + filesystem.
  3. The system prompt is non-trivial and references the cadkit primitives.

For the full regenerate_case() pipeline, we mock the opencode
subprocess call.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from _pytest.monkeypatch import MonkeyPatch

# Make tools/ importable.
_here = Path(__file__).resolve().parent
_session = _here.parent
sys.path.insert(0, str(_session / "tools"))


@pytest.fixture
def case_path(tmp_path: Path) -> Path:
    """A canonical case.py copy in a tmp dir."""
    p = tmp_path / "case.py"
    worked_case = _session / "examples" / "6-button-gamepad" / "case.py"
    p.write_text(worked_case.read_text())
    return p


@pytest.fixture
def submit_dir(tmp_path: Path) -> Path:
    """A submit dir with feedback.json + snapshot.png."""
    d = tmp_path / "submits" / "test-uuid"
    d.mkdir(parents=True)
    feedback = {
        "moves": [],
        "annotations": [
            {
                "id": 1,
                "kind": "arrow",
                "anchor": [100, 100],
                "end": [200, 200],
                "text": "make this edge rounder",
                "element_id": "edge_top_left",
            },
        ],
        "notes": ["Round the corners more"],
    }
    (d / "feedback.json").write_text(json.dumps(feedback))
    # Tiny 1×1 PNG.
    from PIL import Image

    img = Image.new("RGB", (1, 1), (255, 255, 255))
    img.save(d / "snapshot.png")
    return d


def test_build_user_message_includes_case_and_feedback(case_path: Path, submit_dir: Path) -> None:
    from tools.agent.vision import build_user_message

    feedback: dict[str, Any] = {
        "moves": [
            {
                "element_id": "control.action_a",
                "expected_position": [118, 70],
                "new_position": [114, 70],
            }
        ]
    }
    msg = build_user_message(case_path.read_text(), feedback, submit_dir)
    assert "case.py" in msg
    assert "control.action_a" in msg
    assert "button_3" not in msg
    # JSON is pretty-printed, so the list expands to multiple lines.
    assert "114" in msg and "70" in msg
    assert "snapshot.png" in msg  # the snapshot exists in submit_dir
    assert "from build123d import" not in msg
    assert "def build_case" not in msg
    assert '"taper_deg"' in msg
    assert '"length_proportion"' in msg
    assert '"width_proportion"' in msg
    assert '"thickness_proportion"' in msg
    assert '"emblem_svg"' in msg
    assert '"emblem_size_mm"' in msg
    assert '"playstation_inspired"' in msg
    assert '"xbox_inspired"' in msg
    assert '"switch_inspired"' in msg


def test_build_user_message_omits_snapshot_when_missing(case_path: Path, tmp_path: Path) -> None:
    from tools.agent.vision import build_user_message

    empty_submit = tmp_path / "no_snapshot"
    empty_submit.mkdir()
    msg = build_user_message(case_path.read_text(), {}, empty_submit)
    assert "snapshot.png" not in msg
    assert "## Annotated screenshot" not in msg


def test_build_user_message_includes_prompt(case_path: Path, tmp_path: Path) -> None:
    """High-level student prompt must surface as its own section so the
    agent applies it FIRST (before moves/annotations)."""
    from tools.agent.vision import build_user_message

    empty_submit = tmp_path / "submit"
    empty_submit.mkdir()
    feedback: dict[str, Any] = {
        "prompt": "Round all the outer corners with a 6mm fillet.",
        "moves": [
            {
                "element_id": "control.action_a",
                "expected_position": [118, 70],
                "new_position": [114, 70],
            }
        ],
    }
    msg = build_user_message(case_path.read_text(), feedback, empty_submit)
    # The prompt section appears before the JSON section so the agent
    # literally sees it first.
    assert "## Student prompt" in msg
    assert "Round all the outer corners with a 6mm fillet." in msg
    prompt_idx = msg.find("## Student prompt")
    json_idx = msg.find("## Feedback JSON")
    assert prompt_idx < json_idx, "prompt must precede the feedback JSON"


def test_build_user_message_omits_prompt_section_when_empty(
    case_path: Path, tmp_path: Path
) -> None:
    """No `prompt` field in feedback → no prompt section in the message."""
    from tools.agent.vision import build_user_message

    empty_submit = tmp_path / "submit"
    empty_submit.mkdir()
    feedback: dict[str, Any] = {
        "moves": [
            {
                "element_id": "control.action_a",
                "expected_position": [118, 70],
                "new_position": [114, 70],
            }
        ]
    }
    msg = build_user_message(case_path.read_text(), feedback, empty_submit)
    assert "## Student prompt" not in msg


def test_build_user_message_includes_previous_fatal_constraint_feedback(
    case_path: Path, tmp_path: Path
) -> None:
    from tools.agent.vision import build_user_message

    submit = tmp_path / "submit"
    submit.mkdir()
    violations = [
        {
            "severity": "error",
            "category": "print",
            "rule": "keepout_overlap",
            "message": "control keep-outs overlap",
        }
    ]

    message = build_user_message(case_path.read_text(), {}, submit, violations=violations)

    assert "## Previous candidate rejected by source or feasibility checks" in message
    assert '"rule": "keepout_overlap"' in message
    assert "control keep-outs overlap" in message


def test_build_user_message_prompt_section_is_whitespace_safe(
    case_path: Path, tmp_path: Path
) -> None:
    """Whitespace-only prompt doesn't get a stray section."""
    from tools.agent.vision import build_user_message

    empty_submit = tmp_path / "submit"
    empty_submit.mkdir()
    feedback: dict[str, Any] = {"prompt": "   \n  \t  ", "moves": []}
    msg = build_user_message(case_path.read_text(), feedback, empty_submit)
    assert "## Student prompt" not in msg


def test_system_prompt_mentions_cadkit_primitives() -> None:
    from tools.agent.vision import VISION_SYSTEM_PROMPT

    # The system prompt must reference the primitives so the agent
    # doesn't introduce raw build123d code.
    assert "PBS/GUUZI control mounts" in VISION_SYSTEM_PROMPT
    assert "gamepad_body" in VISION_SYSTEM_PROMPT
    assert "exactly four PBS directional buttons plus two GUUZI action" in VISION_SYSTEM_PROMPT
    assert "CONTROLS" in VISION_SYSTEM_PROMPT
    assert "control.action_a" in VISION_SYSTEM_PROMPT
    assert "control.action_b" in VISION_SYSTEM_PROMPT
    assert "dpad_cutout" not in VISION_SYSTEM_PROMPT
    assert "tapered / sloped / narrower at the top" in VISION_SYSTEM_PROMPT
    assert "shorter / narrower / thinner proportions" in VISION_SYSTEM_PROMPT
    assert "logo / emblem / icon" in VISION_SYSTEM_PROMPT
    assert "top-panel effects are alternatives" in VISION_SYSTEM_PROMPT
    assert "melt_depth_mm: use 1.5" in VISION_SYSTEM_PROMPT
    assert "Never reference or import a URL or file path" in VISION_SYSTEM_PROMPT


def test_system_prompt_describes_validated_canonical_scene_feedback():
    from tools.agent.vision import VISION_SYSTEM_PROMPT

    assert "strict JSON" in VISION_SYSTEM_PROMPT
    assert "assembled GLB" in VISION_SYSTEM_PROMPT
    assert "unannotated" in VISION_SYSTEM_PROMPT
    assert "receiving, coupon, printer," in VISION_SYSTEM_PROMPT
    assert "protected routes are immovable" in VISION_SYSTEM_PROMPT.lower()
    assert "D-pad" not in VISION_SYSTEM_PROMPT
    assert "adjust the" not in VISION_SYSTEM_PROMPT
    assert "base_revision" in VISION_SYSTEM_PROMPT
    assert '"control_moves"' in VISION_SYSTEM_PROMPT
    assert '"exterior_design": null' in VISION_SYSTEM_PROMPT
    assert "CONTROLS" in VISION_SYSTEM_PROMPT
    assert "Python" in VISION_SYSTEM_PROMPT
    assert "authoritative snap-fit" in VISION_SYSTEM_PROMPT
    assert "must not modify" in VISION_SYSTEM_PROMPT
    assert "45-degree gussets" in VISION_SYSTEM_PROMPT


def test_case_edit_parser_rejects_fenced_python():
    from tools.agent.case_edits import CaseEditError, parse_case_edit

    with pytest.raises(CaseEditError, match="strict JSON"):
        parse_case_edit("```python\ncase = object()\n```")


def test_case_edit_parser_rejects_prose_preamble():
    from tools.agent.case_edits import CaseEditError, parse_case_edit

    with pytest.raises(CaseEditError, match="strict JSON"):
        parse_case_edit('Here you go: {"schema": "cadkit.case-edit"}')


def test_case_edit_parser_rejects_clean_python_source():
    from tools.agent.case_edits import CaseEditError, parse_case_edit

    with pytest.raises(CaseEditError, match="strict JSON"):
        parse_case_edit("from build123d import BuildPart\ncase = 1\n")


def _typed_response(case_path: Path, target: list[float]) -> str:
    from tools.editor import server

    part, scene = server.__dict__["_scene_for_case"](case_path)
    assert scene is not None
    revision = server.__dict__["_editor_revision"](case_path, scene, part)
    return json.dumps(
        {
            "schema": "cadkit.case-edit",
            "version": "1.0",
            "base_revision": revision,
            "control_moves": [{"id": "control.action_a", "target": target}],
            "exterior_design": None,
        }
    )


def test_regenerate_case_writes_output(
    monkeypatch: MonkeyPatch, case_path: Path, submit_dir: Path
) -> None:
    """The full pipeline, with the opencode subprocess mocked."""
    from tools.agent import config, vision

    selected: dict[str, str] = {}
    response = _typed_response(case_path, [104.0, 30.0])

    def fake_invoke(_prompt: str, _image_path: Path, *, model: str) -> str:
        selected["model"] = model
        return response

    monkeypatch.setattr(vision, "invoke_with_image", fake_invoke)
    out = vision.regenerate_case(case_path, submit_dir)

    # In-place edit (default output_path).
    assert out == case_path
    assert '"x": 104.0' in case_path.read_text()
    assert selected["model"] == config.DEFAULT_MODEL


def test_regenerate_case_to_separate_output(
    case_path: Path, submit_dir: Path, tmp_path: Path
) -> None:
    from tools.agent import vision

    out_path = tmp_path / "case_v2.py"
    response = _typed_response(case_path, [104.0, 30.0])

    with mock.patch.object(vision, "invoke_with_image", return_value=response):
        vision.regenerate_case(case_path, submit_dir, output_path=out_path)

    # Original untouched.
    assert '"x": 105.0' in case_path.read_text()
    assert '"x": 104.0' in out_path.read_text()


def test_regenerate_case_raises_on_missing_feedback(case_path: Path, tmp_path: Path) -> None:
    from tools.agent import vision

    empty_submit = tmp_path / "no_feedback"
    empty_submit.mkdir()
    with pytest.raises(FileNotFoundError, match=r"feedback\.json"):
        vision.regenerate_case(case_path, empty_submit)


def test_regenerate_case_raises_on_missing_snapshot(case_path: Path, tmp_path: Path) -> None:
    from tools.agent import vision

    no_snap = tmp_path / "no_snap"
    no_snap.mkdir()
    (no_snap / "feedback.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match=r"snapshot\.png"):
        vision.regenerate_case(case_path, no_snap)


def test_invoke_with_image_raises_when_opencode_missing(
    monkeypatch: MonkeyPatch, submit_dir: Path
) -> None:
    """If the opencode CLI is not on PATH, surface a clear error."""
    from tools.agent import vision

    def missing_executable(_name: str) -> None:
        return None

    monkeypatch.setattr(shutil, "which", missing_executable)
    with pytest.raises(RuntimeError, match="opencode CLI not on PATH"):
        vision.invoke_with_image("test prompt", submit_dir / "snapshot.png")


def test_invoke_with_image_calls_subprocess(
    monkeypatch: MonkeyPatch, submit_dir: Path, tmp_path: Path
) -> None:
    """Smoke test the subprocess call shape (no real opencode)."""
    from cadkit._bounded_subprocess import BoundedProcessResult

    from tools.agent import config, vision
    from tools.agent.case_edits import parse_case_edit

    proposal = json.dumps(
        {
            "schema": "cadkit.case-edit",
            "version": "1.0",
            "base_revision": "0" * 64,
            "control_moves": [],
            "exterior_design": None,
        },
        separators=(",", ":"),
    )
    stdout = "\n".join(
        (
            json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
            json.dumps({"type": "text", "part": {"type": "text", "text": proposal}}),
            json.dumps({"type": "step_finish", "part": {"type": "step-finish"}}),
        )
    ).encode()
    fake_result = BoundedProcessResult(returncode=0, stdout=stdout, stderr=b"")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> BoundedProcessResult:
        calls.append((cmd, kwargs))
        return fake_result

    executable = tmp_path / "opencode"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)

    def locate_executable(_name: str) -> str:
        return str(executable)

    monkeypatch.setattr(shutil, "which", locate_executable)
    monkeypatch.setattr(vision, "run_bounded", fake_run)
    out = vision.invoke_with_image("hello", submit_dir / "snapshot.png")
    assert out == proposal
    assert parse_case_edit(out).base_revision == "0" * 64
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[0] == str(executable)
    assert cmd[1] == "run"
    assert "--model" in cmd
    assert config.DEFAULT_MODEL in cmd
    assert "--agent" in cmd
    assert "cad-vision" in cmd
    assert "--pure" in cmd
    assert cmd[cmd.index("--format") + 1] == "json"
    assert "--file" in cmd
    assert str(submit_dir / "snapshot.png") in cmd
    # Prompt goes as a positional argument (the CLI dropped --prompt).
    # IMPORTANT: positional must come BEFORE --file, otherwise --file
    # greedily consumes the prompt as a second file path.
    assert "hello" in cmd
    msg_idx = cmd.index("hello")
    file_idx = cmd.index(str(submit_dir / "snapshot.png"))
    assert msg_idx < file_idx
    assert kwargs["cwd"] == submit_dir


def test_opencode_event_extraction_accepts_one_completed_text_without_finish_event() -> None:
    from tools.agent import vision
    from tools.agent.case_edits import parse_case_edit

    proposal = json.dumps(
        {
            "schema": "cadkit.case-edit",
            "version": "1.0",
            "base_revision": "0" * 64,
            "control_moves": [],
            "exterior_design": None,
        }
    )
    stdout = "\n".join(
        (
            json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
            json.dumps({"type": "text", "part": {"type": "text", "text": proposal}}),
        )
    ).encode()

    extracted = vision._proposal_from_opencode_events(  # pyright: ignore[reportPrivateUsage]
        stdout
    )

    assert extracted == proposal
    assert parse_case_edit(extracted).base_revision == "0" * 64


@pytest.mark.parametrize(
    "events",
    [
        [
            {"type": "step_start", "part": {"type": "step-start"}},
            {"type": "text", "part": {"type": "text", "text": "{}"}},
            {"type": "text", "part": {"type": "text", "text": "{}"}},
            {"type": "step_finish", "part": {"type": "step-finish"}},
        ],
        [
            {"type": "step_start", "part": {"type": "step-start"}},
            {"type": "tool_use", "part": {"type": "tool", "output": "{}"}},
            {"type": "text", "part": {"type": "text", "text": "{}"}},
            {"type": "step_finish", "part": {"type": "step-finish"}},
        ],
        [
            {"type": "step_start", "part": {"type": "step-start"}},
            [],
            {"type": "step_finish", "part": {"type": "step-finish"}},
        ],
    ],
)
def test_invoke_with_image_rejects_ambiguous_opencode_events(
    monkeypatch: MonkeyPatch,
    submit_dir: Path,
    tmp_path: Path,
    events: list[Any],
) -> None:
    from cadkit._bounded_subprocess import BoundedProcessResult

    from tools.agent import vision

    executable = tmp_path / "opencode"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)

    def locate_executable(_name: str) -> str:
        return str(executable)

    def fake_run(_cmd: list[str], **_kwargs: Any) -> BoundedProcessResult:
        return BoundedProcessResult(
            returncode=0,
            stdout="\n".join(json.dumps(event) for event in events).encode(),
            stderr=b"",
        )

    monkeypatch.setattr(shutil, "which", locate_executable)
    monkeypatch.setattr(vision, "run_bounded", fake_run)

    with pytest.raises(RuntimeError, match="unambiguous proposal"):
        vision.invoke_with_image("hello", submit_dir / "snapshot.png")


def test_cad_vision_agent_bounds_generation_and_denies_all_tools() -> None:
    agent_path = Path(__file__).resolve().parents[1] / ".opencode" / "agents" / "cad-vision.md"
    source = agent_path.read_text()

    # The typed proposal is one response; trusted code owns every source edit.
    assert "steps: 1" in source
    # A wildcard is required because --pure disables external plugins only;
    # inherited MCP and future custom tools otherwise remain allowed.
    assert "permission: deny" in source
    assert "strict `cadkit.case-edit/1.0` JSON" in source


def test_vision_cli_uses_the_project_model(
    monkeypatch: MonkeyPatch, case_path: Path, submit_dir: Path
) -> None:
    from tools.agent import config, vision

    selected: dict[str, str] = {}

    def fake_regenerate_case(
        _case_path: Path,
        _submit_dir: Path,
        _output_path: Path | None = None,
        *,
        model: str,
    ) -> Path:
        selected["model"] = model
        return case_path

    monkeypatch.setattr(vision, "regenerate_case", fake_regenerate_case)
    monkeypatch.setattr(
        sys,
        "argv",
        ["vision", "--case-path", str(case_path), "--submit-dir", str(submit_dir)],
    )

    assert vision.main() == 0
    assert selected["model"] == config.DEFAULT_MODEL
