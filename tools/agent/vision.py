"""Bounded visual-agent proposal loop for MIG-04 typed case edits.

The model receives a rendered scene and returns typed JSON.  It never receives
an executable case source and it never writes one.  Trusted code adapts the
validated proposal to the existing editor move validator, then rewrites only
literal control coordinates in a private candidate source.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, cast

# Ensure cadkit is on the path (same bootstrap as tools/editor/server.py).
_SESSION_DIR = Path(__file__).resolve().parents[2]
if str(_SESSION_DIR / "tools") not in sys.path:
    sys.path.insert(0, str(_SESSION_DIR / "tools"))

from cadkit._bounded_subprocess import (  # type: ignore[import-not-found]  # noqa: E402
    BoundedOutputLimitExceeded,
    BoundedProcessTimeout,
    run_bounded,
)
from cadkit.exterior_design import EXTERIOR_PROFILES, ExteriorDesignSpec  # noqa: E402

from .case_edits import (  # noqa: E402
    CANONICAL_CONTROL_IDS,
    CaseEditError,
    proposal_move_records,
    validate_case_edit,
)
from .config import DEFAULT_MODEL  # noqa: E402

CAD_VISION_AGENT = "cad-vision"
_OPENCODE_EVENT_TYPES = ("step_start", "text", "step_finish")
_OPENCODE_PART_TYPES = ("step-start", "text", "step-finish")

VISION_SYSTEM_PROMPT = """\
You are a CAD engineer proposing one or more safe typed edits for a Build123d
game-controller case. You do not write code. You do not call tools. You return
one strict JSON object and nothing else.

The student sent you:
  1. The canonical scene's unannotated base PNG for visual context only.
  2. A JSON context containing the current canonical scene, its base_revision,
     exact case-local millimetre positions, annotations, and text.

The canonical scene is authoritative for named nodes, transforms, layers,
component identity, protected routes, and fixed electronics. The active
controls are exactly four PBS directional buttons plus two GUUZI action
buttons. Their only allowed IDs, in canonical order, are:
  control.up, control.down, control.right, control.left,
  control.action_a, control.action_b

Feedback annotations retain their public `view_mode` and screen-space fields;
they are visual context only, never 3D anchors or exact control targets.

The trusted CAD implementation already owns the PBS/GUUZI control mounts,
gamepad_body, and literal CONTROLS data. It preserves the complete assembled GLB
scene; protected routes are immovable. The authoritative snap-fit interface is
protected: you must not modify it; its fixed shell-wall attachment and 45-degree gussets
remain trusted geometry. Do not reproduce any source in your response.

Return exactly this shape:
{
  "schema": "cadkit.case-edit",
  "version": "1.0",
  "base_revision": "the supplied 64-character lowercase SHA-256 revision",
  "control_moves": [{"id": "control.action_a", "target": [x, y]}],
  "exterior_design": null
}

Each control ID may occur at most once. Targets are exact case-local XY [x, y]
millimetre numbers. Use the supplied revision verbatim. Do not return source,
Markdown, Python, Build123d, raw CSG, arbitrary free points, meshes, URLs, file
paths, component substitutions, direct Z changes, wall changes, split changes,
snap changes, USB changes, electronics changes, route changes, or changes to fixed nodes. Do not guess
screen-space annotation coordinates as 3D positions. If the request cannot be
represented by a control or exterior edit, return an empty control_moves array,
null exterior_design, and a concise unsupported_reason; trusted code will report
that it is not applicable.

