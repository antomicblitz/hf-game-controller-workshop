"""Trusted, source-preserving control-coordinate rewriting.

Only literal ``CONTROLS`` dictionary ``x``/``y`` values are editable.  The
rewriter does not execute source, invoke the model, or unparse the whole AST;
all bytes outside the selected numeric literals remain unchanged.
"""

from __future__ import annotations

import ast
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any, cast

from .assembly import CONTROL_IDS
from .exterior_design import ExteriorDesignSpec


def _controls_literal(tree: ast.Module) -> ast.List:
    for statement in tree.body:
        value: ast.expr | None = None
        if (
            isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "CONTROLS"
                for target in statement.targets
            )
        ) or (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "CONTROLS"
        ):
            value = statement.value
        if isinstance(value, ast.List):
            return value
    raise ValueError("case source CONTROLS must be a literal list for move previews")


def _control_literal_fields(record: ast.expr) -> tuple[dict[str, int], ast.Dict]:
    if not isinstance(record, ast.Dict):
        raise ValueError("case source CONTROLS entries must be literal dictionaries")
    fields = {
        key.value: index
        for index, key in enumerate(record.keys)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    if not {"id", "x", "y"} <= fields.keys():
        raise ValueError("case source CONTROLS entries must contain literal id, x, and y fields")
    return fields, record


def _literal_number(value: ast.expr, field: str) -> None:
    if (
        not isinstance(value, ast.Constant)
        or isinstance(value.value, bool)
        or not isinstance(value.value, (int, float))
    ):
        raise ValueError(f"case source CONTROLS {field} fields must be literal finite numbers")
    if not math.isfinite(float(value.value)):
        raise ValueError(f"case source CONTROLS {field} fields must be literal finite numbers")


def _target_position(control_id: str, value: Sequence[float]) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"target for {control_id} must contain two XY values")
    if any(isinstance(item, bool) for item in value):
        raise ValueError(f"target for {control_id} must contain finite numbers")
    position = (float(value[0]), float(value[1]))
    if any(not math.isfinite(item) for item in position):
        raise ValueError(f"target for {control_id} must contain finite numbers")
    return position


def _normalise_targets(targets: Mapping[str, Sequence[float]]) -> dict[str, tuple[float, float]]:
    canonical = {f"control.{control_id}" for control_id in CONTROL_IDS}
    normalised: dict[str, tuple[float, float]] = {}
    for raw_id, value in targets.items():
        control_id = raw_id if raw_id.startswith("control.") else f"control.{raw_id}"
        if control_id not in canonical:
            raise ValueError(f"unknown control target: {raw_id!r}")
        position = _target_position(control_id, value)
        if control_id in normalised:
            raise ValueError(f"duplicate control target: {control_id}")
        normalised[control_id] = position
    if not normalised:
        raise ValueError("at least one control target is required")
    return normalised


def _line_starts(data: bytes) -> list[int]:
    starts = [0]
    for index, byte in enumerate(data):
        if byte == 10:
            starts.append(index + 1)
    return starts


def _offset(starts: list[int], line: int, column: int) -> int:
    return starts[line - 1] + column


def _direct_exterior_import(tree: ast.Module) -> None:
    """Require the one trusted, unaliased exterior value-object import."""
    count = 0
    for statement in tree.body:
        if not isinstance(statement, ast.ImportFrom) or statement.module != "cadkit":
            continue
        for alias in statement.names:
            if alias.name != "ExteriorDesignSpec":
                continue
            if alias.asname is not None:
                raise ValueError("EXTERIOR_DESIGN requires an unaliased cadkit ExteriorDesignSpec")
            count += 1
    if count != 1:
        raise ValueError("case source must import ExteriorDesignSpec exactly once from cadkit")


def _reject_exterior_rebinding(statement: ast.stmt) -> None:
    if isinstance(statement, ast.ImportFrom) and any(
        alias.name == "EXTERIOR_DESIGN" or alias.asname == "EXTERIOR_DESIGN"
        for alias in statement.names
    ):
        raise ValueError("EXTERIOR_DESIGN must be a single literal module binding")
    if isinstance(statement, ast.Import) and any(
        alias.name.split(".", 1)[0] == "EXTERIOR_DESIGN" or alias.asname == "EXTERIOR_DESIGN"
        for alias in statement.names
    ):
        raise ValueError("EXTERIOR_DESIGN must be a single literal module binding")
    if (
        isinstance(statement, ast.AugAssign)
        and isinstance(statement.target, ast.Name)
        and statement.target.id == "EXTERIOR_DESIGN"
    ):
        raise ValueError("EXTERIOR_DESIGN must be a single literal module binding")
    if isinstance(statement, ast.Delete) and any(
        isinstance(target, ast.Name) and target.id == "EXTERIOR_DESIGN"
        for target in statement.targets
    ):
        raise ValueError("EXTERIOR_DESIGN must be a single literal module binding")


