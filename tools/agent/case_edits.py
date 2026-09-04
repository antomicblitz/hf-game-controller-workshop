"""Strict, typed proposals returned by the MIG-04 vision agent.

The model is deliberately kept outside the source-generation boundary.  It
returns a small JSON value, and trusted code validates that value before
turning a control target into the editor's existing scene-move record.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any, cast

from cadkit.assembly import CONTROL_IDS
from cadkit.exterior_design import ExteriorDesignSpec

SCHEMA_NAME = "cadkit.case-edit"
SCHEMA_VERSION = "1.0"
SCHEMA_ID = f"{SCHEMA_NAME}/{SCHEMA_VERSION}"
MAX_RESPONSE_BYTES = 64 * 1024
MAX_UNSUPPORTED_REASON_LENGTH = 512
CANONICAL_CONTROL_IDS = tuple(f"control.{control_id}" for control_id in CONTROL_IDS)
_CANONICAL_CONTROL_ID_SET = frozenset(CANONICAL_CONTROL_IDS)
_REVISION_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_ROOT_KEYS = frozenset(
    {"schema", "version", "base_revision", "control_moves", "exterior_design", "unsupported_reason"}
)
_CONTROL_MOVE_KEYS = frozenset({"id", "target"})
_EXTERIOR_KEYS = frozenset(field.name for field in fields(ExteriorDesignSpec))


class CaseEditError(ValueError):
    """A proposal failed a bounded schema, revision, or edit check."""

    def __init__(
        self,
        rule: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.rule = rule
        self.message = message
        self.details = dict(details or {})
        super().__init__(message)

    def to_violation(self) -> dict[str, Any]:
        """Return retry feedback without exposing source or implementation state."""
        violation: dict[str, Any] = {
            "category": "case_edit",
            "rule": self.rule,
            "severity": "error",
            "message": self.message,
        }
        violation.update(self.details)
        return violation


@dataclass(frozen=True)
class ControlMove:
    """One exact-six semantic control target in case-local millimetres."""

    control_id: str
    target: tuple[float, float]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.control_id, "target": list(self.target)}


ExteriorDesign = ExteriorDesignSpec


@dataclass(frozen=True)
class CaseEditProposal:
    """A parsed and schema-valid proposal, before scene validation."""

    base_revision: str
    control_moves: tuple[ControlMove, ...]
    exterior_design: ExteriorDesign | None = None
    unsupported_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": SCHEMA_NAME,
            "version": SCHEMA_VERSION,
            "base_revision": self.base_revision,
            "control_moves": [move.to_dict() for move in self.control_moves],
            "exterior_design": (
                self.exterior_design.to_dict() if self.exterior_design is not None else None
            ),
        }
        if self.unsupported_reason is not None:
            result["unsupported_reason"] = self.unsupported_reason
        return result


class _DuplicateKey(ValueError):
    """Internal marker for duplicate JSON object keys."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


def _error(rule: str, message: str, **details: Any) -> CaseEditError:
    return CaseEditError(rule, message, details=details)


def _ensure_finite_pair(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, list):
        raise _error("malformed_target", f"{label} must be a two-item JSON array.")
    values = cast(list[Any], value)
    if len(values) != 2:
        raise _error("malformed_target", f"{label} must be a two-item JSON array.")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in values):
        raise _error("non_finite_target", f"{label} must contain finite numbers, not booleans.")
    try:
        target = (float(values[0]), float(values[1]))
    except (TypeError, ValueError, OverflowError) as exc:
        raise _error("non_finite_target", f"{label} must contain finite numbers.") from exc
    if any(not math.isfinite(item) for item in target):
        raise _error("non_finite_target", f"{label} must contain finite numbers.")
    return target


def _decode_response(response: object) -> dict[str, Any]:
    if isinstance(response, str):
        encoded = response.encode("utf-8")
    elif isinstance(response, (bytes, bytearray)):
        encoded = bytes(response)
    else:
        raise _error("malformed_json", "vision output must be a UTF-8 JSON object.")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise _error("oversized_output", f"vision output exceeds {MAX_RESPONSE_BYTES} bytes.")
    try:
        raw = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        ValueError,
        RecursionError,
    ) as exc:
        raise _error(
            "malformed_json", "vision output must be strict JSON; source and Markdown are rejected."
        ) from exc
    if not isinstance(raw, dict):
        raise _error(
            "malformed_json", "vision output must be a JSON object, not source or Markdown."
        )
    return cast(dict[str, Any], raw)