The optional exterior_design is a complete bounded XY silhouette object. Its
exact schema is {
  profile: rounded|snes_inspired|n64_inspired|playstation_inspired|xbox_inspired|switch_inspired|custom,
  roundness_mm: 0..16,
  shoulder_inset_mm: 0..16,
  side_grip_length_mm: 0..35,
  side_grip_splay_deg: 0..30,
  center_grip_length_mm: null or 0..35,
  melt_depth_mm: 0..1.5,
  engraved_text: null or a printable string of at most 24 characters,
  groove_count: integer 0..8,
  groove_depth_mm: 0..1.5,
  chamfer_mm: 0..5,
  taper_deg: 0..30,
  length_proportion: 1, or 0.95..0.98,
  width_proportion: 1, or 0.95..0.98,
  thickness_proportion: 1, or 0.97..0.975,
  emblem_svg: null or one inline path-only SVG up to 8 KiB,
  emblem_size_mm: 0, or 4..30 when emblem_svg is present
}. Named profiles must leave all custom parameters at their defaults; custom
requires at least one non-default parameter and a positive side-grip length when
splay is non-zero. Proportion edits require thickness_proportion below 1 plus at
least one of length_proportion or width_proportion below 1; do not combine them
with taper_deg. Melt, engraving, grooves, taper/proportions, and emblem
top-panel effects are alternatives; when a student requests a new one, clear
the prior conflicting effect instead of carrying it forward. For qualitative
requests without a numeric intensity, choose a clearly reviewable bounded
value: melt_depth_mm: use 1.5; groove_depth_mm: use at least 1.0; chamfer_mm:
use at least 3.0; taper_deg: use at least 18.0; emblem_size_mm: use at least
12.0. emblem_svg must be literal inline SVG in the exact form
<svg viewBox="0 0 100 100"><path d="closed path data ending Z"/></svg>. It may
contain one to eight closed path elements and no links, files, scripts, styles,
events, external references, nested elements, or other tags. Only the XY
silhouette and bounded, subtractive top-wall decorations may change. The 2 mm wall, fixed
split, snap roots, six control openings and ligaments, shared USB opening and
ligament, adhesive/backbone floor, electronics keep-outs, protected routes, and
no-pinch regions remain unchanged. Never claim production, receiving, coupon,
printer, slice, or first-article approval.
No production, receiving, coupon, printer, slice, or first-article gate is
cleared by this software-only proposal.

## Translating requests

Map the student's plain-language exterior requests to the bounded parameters:
- "melted / sagged / drooped / soft" → melt_depth_mm (a shallow concave top dish).
- "engraved text / label / name / logo text" → engraved_text (short centred text).
- "wings / fins / flared sides" → side_grip_length_mm + side_grip_splay_deg.
- "grips / handles" → side_grip_length_mm (and center_grip_length_mm for a thumb rest).
- "rounder / softer corners" → roundness_mm.
- "pinched waist / slim middle" → shoulder_inset_mm.
- "ridges / ribs / grooves / textured" → groove_count + groove_depth_mm.
- "crisper / beveled / chamfered edges" → chamfer_mm.
- "tapered / sloped / narrower at the top" → taper_deg (a drafted upper-panel loft).
- "shorter / narrower / thinner proportions" → length_proportion and/or
  width_proportion plus thickness_proportion below 1. These alter only the
  lofted upper-panel relief, never the fixed 130 × 90 × 65 mm frame.
- "logo / emblem / icon" → a simple centred closed-path emblem_svg plus
  emblem_size_mm. Never reference or import a URL or file path.
- "SNES / N64 / PlayStation / Xbox / Switch shape" → the named profile.
If a request cannot be expressed by these, return an empty control_moves array,
null exterior_design, and a concise unsupported_reason.

## Translating moves

