"""Bounded typed-proposal loop used by the local case editor.

OpenCode owns provider credentials. This module receives only prompts, images,
and the project-selected model identifier; it never accepts or stores API keys.
"""

from __future__ import annotations

import ast
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from cadkit.case_execution import require_sandbox

from .case_edits import CaseEditError
from .config import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MODEL,
)

if TYPE_CHECKING:
    pass


def iterate_with_visual_feedback(
    case_path: Path,
    submit_dir: Path,
    *,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    output_path: Path | None = None,
    base_scene: Any | None = None,
    base_revision: str | None = None,
    editor_moves_applied: bool = False,
) -> dict[str, Any]:
    """Orchestrator: take a /submit queue entry, regenerate the case.py,
    validate, re-render, return the result.

    Args:
        case_path: the current case.py (the agent edits this in place).
        submit_dir: contains feedback.json + snapshot.png from /submit.
        model: the LLM model name.
        max_iterations: bounded attempts for source and feasibility corrections.

    Returns:
        dict with: case_path, submit_dir, model, status, violations,
        iterations.
    """
    require_sandbox()
    from cadkit.constraints import (  # pyright: ignore[reportUnknownVariableType]
        has_blocking_prototype_violations,
        validate,
    )

    validate_part = cast(Callable[..., list[Any]], validate)

    from . import vision

    result: dict[str, Any] = {
        "case_path": str(case_path),
        "submit_dir": str(submit_dir),
        "model": model,
        "iterations": 0,
        "status": "starting",
    }

    working_path = Path(case_path)
    target_path = Path(output_path) if output_path is not None else working_path
    # Preserve the legacy lazy boundary when callers invoke the orchestrator
    # without a complete submission directory; regenerate_case still reports
    # the missing-file error at the normal generation boundary.
    context = (
        (None, None)
        if _missing_submission_file(submit_dir) is not None
        else _iteration_context(working_path, base_scene, base_revision)
    )
    if isinstance(context, dict):
        result.update(context)
        return result
    context_scene, context_revision = context

    typed_baseline = working_path.read_bytes()
    accepted_base_source = typed_baseline
    baseline_part: Any | None = None
    baseline_placements: Any | None = None
    baseline_violations: list[Any] | None = None
    if context_scene is not None:
        try:
            from cadkit.case_execution import execute_case_artifacts

            baseline_artifacts = execute_case_artifacts(working_path)
            baseline_part = baseline_artifacts.part
            baseline_placements = baseline_artifacts.placements
            baseline_violations = validate_part(
                baseline_part,
                placements=baseline_placements,
                require_export_verification=False,
                whole_case_geometry=True,
            )
        except RuntimeError as exc:
            return {
                "status": "vision_error",
                "error": f"failed to load accepted base candidate: {type(exc).__name__}: {exc}",
            }
    previous_violations: list[dict[str, Any]] | None = None
    for iteration in range(1, max_iterations + 1):
        result["iterations"] = iteration
        if iteration > 1 and working_path.read_bytes() != accepted_base_source:
            working_path.write_bytes(accepted_base_source)
        attempt = _run_iteration(
            vision,
            working_path,
            submit_dir,
            target_path,
            model,
            previous_violations,
            context_scene,
            context_revision,
            editor_moves_applied,
            typed_baseline,
            iteration,
            max_iterations,
            validate_part,
            has_blocking_prototype_violations,
            baseline_part,
            baseline_placements,
            baseline_violations,
        )
        result.update(attempt)
        if attempt["status"] != "retry":
            return result
        previous_violations = cast(list[dict[str, Any]], attempt["violations"])
        accepted_base_source = cast(bytes, attempt["accepted_base_source"])
        result.pop("accepted_base_source", None)
        working_path = Path(cast(str, attempt["working_path"]))

    result["status"] = "max_iterations_exhausted"
    return result


def _missing_submission_file(submit_dir: Path) -> Path | None:
    for name in ("feedback.json", "snapshot.png"):
        path = submit_dir / name
        if not path.exists():
            return path
    return None