def _exterior_assignment_for_statement(
    statement: ast.stmt,
) -> ast.Assign | ast.AnnAssign | None:
    _reject_exterior_rebinding(statement)
    if isinstance(statement, ast.Assign):
        if not any(
            isinstance(target, ast.Name) and target.id == "EXTERIOR_DESIGN"
            for target in statement.targets
        ):
            return None
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            raise ValueError("EXTERIOR_DESIGN must be a single literal module binding")
        return statement
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return statement if statement.target.id == "EXTERIOR_DESIGN" else None
    return None


def _exterior_assignment(tree: ast.Module) -> tuple[ast.Assign | ast.AnnAssign, ast.Call]:
    """Locate exactly one module-level literal exterior constructor."""
    assignments = [
        assignment
        for statement in tree.body
        if (assignment := _exterior_assignment_for_statement(statement)) is not None
    ]
    if len(assignments) != 1:
        raise ValueError("case source must contain exactly one EXTERIOR_DESIGN binding")
    assignment = assignments[0]
    value = assignment.value
    if (
        not isinstance(value, ast.Call)
        or not isinstance(value.func, ast.Name)
        or value.func.id != "ExteriorDesignSpec"
    ):
        raise ValueError("EXTERIOR_DESIGN must call the literal ExteriorDesignSpec constructor")
    return assignment, value


def _literal_exterior_kwargs(call: ast.Call) -> dict[str, Any]:
    if call.args or any(keyword.arg is None for keyword in call.keywords):
        raise ValueError("EXTERIOR_DESIGN must use literal keyword arguments only")
    kwargs: dict[str, Any] = {}
    allowed = {field.name for field in fields(ExteriorDesignSpec)}
    for keyword in call.keywords:
        name = keyword.arg
        if name is None or name not in allowed:
            raise ValueError(f"EXTERIOR_DESIGN contains unknown keyword {name!r}")
        if name in kwargs:
            raise ValueError(f"EXTERIOR_DESIGN contains duplicate keyword {name!r}")
        try:
            literal = ast.literal_eval(keyword.value)
        except (ValueError, TypeError, MemoryError, RecursionError) as exc:
            raise ValueError(f"EXTERIOR_DESIGN.{name} must be a literal value") from exc
        if isinstance(literal, (list, dict, set, tuple)):
            raise ValueError(f"EXTERIOR_DESIGN.{name} must be a scalar literal")
        kwargs[name] = literal
    return kwargs