Trusted code converts accepted control moves into the existing editor records
with `element_id`, `expected_position`, and `new_position`. Do not invent or
return those records; return only the typed `id` and `target` proposal fields.
"""


def _scene_payload(scene: Any | None) -> Any:
    if scene is None:
        return None
    to_dict = getattr(scene, "to_dict", None)
    if not callable(to_dict):
        raise CaseEditError("malformed_scene", "canonical scene could not be serialized.")
    return to_dict()


def build_user_message(
    case_source: str,
    feedback: dict[str, Any],
    submit_dir: Path,
    *,
    scene: Any | None = None,
    revision: str | None = None,
    editor_moves: list[dict[str, Any]] | None = None,
    violations: list[dict[str, Any]] | None = None,
    current_exterior: ExteriorDesignSpec | None = None,
) -> str:
    """Build model input without exposing executable case source."""
    if current_exterior is None:
        try:
            from cadkit.case_source import inspect_exterior_design_source

            current_exterior = inspect_exterior_design_source(case_source)
        except (SyntaxError, ValueError):
            current_exterior = None
    snap_path = submit_dir / "snapshot.png"
    parts: list[str] = []
    parts.append(
        "## Current canonical scene for case.py\n"
        + json.dumps(
            {
                "base_revision": revision,
                "allowed_control_ids": list(CANONICAL_CONTROL_IDS),
                "allowed_exterior_profiles": list(EXTERIOR_PROFILES),
                "exterior_design": (
                    current_exterior.to_dict() if current_exterior is not None else None
                ),
                "exterior_parameter_schema": {
                    "roundness_mm": [0.0, 16.0],
                    "shoulder_inset_mm": [0.0, 16.0],
                    "side_grip_length_mm": [0.0, 35.0],
                    "side_grip_splay_deg": [0.0, 30.0],
                    "center_grip_length_mm": [0.0, 35.0],
                    "melt_depth_mm": [0.0, 1.5],
                    "engraved_text": "null or printable string up to 24 characters",
                    "groove_count": [0, 8],
                    "groove_depth_mm": [0.0, 1.5],
                    "chamfer_mm": [0.0, 5.0],
                    "taper_deg": [0.0, 30.0],
                    "length_proportion": "1, or 0.95..0.98",
                    "width_proportion": "1, or 0.95..0.98",
                    "thickness_proportion": "1, or 0.97..0.975",
                    "emblem_svg": "null or path-only inline SVG up to 8 KiB",
                    "emblem_size_mm": "0, or 4..30 when emblem_svg is present",
                },
                "scene": _scene_payload(scene),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    prompt = (feedback.get("prompt") or "").strip()
    if prompt:
        parts.append("## Student prompt\n" + prompt + "\n")
    if editor_moves:
        parts.append(
            "## Explicit editor moves (already applied first)\n"
            + json.dumps(editor_moves, sort_keys=True, indent=2)
            + "\n"
        )
    if violations:
        parts.append(
            "## Previous candidate rejected by source or feasibility checks\n"
            + json.dumps(violations, indent=2)
            + "\nCorrect every listed error without weakening or bypassing validation.\n"
        )
    parts.append("## Feedback JSON\n```json\n" + json.dumps(feedback, indent=2) + "\n```\n")
    if snap_path.exists():
        parts.append(
            "## Canonical scene base render\n"
            f"See the attached PNG: `{snap_path}`. It is intentionally unannotated; "
            "screen-space marks are only context and exact moves come from scene data.\n"
        )
    parts.append(
        "## Instructions\n"
        "Return only the strict cadkit.case-edit/1.0 JSON proposal. Never return "
        "case.py, Python, Markdown, prose, or raw CAD.\n"
    )
    return "\n".join(parts)


def invoke_with_image(
    prompt: str,
    image_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    timeout: int = 600,
) -> str:
    """Invoke the no-tools ``cad-vision`` profile with a PNG attachment."""
    opencode = _resolve_executable("opencode", "opencode CLI")
    if opencode is None:
        raise RuntimeError("opencode CLI not on PATH.")
    cmd = [
        str(opencode),
        "run",
        "--format",
        "json",
        "--pure",
        "--agent",
        CAD_VISION_AGENT,
        "--model",
        model,
        prompt,
        "--file",
        str(image_path),
    ]
    try:
        result = run_bounded(
            cmd,
            cwd=image_path.parent,
            timeout_seconds=timeout,
            stdout_limit=4 * 1024 * 1024,
            stderr_limit=64 * 1024,
        )
    except BoundedProcessTimeout as exc:
        raise RuntimeError(f"opencode CLI timed out after {timeout}s: {exc}") from exc
    except BoundedOutputLimitExceeded as exc:
        raise RuntimeError(f"opencode CLI exceeded a bounded output: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"opencode CLI failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).decode('utf-8', errors='replace').strip()[:400]}"
        )
    return _proposal_from_opencode_events(result.stdout)


def _proposal_from_opencode_events(stdout: bytes) -> str:
    """Extract one model response from OpenCode's bounded JSON event envelope."""
    try:
        lines = stdout.decode("utf-8").splitlines()
        events = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("opencode CLI did not return an unambiguous proposal envelope.") from exc
    if any(not isinstance(event, dict) for event in events):
        raise RuntimeError("opencode CLI did not return an unambiguous proposal envelope.")
    event_types = tuple(event.get("type") for event in events)
    # Some OpenCode releases can exit successfully after the completed text
    # event races the final accounting event.  The optional finish remains
    # unambiguous because every other shape, including extra text, is rejected.
    if event_types not in (_OPENCODE_EVENT_TYPES, _OPENCODE_EVENT_TYPES[:-1]):
        raise RuntimeError("opencode CLI did not return an unambiguous proposal envelope.")
    parts = [event.get("part") for event in events]
    if (
        any(not isinstance(part, dict) for part in parts)
        or tuple(part.get("type") for part in parts) != _OPENCODE_PART_TYPES[: len(parts)]
    ):
        raise RuntimeError("opencode CLI did not return an unambiguous proposal envelope.")
    proposal = parts[1].get("text")
    if not isinstance(proposal, str):
        raise RuntimeError("opencode CLI did not return an unambiguous proposal envelope.")
    return proposal