def _validate_root(raw: dict[str, Any]) -> None:
    unknown = set(raw) - _ROOT_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise _error("unknown_key", f"proposal contains unknown key(s): {names}.")
    required = {"schema", "version", "base_revision", "control_moves"}
    missing = required - set(raw)
    if missing:
        raise _error(
            "missing_key", f"proposal is missing required key(s): {', '.join(sorted(missing))}."
        )
    if raw["schema"] != SCHEMA_NAME or raw["version"] != SCHEMA_VERSION:
        raise _error("schema_version", f"proposal schema must be {SCHEMA_ID!r}.")


def _parse_control_move(raw_move: Any, index: int) -> ControlMove:
    if not isinstance(raw_move, dict):
        raise _error(
            "malformed_move", f"control_moves[{index}] must be an object.", move_index=index
        )
    raw_move = cast(dict[str, Any], raw_move)
    unknown = set(raw_move) - _CONTROL_MOVE_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise _error(
            "unknown_move_key",
            f"control_moves[{index}] contains unknown key(s): {names}.",
            move_index=index,
        )
    if "id" not in raw_move or "target" not in raw_move:
        raise _error(
            "malformed_move",
            f"control_moves[{index}] must contain exactly id and target.",
            move_index=index,
        )
    control_id = raw_move["id"]
    if not isinstance(control_id, str) or control_id not in _CANONICAL_CONTROL_ID_SET:
        raise _error(
            "unknown_control_id",
            f"control_moves[{index}] has an unknown canonical control ID.",
            move_index=index,
        )
    return ControlMove(
        control_id,
        _ensure_finite_pair(raw_move["target"], f"control_moves[{index}].target"),
    )


def _parse_control_moves(raw: Any) -> tuple[ControlMove, ...]:
    if not isinstance(raw, list):
        raise _error("malformed_moves", "control_moves must be a JSON array.")
    raw_moves = cast(list[Any], raw)
    if len(raw_moves) > len(CANONICAL_CONTROL_IDS):
        raise _error(
            "too_many_moves",
            f"control_moves must contain at most {len(CANONICAL_CONTROL_IDS)} moves.",
        )
    moves: list[ControlMove] = []
    seen: set[str] = set()
    for index, raw_move in enumerate(raw_moves):
        move = _parse_control_move(raw_move, index)
        if move.control_id in seen:
            raise _error(
                "duplicate_control_id",
                f"control_moves contains duplicate control ID {move.control_id!r}.",
                move_index=index,
            )
        seen.add(move.control_id)
        moves.append(move)
    return tuple(sorted(moves, key=lambda move: CANONICAL_CONTROL_IDS.index(move.control_id)))


def _parse_exterior(value: Any) -> ExteriorDesignSpec | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _error("malformed_exterior", "exterior_design must be null or an object.")
    value = cast(dict[str, Any], value)
    unknown = set(value) - _EXTERIOR_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise _error("unknown_exterior_key", f"exterior_design contains unknown key(s): {names}.")
    try:
        return ExteriorDesignSpec.from_dict(value)
    except ValueError as exc:
        rule = (
            "unsupported_exterior_profile"
            if "profile" in str(exc) or "unknown exterior profile" in str(exc)
            else "invalid_exterior"
        )
        raise _error(rule, str(exc)) from exc


def parse_case_edit(response: object) -> CaseEditProposal:
    """Parse only the strict ``cadkit.case-edit/1.0`` JSON value.

    Markdown, Python source, prose, duplicate keys, unknown keys, and JSON
    extensions are intentionally rejected rather than normalized.
    """
    raw = _decode_response(response)
    _validate_root(raw)
    base_revision = raw["base_revision"]
    if not isinstance(base_revision, str) or _REVISION_PATTERN.fullmatch(base_revision) is None:
        raise _error("malformed_revision", "base_revision must be a lowercase SHA-256 digest.")

    moves = _parse_control_moves(raw["control_moves"])

    exterior = _parse_exterior(raw.get("exterior_design"))
    unsupported_reason = raw.get("unsupported_reason")
    if unsupported_reason is not None and (
        not isinstance(unsupported_reason, str)
        or not unsupported_reason.strip()
        or len(unsupported_reason) > MAX_UNSUPPORTED_REASON_LENGTH
    ):
        raise _error(
            "malformed_unsupported_reason",
            "unsupported_reason must be a short non-empty string.",
        )
    return CaseEditProposal(base_revision, moves, exterior, unsupported_reason)