def _run_iteration(
    vision: Any,
    working_path: Path,
    submit_dir: Path,
    target_path: Path,
    model: str,
    previous_violations: list[dict[str, Any]] | None,
    context_scene: Any | None,
    context_revision: str | None,
    editor_moves_applied: bool,
    typed_baseline: bytes,
    iteration: int,
    max_iterations: int,
    validate_part: Callable[..., list[Any]],
    has_blocking_prototype_violations: Callable[..., bool],
    baseline_part: Any | None,
    baseline_placements: Any | None,
    baseline_violations: list[Any] | None,
) -> dict[str, Any]:
    baseline_source = working_path.read_bytes()
    generated = _regenerate_attempt(
        vision,
        working_path,
        submit_dir,
        target_path,
        model,
        previous_violations,
        context_scene,
        context_revision,
        editor_moves_applied,
        typed_baseline,
        iteration,
        max_iterations,
    )
    if isinstance(generated, dict):
        if generated.get("status") == "retry":
            generated.setdefault("accepted_base_source", baseline_source)
        return generated
    try:
        violations, moved_baseline_violations = _visual_candidate_violations(
            generated,
            validate_part,
            baseline_part=baseline_part,
            baseline_placements=baseline_placements,
            base_scene=context_scene,
            baseline_source=baseline_source,
        )
    except RuntimeError as exc:
        return {"status": "exec_error", "error": f"{type(exc).__name__}: {exc}"}
    serialized = [v.to_dict() for v in violations]
    comparison_violations = (
        moved_baseline_violations if moved_baseline_violations is not None else baseline_violations
    )
    if comparison_violations is None:
        blocking = has_blocking_prototype_violations(violations)
    else:
        blocking = _has_blocking_candidate_violations(violations, comparison_violations)
    if not blocking:
        return {
            "status": "ok",
            "case_path": str(generated),
            "violations": serialized,
            "llm_accepted_moves": _candidate_control_moves(generated, context_scene),
        }
    next_path = _retry_baseline(
        working_path,
        generated,
        violations,
        baseline_source=baseline_source,
    )
    if iteration < max_iterations:
        return {
            "status": "retry",
            "violations": serialized,
            "working_path": str(next_path),
            "accepted_base_source": baseline_source,
        }
    if target_path.resolve() == next_path.resolve():
        next_path.write_bytes(typed_baseline)
    return {"status": "fatal_after_iterations", "violations": serialized}


def _iteration_context(
    case_path: Path, base_scene: Any | None, base_revision: str | None
) -> tuple[Any | None, str | None] | dict[str, Any]:
    if base_scene is not None and base_revision is not None:
        return base_scene, base_revision
    try:
        from editor import server as editor_server  # type: ignore[import-not-found]

        context_part, loaded_scene = editor_server.__dict__["_scene_for_case"](case_path)
        if loaded_scene is None:
            return None, None
        context_revision = editor_server.__dict__["_editor_revision"](
            case_path, loaded_scene, context_part
        )
        return loaded_scene, context_revision
    except CaseEditError:
        raise
    except Exception as exc:
        return {"status": "vision_error", "error": f"{type(exc).__name__}: {exc}"}


def _regenerate_attempt(
    vision: Any,
    working_path: Path,
    submit_dir: Path,
    target_path: Path,
    model: str,
    previous_violations: list[dict[str, Any]] | None,
    context_scene: Any | None,
    context_revision: str | None,
    editor_moves_applied: bool,
    typed_baseline: bytes,
    iteration: int,
    max_iterations: int,
) -> Path | dict[str, Any]:
    try:
        return vision.regenerate_case(
            working_path,
            submit_dir,
            output_path=target_path,
            model=model,
            violations=previous_violations,
            scene=context_scene,
            base_revision=context_revision,
            editor_moves_applied=editor_moves_applied,
        )
    except (CaseEditError, FileNotFoundError, RuntimeError) as exc:
        result: dict[str, Any] = {
            "status": "vision_error",
            "error": f"{type(exc).__name__}: {exc}",
        }
        if target_path.resolve() == working_path.resolve():
            working_path.write_bytes(typed_baseline)
        if isinstance(exc, CaseEditError):
            violation = exc.to_violation()
            result["violations"] = [violation]
            if iteration < max_iterations:
                return {
                    "status": "retry",
                    "violations": [violation],
                    "working_path": str(working_path),
                }
        return result


def _candidate_control_moves(path: Path, base_scene: Any | None) -> list[dict[str, Any]]:
    """Return control deltas from the editor base to a validated candidate."""
    if base_scene is None:
        return []
    from cadkit.assembly import AssemblyScene
    from cadkit.case_execution import execute_case_artifacts

    artifacts = execute_case_artifacts(path)
    if artifacts.assembly is None:
        raise RuntimeError("candidate case.py has no canonical ASSEMBLY_SPEC scene")
    candidate_scene = AssemblyScene.from_dict(artifacts.assembly)
    return _scene_control_moves(base_scene, candidate_scene)


def _scene_control_moves(base_scene: Any, candidate_scene: Any) -> list[dict[str, Any]]:
    """Return only trusted exact-six control deltas between two scenes."""
    from cadkit.assembly import CONTROL_IDS

    moves: list[dict[str, Any]] = []
    for control_id in CONTROL_IDS:
        element_id = f"control.{control_id}"
        before = base_scene.node(element_id).transform.position[:2]
        after = candidate_scene.node(element_id).transform.position[:2]
        if after != before:
            moves.append({"element_id": element_id, "new_position": list(after)})
    return moves


def _candidate_syntax_violation(case_path: Path) -> Any | None:
    """Return structured retry feedback when generated Python cannot parse."""
    from cadkit.constraints import ConstraintViolation

    try:
        ast.parse(case_path.read_text(), filename=str(case_path))
    except SyntaxError as exc:
        location = f"line {exc.lineno}"
        if exc.offset is not None:
            location += f", column {exc.offset}"
        return ConstraintViolation(
            "source",
            "python_syntax",
            "error",
            f"{exc.msg} ({location})",
            location,
        )
    return None