def _resolve_executable(name: str, label: str) -> Path | None:
    """Resolve a security-sensitive executable before subprocess use."""
    resolved = shutil.which(name)
    if resolved is None:
        return None
    executable = Path(resolved).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f"{label} resolved to a non-executable path: {executable}")
    return executable


def _load_scene_context(case_path: Path) -> tuple[Any, Any, str]:
    from editor.server import _editor_revision, _scene_for_case  # type: ignore[import-not-found]

    try:
        part, scene = _scene_for_case(case_path)
        if scene is None:
            raise ValueError("typed case edits require a canonical ASSEMBLY_SPEC scene")
        return part, scene, _editor_revision(case_path, scene, part)
    except CaseEditError:
        raise
    except Exception as exc:
        raise CaseEditError("malformed_scene", f"failed to load canonical scene: {exc}") from exc


def _apply_editor_moves(scene: Any, feedback: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    from editor.server import SceneMoveError, apply_scene_moves  # type: ignore[import-not-found]

    try:
        return apply_scene_moves(scene, feedback.get("moves", []))
    except SceneMoveError as exc:
        raise CaseEditError(
            "editor_move_validation",
            f"explicit editor move {exc.index + 1} failed validation: {exc}",
            details={"move_index": exc.index, "violations": exc.result.get("violations", [])},
        ) from exc


def _current_positions(scene: Any) -> dict[str, tuple[float, float]]:
    return {
        node.id: (float(node.transform.position[0]), float(node.transform.position[1]))
        for node in scene.nodes
        if node.id in CANONICAL_CONTROL_IDS
    }


def _rewrite_targets(scene: Any, moves: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    targets: dict[str, tuple[float, float]] = {}
    for move in moves:
        element_id = move["element_id"]
        position = scene.node(element_id).transform.position[:2]
        targets[element_id] = (float(position[0]), float(position[1]))
    return targets


def _apply_llm_proposal(
    scene: Any,
    response: str,
    *,
    revision: str,
    current_exterior: ExteriorDesignSpec | None = None,
) -> tuple[Any, list[dict[str, Any]], ExteriorDesignSpec | None]:
    from editor.server import SceneMoveError, apply_scene_moves  # type: ignore[import-not-found]

    proposal = validate_case_edit(
        response,
        current_revision=revision,
        current_positions=_current_positions(scene),
        current_exterior=current_exterior,
    )
    final_scene = scene
    accepted: list[dict[str, Any]] = []
    try:
        records = proposal_move_records(proposal, _current_positions(scene))
        final_scene, accepted = apply_scene_moves(scene, records)
        unchanged = [
            move["element_id"]
            for move in accepted
            if final_scene.node(move["element_id"]).transform.position[:2]
            == scene.node(move["element_id"]).transform.position[:2]
        ]
        if unchanged:
            raise CaseEditError(
                "no_op_proposal",
                "proposal control target(s) round to their current position: "
                + ", ".join(unchanged),
            )
        return final_scene, accepted, proposal.exterior_design
    except SceneMoveError as exc:
        raise CaseEditError(
            "llm_move_validation",
            f"LLM control move {exc.index + 1} failed validation: {exc}",
            details={"move_index": exc.index, "violations": exc.result.get("violations", [])},
        ) from exc


def _load_feedback(path: Path) -> dict[str, Any]:
    try:
        feedback = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseEditError(
            "malformed_feedback", f"feedback.json is not valid JSON: {exc}"
        ) from exc
    if not isinstance(feedback, dict):
        raise CaseEditError("malformed_feedback", "feedback.json must contain an object.")
    return cast(dict[str, Any], feedback)


def regenerate_case(
    case_path: Path,
    submit_dir: Path,
    output_path: Path | None = None,
    *,
    model: str = DEFAULT_MODEL,
    violations: list[dict[str, Any]] | None = None,
    scene: Any | None = None,
    base_revision: str | None = None,
    editor_moves_applied: bool = False,
) -> Path:
    """Turn one visual response into a private deterministic source candidate.

    ``scene`` is the scene after explicit editor moves when supplied by the
    server.  Direct callers may omit it; in that case the canonical scene and
    editor moves are loaded and applied before the LLM proposal.
    """
    feedback_path = submit_dir / "feedback.json"
    snap_path = submit_dir / "snapshot.png"
    if not feedback_path.exists():
        raise FileNotFoundError(f"missing {feedback_path}")
    if not snap_path.exists():
        raise FileNotFoundError(f"missing {snap_path}")
    feedback = _load_feedback(feedback_path)

    if scene is None or base_revision is None:
        _part, loaded_scene, loaded_revision = _load_scene_context(case_path)
        current_scene = scene if scene is not None else loaded_scene
        revision = base_revision if base_revision is not None else loaded_revision
    else:
        current_scene = scene
        revision = base_revision
    editor_accepted: list[dict[str, Any]] = []
    source = case_path.read_text(encoding="utf-8")
    if not editor_moves_applied:
        current_scene, editor_accepted = _apply_editor_moves(current_scene, feedback)
        if editor_accepted:
            from cadkit.case_source import rewrite_controls_source

            source = rewrite_controls_source(
                source, _rewrite_targets(current_scene, editor_accepted)
            )

    current_exterior = _current_exterior(source)
    user_msg = build_user_message(
        source,
        feedback,
        submit_dir,
        scene=current_scene,
        revision=revision,
        editor_moves=editor_accepted,
        violations=violations,
        current_exterior=current_exterior,
    )
    response = invoke_with_image(
        VISION_SYSTEM_PROMPT + "\n\n" + user_msg,
        snap_path,
        model=model,
    )
    final_scene, llm_accepted, exterior_design = _apply_llm_proposal(
        current_scene,
        response,
        revision=revision,
        current_exterior=current_exterior,
    )
    from cadkit.case_source import rewrite_controls_source, rewrite_exterior_design_source

    candidate_source = source
    if llm_accepted:
        candidate_source = rewrite_controls_source(
            source, _rewrite_targets(final_scene, llm_accepted)
        )
    if exterior_design is not None:
        candidate_source = rewrite_exterior_design_source(candidate_source, exterior_design)
    target = output_path or case_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(candidate_source, encoding="utf-8")
    return target


def _current_exterior(source: str) -> ExteriorDesignSpec:
    from cadkit.case_source import inspect_exterior_design_source

    try:
        return inspect_exterior_design_source(source)
    except ValueError as exc:
        raise CaseEditError(
            "malformed_source", f"current exterior design is unavailable: {exc}"
        ) from exc


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Propose a typed case edit from visual feedback.")
    parser.add_argument("--case-path", type=Path, required=True)
    parser.add_argument("--submit-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    try:
        output = regenerate_case(args.case_path, args.submit_dir, args.output, model=args.model)
    except Exception as exc:
        print(f"vision.regenerate_case failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(str(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