def inspect_exterior_design_source(source: str) -> ExteriorDesignSpec:
    """Inspect one literal exterior binding without executing case source."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"case source is not valid Python: {exc}") from exc
    _direct_exterior_import(tree)
    _assignment, call = _exterior_assignment(tree)
    try:
        return ExteriorDesignSpec.from_dict(_literal_exterior_kwargs(call))
    except ValueError as exc:
        raise ValueError(f"EXTERIOR_DESIGN is invalid: {exc}") from exc


def _exterior_call_span(source: str) -> tuple[int, int]:
    tree = ast.parse(source)
    _direct_exterior_import(tree)
    _assignment, call = _exterior_assignment(tree)
    data = source.encode("utf-8")
    starts = _line_starts(data)
    return (
        _offset(starts, call.lineno, call.col_offset),
        _offset(starts, cast(int, call.end_lineno), cast(int, call.end_col_offset)),
    )


def _render_exterior_constructor(design: ExteriorDesignSpec) -> str:
    values = design.to_dict()
    if design.profile != "custom":
        return f"ExteriorDesignSpec(profile={json.dumps(design.profile)})"
    lines = ["ExteriorDesignSpec("]
    for name in fields(ExteriorDesignSpec):
        value = values[name.name]
        rendered = json.dumps(value) if isinstance(value, str) else repr(value)
        lines.append(f"    {name.name}={rendered},")
    lines.append(")")
    return "\n".join(lines)


def rewrite_exterior_design_source(source: str, design: ExteriorDesignSpec) -> str:
    """Replace only the literal ``EXTERIOR_DESIGN`` constructor expression."""
    if type(design) is not ExteriorDesignSpec:
        raise ValueError("design must be an exact ExteriorDesignSpec")
    # Inspect first so aliases, dynamic calls, duplicate bindings, and unknown
    # constructor fields fail before any bytes are changed.
    inspect_exterior_design_source(source)
    start, end = _exterior_call_span(source)
    data = source.encode("utf-8")
    replacement = _render_exterior_constructor(design).encode("utf-8")
    return (data[:start] + replacement + data[end:]).decode("utf-8")


def _editable_source_bytes(source: str) -> bytes:
    """Mask only trusted control coordinates and the exterior constructor."""
    tree = ast.parse(source)
    controls = _controls_literal(tree)
    _, exterior_call = _exterior_assignment(tree)
    data = source.encode("utf-8")
    starts = _line_starts(data)
    spans: list[tuple[int, int]] = [
        (
            _offset(starts, exterior_call.lineno, exterior_call.col_offset),
            _offset(
                starts, cast(int, exterior_call.end_lineno), cast(int, exterior_call.end_col_offset)
            ),
        )
    ]
    for raw_record in controls.elts:
        fields_by_name, record = _control_literal_fields(raw_record)
        for field_name in ("x", "y"):
            value = record.values[fields_by_name[field_name]]
            _literal_number(value, field_name)
            spans.append(
                (
                    _offset(starts, value.lineno, value.col_offset),
                    _offset(starts, cast(int, value.end_lineno), cast(int, value.end_col_offset)),
                )
            )
    for start, end in sorted(spans, reverse=True):
        data = data[:start] + b"<trusted-edit>" + data[end:]
    return data


def source_changes_outside_editable(base: str, candidate: str) -> bool:
    """Return whether candidate changed bytes outside trusted literal fields."""
    try:
        return _editable_source_bytes(base) != _editable_source_bytes(candidate)
    except (SyntaxError, ValueError, TypeError):
        return True


# Descriptive aliases keep the source boundary discoverable to editor/agent
# callers without creating a second parser or rewrite implementation.
inspect_exterior_design = inspect_exterior_design_source
rewrite_exterior_design = rewrite_exterior_design_source


def rewrite_controls_source(source: str, targets: Mapping[str, Sequence[float]]) -> str:
    """Return ``source`` with only requested literal control XY values changed."""
    parsed_targets = _normalise_targets(targets)
    tree = ast.parse(source)
    controls = _controls_literal(tree)
    data = source.encode("utf-8")
    starts = _line_starts(data)
    replacements: list[tuple[int, int, bytes]] = []
    found: set[str] = set()
    for raw_record in controls.elts:
        fields, record = _control_literal_fields(raw_record)
        raw_id = record.values[fields["id"]]
        if not isinstance(raw_id, ast.Constant) or not isinstance(raw_id.value, str):
            raise ValueError("case source CONTROLS id fields must be literal control IDs")
        control_id = (
            raw_id.value if raw_id.value.startswith("control.") else f"control.{raw_id.value}"
        )
        if control_id not in parsed_targets:
            continue
        if control_id in found:
            raise ValueError(f"case source contains duplicate moved control: {control_id}")
        x_value = record.values[fields["x"]]
        y_value = record.values[fields["y"]]
        _literal_number(x_value, "x")
        _literal_number(y_value, "y")
        x, y = parsed_targets[control_id]
        for value_node, replacement in ((x_value, repr(x)), (y_value, repr(y))):
            start = _offset(
                starts,
                value_node.lineno,
                value_node.col_offset,
            )
            end = _offset(
                starts,
                cast(int, value_node.end_lineno),
                cast(int, value_node.end_col_offset),
            )
            replacements.append((start, end, replacement.encode("ascii")))
        found.add(control_id)
    missing = sorted(set(parsed_targets) - found)
    if missing:
        raise ValueError(f"case source CONTROLS is missing moved controls: {', '.join(missing)}")
    for start, end, replacement in sorted(replacements, reverse=True):
        data = data[:start] + replacement + data[end:]
    return data.decode("utf-8")


def rewrite_case_source(
    path: Path,
    scene: Any,
    accepted_moves: Sequence[Mapping[str, Any]],
    *,
    exterior_design: ExteriorDesignSpec | None = None,
) -> str:
    """Rewrite trusted literal controls and, optionally, the exterior value."""
    targets: dict[str, tuple[float, float]] = {}
    for move in accepted_moves:
        element_id = move.get("element_id")
        if not isinstance(element_id, str):
            raise ValueError("accepted move must contain an element_id")
        try:
            position = scene.node(element_id).transform.position[:2]
        except (AttributeError, KeyError) as exc:
            raise ValueError(
                f"accepted move references unknown scene element: {element_id!r}"
            ) from exc
        targets[element_id] = (float(position[0]), float(position[1]))
    source = path.read_text(encoding="utf-8")
    if targets:
        source = rewrite_controls_source(source, targets)
    if exterior_design is not None:
        source = rewrite_exterior_design_source(source, exterior_design)
    return source


rewrite_control_positions = rewrite_controls_source

__all__ = [
    "inspect_exterior_design",
    "inspect_exterior_design_source",
    "rewrite_case_source",
    "rewrite_control_positions",
    "rewrite_controls_source",
    "rewrite_exterior_design",
    "rewrite_exterior_design_source",
    "source_changes_outside_editable",
]