def _retry_baseline(
    last_valid: Path,
    candidate: Path,
    violations: list[Any],
    *,
    baseline_source: bytes,
) -> Path:
    """Keep malformed source as evidence but retry from the last valid source."""
    if not any(getattr(item, "rule", "") == "python_syntax" for item in violations):
        return candidate
    if candidate.resolve() == last_valid.resolve():
        # The public default edits in place. Restore the source that produced
        # this attempt before asking the model to retry; private submit
        # workspaces keep their malformed candidate separately as evidence.
        last_valid.write_bytes(baseline_source)
    return last_valid


def _candidate_geometry_changed(baseline_part: Any, candidate_part: Any) -> bool:
    """Return whether the rendered candidate mesh differs from its accepted base."""
    try:
        return baseline_part.tessellate(0.2) != candidate_part.tessellate(0.2)
    except Exception as exc:
        raise RuntimeError(f"candidate geometry comparison failed: {exc}") from exc


def _visual_candidate_violations(
    case_path: Path,
    validate_part: Callable[..., list[Any]],
    *,
    baseline_part: Any | None = None,
    baseline_placements: Any | None = None,
    base_scene: Any | None = None,
    baseline_source: bytes | None = None,
) -> tuple[list[Any], list[Any] | None]:
    from cadkit.case_execution import execute_case_artifacts

    syntax_violation = _candidate_syntax_violation(case_path)
    if syntax_violation is not None:
        return [syntax_violation], None
    artifacts = execute_case_artifacts(case_path)
    if artifacts.placements is None:
        violations = validate_part(artifacts.part)
    else:
        violations = validate_part(
            artifacts.part,
            placements=artifacts.placements,
            require_export_verification=False,
            whole_case_geometry=True,
        )
    if baseline_part is None or base_scene is None or artifacts.assembly is None:
        return violations, None
    try:
        from cadkit.assembly import AssemblyScene
        from cadkit.constraints import ConstraintViolation
        from editor import server as editor_server

        candidate_scene = AssemblyScene.from_dict(artifacts.assembly)
        if not _candidate_geometry_changed(baseline_part, artifacts.part):
            violations.append(
                ConstraintViolation(
                    "aesthetic",
                    "no_geometry_change",
                    "error",
                    "candidate source produced no rendered geometry change; choose a visibly "
                    "different supported exterior design or control position",
                )
            )
        moved_controls = _scene_control_moves(base_scene, candidate_scene)
        if moved_controls and baseline_source is not None:
            with tempfile.TemporaryDirectory(prefix="hf-agent-layout-baseline-") as directory:
                baseline_path = Path(directory) / "case.py"
                baseline_path.write_bytes(baseline_source)
                layout_baseline_part, layout_baseline_scene = editor_server.build_layout_baseline(
                    baseline_path, base_scene, moved_controls
                )
        elif moved_controls:
            layout_baseline_part, layout_baseline_scene = editor_server.build_layout_baseline(
                case_path,
                base_scene,
                moved_controls,
            )
        else:
            layout_baseline_part, layout_baseline_scene = baseline_part, base_scene
        from cadkit.assembly import placements_from_assembly

        layout_baseline_placements = placements_from_assembly(layout_baseline_scene)
        moved_baseline_violations = validate_part(
            layout_baseline_part,
            placements=layout_baseline_placements,
            require_export_verification=False,
            whole_case_geometry=True,
        )
        violations.extend(
            editor_server.validate_layout_candidate(
                layout_baseline_part,
                artifacts.part,
                layout_baseline_scene,
            )
        )
        return violations, moved_baseline_violations
    except Exception as exc:
        from cadkit.constraints import ConstraintViolation

        violations.append(
            ConstraintViolation(
                "print",
                "exterior_protected_geometry_unavailable",
                "error",
                f"exterior protected-geometry validation failed closed: {exc}",
            )
        )
        return violations, []


_GRANDFATHERED_PREVIEW_RULES = frozenset({"keepout_overlap", "route_intersects_volume"})


def _violation_signature(violation: Any) -> tuple[str, str, str, str, str]:
    return (
        str(getattr(violation, "category", "")),
        str(getattr(violation, "rule", "")),
        str(getattr(violation, "severity", "")),
        str(getattr(violation, "message", "")),
        str(getattr(violation, "location", "")),
    )


def _has_blocking_candidate_violations(candidate: list[Any], baseline: list[Any]) -> bool:
    baseline_signatures = sorted(
        _violation_signature(item)
        for item in baseline
        if getattr(item, "severity", None) == "error"
        and getattr(item, "rule", None) in _GRANDFATHERED_PREVIEW_RULES
    )
    candidate_signatures = sorted(
        _violation_signature(item)
        for item in candidate
        if getattr(item, "severity", None) == "error"
        and getattr(item, "rule", None) in _GRANDFATHERED_PREVIEW_RULES
    )
    if candidate_signatures != baseline_signatures:
        return True
    for violation in candidate:
        if getattr(violation, "severity", None) != "error":
            continue
        if getattr(violation, "rule", None) not in _GRANDFATHERED_PREVIEW_RULES:
            return True
    return False