def _current_position(
    control_id: str, current_positions: Mapping[str, Sequence[float]]
) -> tuple[float, float]:
    current = current_positions.get(control_id)
    if current is None:
        current = current_positions.get(control_id.removeprefix("control."))
    if current is None:
        raise _error("unknown_control_id", f"no current position exists for {control_id!r}.")
    if len(current) != 2 or any(isinstance(item, bool) for item in current):
        raise _error("malformed_scene", f"current position for {control_id!r} is malformed.")
    current_pair = (float(current[0]), float(current[1]))
    if any(not math.isfinite(item) for item in current_pair):
        raise _error("malformed_scene", f"current position for {control_id!r} is non-finite.")
    return current_pair


def _proposal_is_noop(
    proposal: CaseEditProposal,
    current_positions: Mapping[str, Sequence[float]],
    current_exterior: ExteriorDesignSpec | None,
) -> bool:
    controls_noop = all(
        move.target == _current_position(move.control_id, current_positions)
        for move in proposal.control_moves
    )
    exterior_noop = proposal.exterior_design is None or proposal.exterior_design == current_exterior
    return controls_noop and exterior_noop


def _current_exterior_spec(
    current_exterior: ExteriorDesignSpec | Mapping[str, Any] | None,
) -> ExteriorDesignSpec | None:
    if current_exterior is None:
        return None
    if type(current_exterior) is ExteriorDesignSpec:
        return current_exterior
    if isinstance(current_exterior, Mapping):
        try:
            return ExteriorDesignSpec.from_dict(current_exterior)
        except ValueError as exc:
            raise _error("malformed_scene", f"current exterior design is malformed: {exc}") from exc
    raise _error("malformed_scene", "current exterior design is malformed.")


def _validate_unsupported(proposal: CaseEditProposal) -> None:
    if proposal.unsupported_reason is None:
        return
    if proposal.control_moves or proposal.exterior_design is not None:
        raise _error(
            "malformed_unsupported_reason",
            "unsupported_reason cannot be combined with a control or exterior edit.",
        )
    raise _error(
        "unsupported_request",
        f"proposal is not applicable: {proposal.unsupported_reason.strip()}",
    )


def validate_case_edit(
    response: object,
    *,
    current_revision: str,
    current_positions: Mapping[str, Sequence[float]],
    current_exterior: ExteriorDesignSpec | Mapping[str, Any] | None = None,
) -> CaseEditProposal:
    """Parse and apply revision/no-op policy before scene validation.

    The exterior value is intentionally parsed by the authoritative cadkit
    value object.  Agent code owns proposal framing only; it does not duplicate
    the silhouette limits or profile semantics.
    """
    proposal = parse_case_edit(response)
    if proposal.base_revision != current_revision:
        raise _error(
            "stale_revision",
            "proposal base_revision is stale; reload the current editor revision.",
            expected_revision=current_revision,
            received_revision=proposal.base_revision,
        )
    _validate_unsupported(proposal)
    current_spec = _current_exterior_spec(current_exterior)
    if proposal.exterior_design is not None and current_spec is None:
        raise _error(
            "exterior_baseline_unavailable",
            "exterior edits require the current literal EXTERIOR_DESIGN baseline.",
        )
    if not proposal.control_moves and proposal.exterior_design is None:
        raise _error("empty_proposal", "proposal must contain at least one control move.")
    if _proposal_is_noop(proposal, current_positions, current_spec):
        raise _error(
            "no_op_proposal", "proposal does not change the current controls or exterior design."
        )
    return proposal


def proposal_move_records(
    proposal: CaseEditProposal,
    current_positions: Mapping[str, Sequence[float]],
) -> list[dict[str, Any]]:
    """Adapt typed targets to the editor's existing expected/new move shape."""
    records: list[dict[str, Any]] = []
    for move in proposal.control_moves:
        current = current_positions.get(move.control_id)
        if current is None:
            current = current_positions.get(move.control_id.removeprefix("control."))
        if current is None or len(current) != 2:
            raise _error("malformed_scene", f"no current position exists for {move.control_id!r}.")
        records.append(
            {
                "element_id": move.control_id,
                "expected_position": [float(current[0]), float(current[1])],
                "new_position": list(move.target),
            }
        )
    return records


# Descriptive aliases keep the boundary easy to discover for callers.
parse_proposal = parse_case_edit
validate_proposal = validate_case_edit

__all__ = [
    "CANONICAL_CONTROL_IDS",
    "MAX_RESPONSE_BYTES",
    "SCHEMA_ID",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "CaseEditError",
    "CaseEditProposal",
    "ControlMove",
    "ExteriorDesign",
    "parse_case_edit",
    "parse_proposal",
    "proposal_move_records",
    "validate_case_edit",
    "validate_proposal",
]
