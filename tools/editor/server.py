"""Hardened local editor server with canonical 2D/3D assembly previews.

Generated case source is never executed in this process.  The only runtime
bridge is :func:`cadkit.case_execution.execute_case_artifacts`, which returns
the exact Part and a bounded JSON scene from the mandatory OS sandbox.
"""

from __future__ import annotations

import ast
import base64
import binascii
import contextlib
import datetime as _dt
import errno
import hashlib
import io
import itertools
import json
import math
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
import uuid
from collections.abc import Generator
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, Protocol, cast

os.environ.setdefault("YACV_DISABLE_SERVER", "1")
_SESSION_DIR = Path(__file__).resolve().parents[2]
if str(_SESSION_DIR / "tools") not in sys.path:
    sys.path.insert(0, str(_SESSION_DIR / "tools"))

from cadkit.assembly import (  # noqa: E402
    CONTROL_COMPONENT_IDS,
    CONTROL_IDS,
    CONTROL_KINDS,
    CONTROL_ROLES,
    ELECTRONICS_BACKBONE_GROUND_BRIDGE_ROUTE_ID,
    ELECTRONICS_BACKBONE_PREVIEW_DIMENSIONS_MM,
)
from flask import Flask, Response, jsonify, request, send_file  # noqa: E402
from werkzeug.serving import BaseWSGIServer, make_server  # noqa: E402

from .snapshot import composite_snapshot, make_test_base_png, tessellate_part  # noqa: E402

JsonObject = dict[str, Any]
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_FEEDBACK_BYTES = 256 * 1024
MAX_PROTOTYPE_MOVES_BYTES = 8 * 1024
MAX_SNAPSHOT_B64_BYTES = 8 * 1024 * 1024
MAX_CASE_BYTES = 4 * 1024 * 1024
MAX_RENDER_DIMENSION = 4096
MAX_RENDER_PIXELS = 16_777_216
# Small allowance for subprocess BRep round-trip noise in differential checks.
_EXTERIOR_PREVIEW_TOLERANCE_MM = 1e-4
DEFAULT_EDITOR_PORT = 5000
LAST_EDITOR_PORT = 5010


class _LockProtocol(Protocol):
    def acquire(self) -> bool: ...
    def release(self) -> None: ...


_CASE_LOCKS: dict[Path, _LockProtocol] = {}
_CASE_LOCKS_GUARD = threading.Lock()


def _case_lock(case_path: Path) -> contextlib.AbstractContextManager[None]:
    key = case_path.resolve()
    with _CASE_LOCKS_GUARD:
        lock = _CASE_LOCKS.setdefault(key, threading.RLock())
    return _locked(lock)


@contextlib.contextmanager
def _locked(lock: _LockProtocol) -> Generator[None, None, None]:
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _restore_case_source(case_path: Path, original: bytes | None, mode: int = 0o600) -> None:
    if original is not None:
        with contextlib.suppress(OSError):
            _atomic_write_bytes(case_path, original, mode)


def _read_case_bytes(case_path: Path) -> bytes:
    with case_path.open("rb") as handle:
        content = handle.read(MAX_CASE_BYTES + 1)
    if len(content) > MAX_CASE_BYTES:
        raise ValueError("case.py exceeds the execution size limit")
    return content


def _decode_base64(value: str, label: str, maximum: int) -> bytes:
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds the request limit")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{label} is not valid base64") from exc


def _render_dimensions(body: JsonObject) -> tuple[int, int]:
    try:
        width = int(body.get("width", 800))
        height = int(body.get("height", 600))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("width and height must be integers") from exc
    if (
        isinstance(body.get("width", 800), bool)
        or isinstance(body.get("height", 600), bool)
        or width < 1
        or height < 1
        or width > MAX_RENDER_DIMENSION
        or height > MAX_RENDER_DIMENSION
        or width * height > MAX_RENDER_PIXELS
    ):
        raise ValueError("width and height exceed the render limits")
    return width, height


def _node_aabb(
    node: Any, dimensions: Any
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    x, y, z = dimensions.x, dimensions.y, dimensions.z
    angle = math.radians(node.transform.rotation_deg[2])
    rotated_x = abs(x * math.cos(angle)) + abs(y * math.sin(angle))
    rotated_y = abs(x * math.sin(angle)) + abs(y * math.cos(angle))
    cx, cy, cz = node.transform.position
    return (
        (cx - rotated_x / 2, cy - rotated_y / 2, cz - z / 2),
        (cx + rotated_x / 2, cy + rotated_y / 2, cz + z / 2),
    )


def _aabb_overlaps(left: Any, right: Any) -> bool:
    return all(
        left[0][axis] < right[1][axis] and right[0][axis] < left[1][axis] for axis in range(3)
    )


def _move_violation(rule: str, message: str) -> dict[str, str]:
    return {"rule": rule, "severity": "error", "message": message}


class SceneMoveError(ValueError):
    """A move failed while applying a sequential canonical-scene delta."""

    def __init__(self, result: dict[str, Any], index: int) -> None:
        self.result = result
        self.index = index
        super().__init__(result["violations"][0]["message"])


def _invalid_position_result(element_id: str) -> dict[str, Any]:
    return {
        "valid": False,
        "element_id": element_id,
        "violations": [
            _move_violation(
                "position", "new_position must contain two finite XY millimetre values."
            )
        ],
        "rules": ["finite XY", "0.1mm payload"],
    }


def _rounded_position(value: Any) -> tuple[float, float] | None:
    try:
        if isinstance(value, (str, bytes)) or len(value) != 2:
            raise ValueError
        if any(isinstance(item, bool) for item in value):
            raise ValueError
        raw_x, raw_y = (float(item) for item in value)
        if not math.isfinite(raw_x) or not math.isfinite(raw_y):
            raise ValueError
        return round(raw_x * 10) / 10, round(raw_y * 10) / 10
    except (TypeError, ValueError, OverflowError):
        return None


def _case_bounds_violations(scene: Any, node: Any, footprint: Any) -> list[dict[str, str]]:
    x, y = node.transform.position[:2]
    if (
        x < footprint.x / 2
        or x > scene.case_dimensions.x - footprint.x / 2
        or y < footprint.y / 2
        or y > scene.case_dimensions.y - footprint.y / 2
    ):
        return [
            _move_violation(
                "case_bounds_keepout", f"{node.id} keep-out must remain inside the case bounds."
            )
        ]
    return []


def _control_spacing_violations(scene: Any, node: Any) -> list[dict[str, str]]:
    from cadkit.constraints import MIN_BUTTON_SPACING_MM

    violations: list[dict[str, str]] = []
    for other in scene.nodes:
        if other.id == node.id or other.mobility != "constrained_xy":
            continue
        distance = math.dist(node.transform.position[:2], other.transform.position[:2])
        if distance < MIN_BUTTON_SPACING_MM:
            violations.append(
                _move_violation(
                    "control_control_spacing",
                    f"Controls {node.id} and {other.id} must be at least "
                    f"{MIN_BUTTON_SPACING_MM:g} mm apart; got {distance:.1f} mm.",
                )
            )
    return violations


def _control_overlap_violations(scene: Any, node: Any, footprint: Any) -> list[dict[str, str]]:
    proposed_box = _node_aabb(node, footprint)
    violations: list[dict[str, str]] = []
    for other in scene.nodes:
        if other.id == node.id or other.id.startswith("case."):
            continue
        if other.metadata.get("mechanical_interface") is False:
            continue
        dimensions = (
            other.physical_dimensions
            if other.kind in {"feather_board", "micro_usb_connector", "rear_usb_opening"}
            else (other.keep_out_dimensions or other.physical_dimensions)
        )
        if _aabb_overlaps(proposed_box, _node_aabb(other, dimensions)):
            violations.append(
                _move_violation(
                    "component_keepout_aabb", f"{node.id} keep-out overlaps {other.id}."
                )
            )
    return violations


def validate_scene_move(scene: Any, element_id: str, new_position: Any) -> dict[str, Any]:
    """Validate one rounded case-local XY move without mutating ``scene``."""
    nodes = {node.id: node for node in scene.nodes}
    if element_id not in nodes:
        return {
            "valid": False,
            "element_id": element_id,
            "violations": [
                _move_violation("unknown_element", f"Unknown scene element: {element_id!r}.")
            ],
            "rules": ["known element", "mobility=constrained_xy"],
        }
    node = nodes[element_id]
    violations: list[dict[str, str]] = []
    expected = _CONTROL_EXPECTATIONS.get(element_id)
    metadata = node.metadata
    if (
        expected is None
        or (node.kind, node.mobility) != (expected[0], "constrained_xy")
        or metadata.get("control_role") != expected[1]
        or metadata.get("component_id") != expected[2]
    ):
        violations.append(
            _move_violation(
                "mobility",
                f"{element_id} is not an authorized constrained_xy control with fixed identity.",
            )
        )
    position = _rounded_position(new_position)
    if position is None:
        return _invalid_position_result(element_id)
    x, y = position

    proposed = replace(
        node, transform=replace(node.transform, position=(x, y, node.transform.position[2]))
    )
    proposed_scene = replace(
        scene, nodes=tuple(proposed if item.id == element_id else item for item in scene.nodes)
    )
    node = proposed_scene.node(element_id)
    footprint = node.keep_out_dimensions or node.physical_dimensions
    violations.extend(_case_bounds_violations(scene, node, footprint))
    if expected is not None and node.mobility == "constrained_xy":
        violations.extend(_control_spacing_violations(proposed_scene, node))
        violations.extend(_control_overlap_violations(proposed_scene, node, footprint))
    return {
        "valid": not violations,
        "element_id": element_id,
        "new_position": [x, y],
        "violations": violations,
        "rules": [
            "mobility=constrained_xy",
            "case bounds using keep-out",
            "control spacing >= 20mm",
            "Feather/USB/keep-out AABB non-overlap",
            "0.1mm payload",
        ],
    }


def _parse_move(raw_move: Any, index: int) -> JsonObject:
    move = cast(JsonObject, raw_move) if isinstance(raw_move, dict) else {}
    if isinstance(move.get("element_id"), str) and move.get("element_id"):
        return move
    raise SceneMoveError(
        {
            "valid": False,
            "violations": [
                _move_violation("malformed_move", f"move {index + 1} must contain an element_id.")
            ],
        },
        index,
    )


def _check_expected_position(current: Any, move: JsonObject, index: int) -> None:
    expected = move.get("expected_position", move.get("old_position"))
    try:
        if not isinstance(expected, (list, tuple)):
            raise ValueError
        expected_values = cast(list[Any] | tuple[Any, ...], expected)
        if len(expected_values) != 2:
            raise ValueError
        if any(isinstance(item, bool) for item in expected_values):
            raise ValueError
        expected_values = tuple(float(item) for item in expected_values)
        if not all(math.isfinite(item) for item in expected_values):
            raise ValueError
        try:
            current_position = current.node(move["element_id"]).transform.position[:2]
        except KeyError:
            return
        if any(abs(current_position[i] - expected_values[i]) > 1e-6 for i in range(2)):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise SceneMoveError(
            {
                "valid": False,
                "element_id": move["element_id"],
                "violations": [
                    _move_violation(
                        "stale_move",
                        f"{move['element_id']} no longer has the expected source position.",
                    )
                ],
            },
            index,
        ) from None


def _apply_validated_move(current: Any, move: JsonObject, index: int) -> tuple[Any, dict[str, Any]]:
    result = validate_scene_move(current, move["element_id"], move.get("new_position"))
    if not result["valid"]:
        raise SceneMoveError(result, index)
    node = current.node(move["element_id"])
    updated = replace(
        node,
        transform=replace(
            node.transform,
            position=(
                result["new_position"][0],
                result["new_position"][1],
                node.transform.position[2],
            ),
        ),
    )
    updated_scene = replace(
        current, nodes=tuple(updated if item.id == node.id else item for item in current.nodes)
    )
    return updated_scene, {"element_id": node.id, "new_position": result["new_position"]}


def apply_scene_moves(scene: Any, moves: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Apply moves sequentially, rejecting stale source positions."""
    if not isinstance(moves, list):
        raise SceneMoveError(
            {
                "valid": False,
                "violations": [
                    _move_violation("malformed_move", "moves must be a list of move objects.")
                ],
            },
            0,
        )
    current = scene
    accepted: list[dict[str, Any]] = []
    for index, raw_move in enumerate(cast(list[Any], moves)):
        move = _parse_move(raw_move, index)
        _check_expected_position(current, move, index)
        current, accepted_move = _apply_validated_move(current, move, index)
        accepted.append(accepted_move)
    return current, accepted


def _revision_moves(canonical_scene: Any, final_scene: Any) -> list[dict[str, Any]]:
    """Return the minimal canonical-to-final move set for one editor revision."""
    records: list[dict[str, Any]] = []
    for control_id in CONTROL_IDS:
        element_id = f"control.{control_id}"
        expected = canonical_scene.node(element_id).transform.position[:2]
        final = final_scene.node(element_id).transform.position[:2]
        if final != expected:
            records.append(
                {
                    "element_id": element_id,
                    "expected_position": list(expected),
                    "new_position": list(final),
                }
            )
    return records


def _revision_move_error(message: str, index: int, element_id: str | None = None) -> SceneMoveError:
    result: dict[str, Any] = {
        "valid": False,
        "violations": [_move_violation("malformed_move", message)],
    }
    if element_id is not None:
        result["element_id"] = element_id
    return SceneMoveError(result, index)


def _parse_revision_moves(scene: Any, moves: Any) -> list[tuple[JsonObject, tuple[float, float]]]:
    """Parse a bounded set with one canonical-to-final record per control."""
    raw_moves = cast(list[Any], moves) if isinstance(moves, list) else []
    if not isinstance(moves, list) or len(raw_moves) > len(CONTROL_IDS):
        raise _revision_move_error(
            f"revision moves must contain at most {len(CONTROL_IDS)} move objects.", 0
        )

    parsed: list[tuple[JsonObject, tuple[float, float]]] = []
    seen: set[str] = set()
    for index, raw_move in enumerate(raw_moves):
        move = _parse_move(raw_move, index)
        element_id = cast(str, move["element_id"])
        if element_id in seen:
            raise _revision_move_error(
                f"revision moves contain duplicate element_id {element_id!r}.",
                index,
                element_id,
            )
        seen.add(element_id)
        _check_expected_position(scene, move, index)
        position = _rounded_position(move.get("new_position"))
        if position is None:
            raise SceneMoveError(_invalid_position_result(element_id), index)
        try:
            expected = scene.node(element_id).transform.position[:2]
        except KeyError:
            result = validate_scene_move(scene, element_id, position)
            raise SceneMoveError(result, index) from None
        if position == expected:
            raise _revision_move_error(
                f"revision move for {element_id} does not change its canonical position.",
                index,
                element_id,
            )
        parsed.append((move, position))
    return parsed


def _scene_with_move_targets(
    scene: Any, parsed: list[tuple[JsonObject, tuple[float, float]]]
) -> Any:
    """Apply all final XY targets at once so transient drag order is irrelevant."""
    targets = {cast(str, move["element_id"]): position for move, position in parsed}
    return replace(
        scene,
        nodes=tuple(
            replace(
                node,
                transform=replace(
                    node.transform,
                    position=(*targets[node.id], node.transform.position[2]),
                ),
            )
            if node.id in targets
            else node
            for node in scene.nodes
        ),
    )


def _apply_revision_moves(scene: Any, moves: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Apply and validate a minimal revision move set against its final scene."""
    parsed = _parse_revision_moves(scene, moves)
    final_scene = _scene_with_move_targets(scene, parsed)
    accepted: list[dict[str, Any]] = []
    for index, (move, position) in enumerate(parsed):
        element_id = cast(str, move["element_id"])
        result = validate_scene_move(final_scene, element_id, position)
        if not result["valid"]:
            raise SceneMoveError(result, index)
        accepted.append({"element_id": element_id, "new_position": list(position)})
    return final_scene, accepted


def _moved_case_source(path: Path, scene: Any, accepted: list[dict[str, Any]]) -> str:
    from cadkit.case_source import rewrite_case_source

    return rewrite_case_source(path, scene, accepted)


def _preview_part_for_moves(
    path: Path,
    scene: Any,
    accepted: list[dict[str, Any]],
    *,
    baseline_part: Any | None = None,
    baseline_placements: Any | None = None,
) -> tuple[Any, Any]:
    source = _moved_case_source(path, scene, accepted)
    with tempfile.TemporaryDirectory(prefix="hf-editor-move-preview-") as directory:
        preview_path = Path(directory) / "case.py"
        preview_path.write_text(source, encoding="utf-8")
        artifacts = _case_artifacts(preview_path)
    if artifacts.assembly is None:
        raise ValueError("moved preview source did not emit ASSEMBLY_SPEC")
    from cadkit.assembly import AssemblyScene

    candidate_scene = AssemblyScene.from_dict(artifacts.assembly)
    canonical_error = _canonical_scene_error(candidate_scene)
    if canonical_error:
        raise ValueError(canonical_error)
    move_error = _accepted_move_error(scene, candidate_scene, accepted)
    if move_error:
        raise ValueError(move_error)
    placements = _prototype_placements(artifacts)
    placements_error = _placements_match_error(candidate_scene, placements)
    if placements_error:
        raise ValueError(placements_error)
    validation_error = _prototype_validation_error(
        artifacts.part,
        placements,
        baseline_part=baseline_part,
        baseline_placements=baseline_placements,
    )
    if validation_error:
        raise ValueError(validation_error)
    return artifacts.part, candidate_scene


def build_layout_baseline(
    path: Path,
    scene: Any,
    moves: list[dict[str, Any]],
) -> tuple[Any, Any]:
    """Execute the accepted layout in isolation with its original exterior."""
    accepted = _moves_with_expected_positions(scene, moves)
    parsed = _parse_revision_moves(scene, accepted)
    layout_scene = _scene_with_move_targets(scene, parsed)
    return _preview_part_for_moves(path, layout_scene, accepted)


def validate_layout_candidate(
    baseline_part: Any, candidate_part: Any, layout_scene: Any
) -> list[Any]:
    """Validate a candidate against the fully moved, accepted layout baseline."""
    from cadkit.constraints import validate_exterior_geometry

    return validate_exterior_geometry(
        baseline_part,
        candidate_part,
        protected_regions=_protected_exterior_regions(layout_scene),
        expected_bounds=baseline_part.bounding_box(),
        tolerance_mm=_EXTERIOR_PREVIEW_TOLERANCE_MM,
    )


_REQUIRED_CANONICAL_NODES = frozenset(
    {
        "case.shell",
        "case.bottom",
        "case.top",
        "electronics.breadboard",
        "feather",
        "feather.header_12",
        "feather.header_16",
        "harness.header_12.connector",
        "harness.header_12.strain_relief",
        "harness.header_12.no_pinch",
        "harness.header_16.connector",
        "harness.header_16.strain_relief",
        "harness.header_16.no_pinch",
        "usb.connector",
        "usb.opening",
        *(f"control.{control_id}" for control_id in CONTROL_IDS),
    }
)
_REQUIRED_CANONICAL_ROUTES = frozenset(
    {
        *(f"wire.control_{control_id}" for control_id in CONTROL_IDS),
        *(f"wire.control_ground_{control_id}" for control_id in CONTROL_IDS),
        ELECTRONICS_BACKBONE_GROUND_BRIDGE_ROUTE_ID,
        "wire.harness.header_12",
        "wire.harness.header_16",
    }
)
_CONTROL_EXPECTATIONS = {
    node_id: (kind, role, component_id)
    for node_id, kind, role, component_id in zip(
        tuple(f"control.{control_id}" for control_id in CONTROL_IDS),
        CONTROL_KINDS,
        CONTROL_ROLES,
        CONTROL_COMPONENT_IDS,
        strict=True,
    )
}
_MOVABLE_CANONICAL_IDS = frozenset(_CONTROL_EXPECTATIONS)
_CANONICAL_NODE_EXPECTATIONS = {
    "case.shell": ("case_shell", "fixed"),
    "case.bottom": ("case_bottom", "derived"),
    "case.top": ("case_top", "derived"),
    "electronics.breadboard": ("half_size_breadboard", "fixed"),
    "feather": ("feather_board", "fixed"),
    "feather.header_12": ("pid2830_header_row", "fixed"),
    "feather.header_16": ("pid2830_header_row", "fixed"),
    "harness.header_12.connector": ("dupont_connector_row", "fixed"),
    "harness.header_12.strain_relief": ("harness_strain_relief", "fixed"),
    "harness.header_12.no_pinch": ("harness_no_pinch_envelope", "fixed"),
    "harness.header_16.connector": ("dupont_connector_row", "fixed"),
    "harness.header_16.strain_relief": ("harness_strain_relief", "fixed"),
    "harness.header_16.no_pinch": ("harness_no_pinch_envelope", "fixed"),
    "usb.connector": ("micro_usb_connector", "fixed"),
    "usb.opening": ("rear_usb_opening", "fixed"),
    **{
        node_id: (kind, "constrained_xy")
        for node_id, (kind, _role, _component_id) in _CONTROL_EXPECTATIONS.items()
    },
}
_CANONICAL_ROUTE_EXPECTATIONS = {
    **{
        f"wire.control_{control_id}": (
            f"control.{control_id}.signal",
            f"electronics.breadboard.signal_{control_id}",
            role,
        )
        for control_id, role in zip(
            CONTROL_IDS,
            CONTROL_ROLES,
            strict=True,
        )
    },
    **{
        f"wire.control_ground_{control_id}": (
            f"control.{control_id}.ground",
            f"electronics.breadboard.gnd_{control_id}",
            "GND",
        )
        for control_id in CONTROL_IDS
    },
    ELECTRONICS_BACKBONE_GROUND_BRIDGE_ROUTE_ID: (
        "feather.gnd",
        "electronics.breadboard.gnd_bridge",
        "GND",
    ),
    "wire.harness.header_12": (
        "harness.header_12.connector.connector",
        "electronics.breadboard.header_12_connector",
        "GND",
    ),
    "wire.harness.header_16": (
        "harness.header_16.connector.connector",
        "electronics.breadboard.header_16_connector",
        "GND",
    ),
}


def _canonical_nodes_error(scene: Any) -> str | None:
    node_ids = {node.id for node in scene.nodes}
    missing = sorted(_REQUIRED_CANONICAL_NODES - node_ids)
    if missing:
        return f"canonical scene is missing required nodes: {', '.join(missing)}"

    return _canonical_controls_error(scene) or _canonical_retired_token_error(scene)


def _canonical_controls_error(scene: Any) -> str | None:
    controls = tuple(node for node in scene.nodes if node.mobility == "constrained_xy")
    if any(node.id not in _MOVABLE_CANONICAL_IDS for node in controls):
        return "canonical scene contains an unauthorized movable node"
    if tuple(node.id for node in controls) != tuple(_CONTROL_EXPECTATIONS):
        return "canonical scene must contain the six ordered constrained_xy controls"
    for node_id, (kind, mobility) in _CANONICAL_NODE_EXPECTATIONS.items():
        node = scene.node(node_id)
        if (node.kind, node.mobility) != (kind, mobility):
            return f"canonical node {node_id} must be kind={kind}, mobility={mobility}"
        expected = _CONTROL_EXPECTATIONS.get(node_id)
        if expected is not None and (
            node.metadata.get("control_role") != expected[1]
            or node.metadata.get("component_id") != expected[2]
        ):
            return f"canonical control {node_id} has an unexpected role or component"


def _canonical_retired_token_error(scene: Any) -> str | None:
    active_text = repr(scene.to_dict()).lower()
    for retired in (
        "dpad",
        "qwiic",
        "i2c",
        "pca9554",
        "b1",
        "b2",
        "b3",
        "b4",
        "b5",
        "b6",
        "b7",
        "b8",
        "spare",
    ):
        if retired in active_text:
            return f"canonical scene contains retired active token: {retired}"


def _canonical_routes_error(scene: Any) -> str | None:
    route_ids = {route.id for route in scene.routes}
    missing_routes = sorted(_REQUIRED_CANONICAL_ROUTES - route_ids)
    if missing_routes:
        return f"canonical scene is missing required routes: {', '.join(missing_routes)}"
    for route in scene.routes:
        expected = _CANONICAL_ROUTE_EXPECTATIONS.get(route.id)
        if expected is not None and (route.source, route.target, route.signal) != expected:
            return f"canonical route {route.id} has unexpected endpoints or signal"
    if scene.route_signal_errors():
        return "canonical scene has invalid route endpoint signals"
    signals = {route.signal for route in scene.routes}
    if signals != {*CONTROL_ROLES, "GND"}:
        return "canonical scene routes must use the six semantic signals and GND"


def _canonical_geometry_error(scene: Any) -> str | None:
    for node_id in (
        "electronics.breadboard",
        "feather",
        "usb.connector",
        "usb.opening",
        "harness.header_12.connector",
        "harness.header_12.strain_relief",
        "harness.header_12.no_pinch",
        "harness.header_16.connector",
        "harness.header_16.strain_relief",
        "harness.header_16.no_pinch",
    ):
        node = scene.node(node_id)
        if node.status != "provisional" or node.metadata.get("mechanical_interface") is not False:
            return f"canonical node {node_id} must remain a provisional logical preview"
    breadboard = scene.node("electronics.breadboard")
    dimensions = breadboard.physical_dimensions
    if (dimensions.x, dimensions.y, dimensions.z) != ELECTRONICS_BACKBONE_PREVIEW_DIMENSIONS_MM:
        return "canonical breadboard preview must retain its operator-reported envelope"
    if (
        breadboard.metadata.get("supplier_sku") != "100058"
        or breadboard.metadata.get("dimension_status")
        != "operator_reported_preview_not_receiving_evidence"
        or breadboard.metadata.get("measurement_date") != "2026-09-01"
        or breadboard.metadata.get("measurement_method") != "ruler"
        or breadboard.metadata.get("measurement_scope") != "installed_stack"
        or breadboard.metadata.get("adhesive_included") is not True
        or breadboard.metadata.get("photo_context") != "top_side_unscaled"
        or breadboard.metadata.get("receiving_evidence") is not False
        or breadboard.metadata.get("placement_status") != "unmeasured_editor_preview_only"
    ):
        return "canonical breadboard preview must remain explicitly provisional"


def _canonical_scene_error(scene: Any) -> str | None:
    return (
        _canonical_nodes_error(scene)
        or _canonical_routes_error(scene)
        or _canonical_geometry_error(scene)
    )


def _accepted_move_error(
    feedback_scene: Any, candidate_scene: Any, moves: list[dict[str, Any]]
) -> str | None:
    if candidate_scene.electronics_backbone != feedback_scene.electronics_backbone:
        return "candidate changed protected ElectronicsBackbone evidence"
    moved_ids = {move["element_id"] for move in moves}
    expected_targets = _accepted_move_targets(moves)
    for control_id in CONTROL_IDS:
        element_id = f"control.{control_id}"
        expected = feedback_scene.node(element_id).transform.position[:2]
        actual = candidate_scene.node(element_id).transform.position[:2]
        target = expected_targets.get(element_id)
        if target is not None:
            error = _accepted_target_error(element_id, actual, target)
            if error:
                return error
            continue
        if any(abs(actual[i] - expected[i]) > 1e-9 for i in range(2)):
            if element_id in moved_ids:
                return f"candidate ignored accepted move for {element_id}: expected XY {list(expected)}, got {list(actual)}"
            return f"{element_id} changed without a validated 2D drag"
    return None


def _accepted_move_targets(moves: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    return {
        move["element_id"]: tuple(move["new_position"])
        for move in moves
        if isinstance(move.get("new_position"), (list, tuple)) and len(move["new_position"]) == 2
    }


def _accepted_target_error(
    element_id: str, actual: tuple[float, float, float], target: tuple[float, float]
) -> str | None:
    if any(abs(actual[i] - target[i]) > 1e-9 for i in range(2)):
        return (
            f"candidate ignored accepted move for {element_id}: "
            f"expected XY {list(target)}, got {list(actual[:2])}"
        )
    return None


def _direct_named_call(value: ast.AST | None, name: str) -> ast.Call | None:
    if not isinstance(value, ast.Call):
        return None
    return value if isinstance(value.func, ast.Name) and value.func.id == name else None


def _module_assignment(tree: ast.Module, name: str) -> ast.expr | None:
    value: ast.expr | None = None
    for statement in tree.body:
        if isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and statement.target.id == name:
                value = statement.value
        elif isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in statement.targets
        ):
            value = statement.value
    return value


def _module_direct_call(tree: ast.Module, name: str, call_name: str) -> ast.Call | None:
    return _direct_named_call(_module_assignment(tree, name), call_name)


def _has_unique_named_keyword(call: ast.Call, keyword: str, value: str) -> bool:
    matches = [
        item
        for item in call.keywords
        if item.arg == keyword and isinstance(item.value, ast.Name) and item.value.id == value
    ]
    return len(matches) == 1


def _has_raw_build123d_import(tree: ast.Module) -> bool:
    allowed = {"BuildPart", "Location", "Locations", "Mode", "Part", "add"}
    module_statements = {id(node) for node in tree.body}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("build123d")
            and (
                id(node) not in module_statements
                or node.module != "build123d"
                or any(
                    alias.name not in allowed or alias.asname is not None for alias in node.names
                )
            )
        ):
            return True
        if isinstance(node, ast.Import) and any(
            alias.name == "build123d" or alias.name.startswith("build123d.") for alias in node.names
        ):
            return True
    return False


def _imported_binding(alias: ast.alias, *, from_import: bool) -> str:
    return alias.asname or (alias.name if from_import else alias.name.split(".", 1)[0])


def _is_canonical_cadkit_import(
    node: ast.ImportFrom,
    alias: ast.alias,
    name: str,
    module_statements: set[int],
) -> bool:
    return (
        id(node) in module_statements
        and node.module == "cadkit"
        and alias.name == name
        and alias.asname is None
    )


def _from_import_binding_count(
    node: ast.ImportFrom,
    name: str,
    module_statements: set[int],
) -> int | None:
    if any(alias.name == "*" for alias in node.names):
        return None
    matching = [alias for alias in node.names if _imported_binding(alias, from_import=True) == name]
    if any(
        not _is_canonical_cadkit_import(node, alias, name, module_statements) for alias in matching
    ):
        return None
    return len(matching)


def _cadkit_import_count_or_conflict(tree: ast.Module, name: str) -> int | None:
    count = 0
    module_statements = {id(statement) for statement in tree.body}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            binding_count = _from_import_binding_count(node, name, module_statements)
            if binding_count is None:
                return None
            count += binding_count
        elif isinstance(node, ast.Import) and any(
            _imported_binding(alias, from_import=False) == name for alias in node.names
        ):
            return None
    return count


def _attribute_root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _source_shadows_name(tree: ast.Module, name: str) -> bool:
    return any(
        (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
        )
        or (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        )
        or (isinstance(node, ast.arg) and node.arg == name)
        or (isinstance(node, ast.ExceptHandler) and node.name == name)
        or (isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name)
        or (isinstance(node, ast.Attribute) and _attribute_root_name(node) == name)
        for node in ast.walk(tree)
    )


def _has_unshadowed_cadkit_binding(tree: ast.Module, name: str) -> bool:
    return _cadkit_import_count_or_conflict(tree, name) == 1 and not _source_shadows_name(
        tree, name
    )


def _main_assignment_call(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    targets: tuple[str, str],
    call_name: str,
) -> ast.Call | None:
    for statement in function.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, (ast.Tuple, ast.List)) or [
            item.id if isinstance(item, ast.Name) else None for item in target.elts
        ] != list(targets):
            continue
        return _direct_named_call(statement.value, call_name)
    return None


def _has_dynamic_geometry_binding(tree: ast.Module) -> bool:
    dynamic_names = {
        "__import__",
        "delattr",
        "eval",
        "exec",
        "globals",
        "locals",
        "setattr",
        "vars",
    }
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in dynamic_names)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")
        )
        for node in ast.walk(tree)
    )


def _protected_geometry_binding_error(tree: ast.Module) -> str | None:
    if _has_raw_build123d_import(tree):
        return "candidate source may not recreate snap geometry with raw build123d primitives"
    if _has_dynamic_geometry_binding(tree):
        return "candidate source may not dynamically replace protected geometry bindings"
    if not _has_unshadowed_cadkit_binding(tree, "gamepad_body"):
        return "candidate source must retain the unshadowed cadkit gamepad_body import"
    if not _has_unshadowed_cadkit_binding(tree, "snap_fit_pair"):
        return "candidate source must retain the unshadowed cadkit snap_fit_pair import"
    if not _has_unshadowed_cadkit_binding(tree, "orient_case_halves_for_print"):
        return "candidate source must retain authoritative print orientation"
    return None


def _authoritative_main(tree: ast.Module) -> ast.FunctionDef | None:
    functions = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    ]
    return functions[0] if len(functions) == 1 else None


def _snap_call_contract_error(tree: ast.Module, main_function: ast.FunctionDef) -> str | None:
    snap_calls = [
        call
        for node in ast.walk(tree)
        if (call := _direct_named_call(node, "snap_fit_pair")) is not None
    ]
    if len(snap_calls) != 1:
        return "candidate source must retain exactly one authoritative snap_fit_pair call"
    snap_call = _main_assignment_call(
        main_function,
        targets=("assembly_top", "assembly_bottom"),
        call_name="snap_fit_pair",
    )
    if snap_call is None:
        return "candidate source must apply snap_fit_pair to the exported shell halves"
    if (
        len(snap_call.args) != 1
        or not isinstance(snap_call.args[0], ast.Name)
        or snap_call.args[0].id != "case_for_main"
        or bool(snap_call.keywords)
    ):
        return "candidate source may not parameterize authoritative snap-fit geometry"
    return None


def _print_orientation_contract_error(
    tree: ast.Module, main_function: ast.FunctionDef
) -> str | None:
    orientation_calls = [
        call
        for node in ast.walk(tree)
        if (call := _direct_named_call(node, "orient_case_halves_for_print")) is not None
    ]
    orientation_call = _main_assignment_call(
        main_function,
        targets=("top", "bottom"),
        call_name="orient_case_halves_for_print",
    )
    actual_arguments = (
        [
            argument.id if isinstance(argument, ast.Name) else None
            for argument in orientation_call.args
        ]
        if orientation_call is not None
        else []
    )
    if (
        len(orientation_calls) != 1
        or orientation_call is None
        or actual_arguments != ["assembly_top", "assembly_bottom"]
        or bool(orientation_call.keywords)
    ):
        return "candidate source must retain authoritative broad-face-down print orientation"
    return None


def _authoritative_geometry_contract_error(
    tree: ast.Module, body_calls: list[ast.Call]
) -> str | None:
    if len(body_calls) != 1:
        return "candidate build_case must retain exactly one direct gamepad_body call"
    body_call = body_calls[0]
    if not _has_unique_named_keyword(body_call, "fillet_radius_mm", "FILLET_RADIUS_MM"):
        return "candidate build_case must pass FILLET_RADIUS_MM as gamepad_body fillet_radius_mm"
    required_body_keywords = (
        ("length_mm", "CASE_LENGTH_MM"),
        ("width_mm", "CASE_WIDTH_MM"),
        ("thickness_mm", "CASE_THICKNESS_MM"),
        ("feather_variant", "FEATHER_VARIANT"),
        ("exterior_design", "EXTERIOR_DESIGN"),
    )
    if any(
        not _has_unique_named_keyword(body_call, name, value)
        for name, value in required_body_keywords
    ):
        return (
            "candidate gamepad_body must retain canonical dimensions, fillet, Feather, "
            "and EXTERIOR_DESIGN bindings"
        )
    if any(keyword.arg == "wall_thickness_mm" for call in body_calls for keyword in call.keywords):
        return "candidate source may not override the authoritative shell wall thickness"
    if binding_error := _protected_geometry_binding_error(tree):
        return binding_error
    main_function = _authoritative_main(tree)
    if main_function is None:
        return "candidate source must retain exactly one main function"
    return _snap_call_contract_error(tree, main_function) or _print_orientation_contract_error(
        tree, main_function
    )


def _assembly_contract_error(tree: ast.Module) -> str | None:
    assembly_call = _module_direct_call(tree, "ASSEMBLY_SPEC", "build_demo_assembly")
    if assembly_call is None:
        return "candidate ASSEMBLY_SPEC must retain the direct build_demo_assembly call"
    dimensions = assembly_call.args[0] if len(assembly_call.args) == 1 else None
    canonical_dimensions = isinstance(dimensions, ast.Tuple) and [
        item.id for item in dimensions.elts if isinstance(item, ast.Name)
    ] == ["CASE_LENGTH_MM", "CASE_WIDTH_MM", "CASE_THICKNESS_MM"]
    if not canonical_dimensions:
        return "candidate ASSEMBLY_SPEC must retain canonical case dimensions"
    if not _has_unique_named_keyword(assembly_call, "case_fillet_radius_mm", "FILLET_RADIUS_MM"):
        return "candidate ASSEMBLY_SPEC must pass FILLET_RADIUS_MM as case_fillet_radius_mm"
    if not _has_unique_named_keyword(assembly_call, "buttons", "CONTROLS"):
        return (
            "candidate ASSEMBLY_SPEC must retain canonical CONTROLS and FILLET_RADIUS_MM bindings"
        )
    if {item.arg for item in assembly_call.keywords} != {
        "buttons",
        "case_fillet_radius_mm",
    }:
        return "candidate ASSEMBLY_SPEC may not parameterize protected assembly geometry"
    return None


def _usb_contract_error(build_case: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    calls = [
        call
        for node in ast.walk(build_case)
        if (call := _direct_named_call(node, "shared_usb_opening")) is not None
    ]
    if len(calls) != 1:
        return "candidate build_case must retain exactly one shared USB opening call"
    call = calls[0]
    argument = call.args[0] if len(call.args) == 1 else None
    canonical_argument = isinstance(argument, ast.Tuple) and [
        item.attr
        for item in argument.elts
        if isinstance(item, ast.Attribute)
        and isinstance(item.value, ast.Name)
        and item.value.id == "opening_dimensions"
    ] == ["x", "y", "z"]
    if not canonical_argument:
        return "candidate build_case must retain the canonical shared USB opening dimensions"
    if call.keywords:
        return "candidate build_case may not parameterize the shared USB opening"
    return None


def _build_case_contract_error(tree: ast.Module) -> str | None:
    build_cases = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "build_case"
    ]
    if len(build_cases) != 1:
        return "candidate source must retain exactly one build_case function"
    build_case = build_cases[0]
    if usb_error := _usb_contract_error(build_case):
        return usb_error
    body_calls = [
        call
        for node in ast.walk(build_case)
        if (call := _direct_named_call(node, "gamepad_body")) is not None
    ]
    placements_call = _module_direct_call(tree, "PLACEMENTS", "placements_from_assembly")
    if placements_call is None or len(placements_call.args) != 1 or placements_call.keywords:
        return "candidate PLACEMENTS must be derived from ASSEMBLY_SPEC"
    if (
        not isinstance(placements_call.args[0], ast.Name)
        or placements_call.args[0].id != "ASSEMBLY_SPEC"
    ):
        return "candidate PLACEMENTS must be derived exactly from ASSEMBLY_SPEC"
    case_call = _module_direct_call(tree, "case", "build_case")
    if case_call is None or case_call.args or case_call.keywords:
        return "candidate case must be the direct build_case result"
    return _authoritative_geometry_contract_error(tree, body_calls)


def _source_contract_error(path: Path) -> str | None:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return f"candidate source is not valid Python: {exc}"
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    missing = sorted(
        {"CONTROLS", "EXTERIOR_DESIGN", "ASSEMBLY_SPEC", "PLACEMENTS", "build_case", "case"} - names
    )
    if missing:
        return f"candidate source is missing required names: {', '.join(missing)}"
    try:
        from cadkit.case_source import inspect_exterior_design_source

        inspect_exterior_design_source(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"candidate source must retain a literal EXTERIOR_DESIGN binding: {exc}"
    if assembly_error := _assembly_contract_error(tree):
        return assembly_error
    return _build_case_contract_error(tree)


def _resolve_case_path(value: str) -> Path | None:
    session = Path(__file__).resolve().parents[2]
    candidate = session / value
    if candidate.name != "case.py" or candidate.is_symlink():
        return None
    target = candidate.resolve()
    roots = ((session / "examples").resolve(), (session / "out").resolve())
    if target.name != "case.py" or target.is_symlink() or not target.is_file():
        return None
    return target if any(root == target or root in target.parents for root in roots) else None


def _work_dir_for(case_path: str | Path, submit_id: str) -> Path:
    path = Path(case_path)
    return _SESSION_DIR / "examples" / path.parent.name / "submits" / submit_id


def _case_artifacts(path: Path) -> Any:
    from cadkit.case_execution import execute_case_artifacts

    return execute_case_artifacts(path)


def _editor_revision(case_path: Path, scene: Any, part: Any) -> str:
    """Return a stable revision for one validated source/scene/geometry triple."""
    payload = {
        "case_sha256": hashlib.sha256(_read_case_bytes(case_path)).hexdigest(),
        "assembly": scene.to_dict(),
        "part_sha256": hashlib.sha256(tessellate_part(part)).hexdigest(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_with_revision(case_path: Path, scene: Any, part: Any) -> dict[str, Any]:
    from cadkit.manifest import from_case_path

    manifest = from_case_path(case_path, scene.to_dict())
    manifest["editor_revision"] = _editor_revision(case_path, scene, part)
    return manifest


def _requested_prototype_moves() -> Any:
    raw = request.args.get("moves", "")
    if not raw:
        return []
    if len(raw.encode("utf-8")) > MAX_PROTOTYPE_MOVES_BYTES:
        raise ValueError("STL moves exceed the size limit")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("STL moves must be valid JSON") from exc


def _requested_prototype_geometry(
    case_path: Path, artifacts: Any, part: Any, scene: Any
) -> tuple[Any, Any, Any]:
    moved_scene, accepted = _apply_revision_moves(scene, _requested_prototype_moves())
    if not accepted:
        return part, scene, _prototype_placements(artifacts)
    moved_part, moved_scene = _preview_part_for_moves(
        case_path,
        moved_scene,
        accepted,
        baseline_part=part,
        baseline_placements=_prototype_placements(artifacts),
    )
    from cadkit.assembly import placements_from_assembly

    return moved_part, moved_scene, placements_from_assembly(moved_scene)


def _render_case_glb(part: Any, scene: Any | None) -> bytes:
    if scene is None:
        payload = tessellate_part(part)
    else:
        from .gltf_scene import build_assembled_glb

        payload = build_assembled_glb(scene, case_part=part)
    from .gltf_scene import MAX_GLB_BYTES

    if len(payload) > MAX_GLB_BYTES:
        raise ValueError(f"GLB exceeds the {MAX_GLB_BYTES} byte limit")
    return payload


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    app.add_url_rule("/health", "health", _health, methods=["GET"])
    app.add_url_rule("/", "index", _index, methods=["GET"])
    app.add_url_rule("/manifest", "manifest", _manifest, methods=["GET"])
    app.add_url_rule("/stl/<part_name>", "student_stl", _prototype_stl, methods=["GET"])
    app.add_url_rule("/validate-move", "validate_move", _validate_move, methods=["POST"])
    app.add_url_rule("/snapshot", "snapshot", _snapshot, methods=["POST"])
    app.add_url_rule("/reset", "reset", _reset, methods=["POST"])
    app.add_url_rule("/submit", "submit", _submit, methods=["POST"])
    return app


def _health() -> Any:
    from cadkit.case_execution import sandbox_status

    return jsonify({"status": "ok", "sandbox": sandbox_status().to_dict()})


def _index() -> Any:
    from .frontend import MINIMAL_HTML

    response = Response(MINIMAL_HTML, mimetype="text/html")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _manifest() -> Any:
    from cadkit.manifest import from_case_path, has_assembly_spec

    value = request.args.get("case_path", "").strip()
    if not value:
        return jsonify({"error": "missing 'case_path' query parameter"}), 400
    path = _resolve_case_path(value)
    if path is None:
        return jsonify({"error": f"case.py not found: {value}"}), 404
    with _case_lock(path):
        try:
            if has_assembly_spec(path):
                artifacts = _case_artifacts(path)
                if artifacts.assembly is None:
                    raise ValueError("canonical ASSEMBLY_SPEC did not emit a scene artifact")
                from cadkit.assembly import AssemblyScene

                scene = AssemblyScene.from_dict(artifacts.assembly)
                canonical_error = _canonical_scene_error(scene)
                if canonical_error:
                    raise ValueError(canonical_error)
                manifest = _manifest_with_revision(path, scene, artifacts.part)
            else:
                manifest = from_case_path(path)
        except Exception as exc:
            from cadkit.case_execution import SandboxUnavailable

            if isinstance(exc, SandboxUnavailable):
                return jsonify({"status": "sandbox_unavailable", "error": str(exc)}), 503
            return jsonify({"error": str(exc)}), 400
    return jsonify(manifest)


def _prototype_stl(part_name: str) -> Any:
    """Download one revision-bound, validated shell STL."""
    if part_name not in {"top", "bottom"}:
        return jsonify({"error": "part_name must be 'top' or 'bottom'"}), 404
    case_value = request.args.get("case_path", "").strip()
    revision = request.args.get("revision", "").strip()
    if not case_value:
        return jsonify({"error": "missing 'case_path' query parameter"}), 400
    if not revision:
        return jsonify({"error": "missing 'revision' query parameter"}), 400
    if len(revision) != 64 or any(character not in "0123456789abcdef" for character in revision):
        return jsonify({"error": "revision must be a lowercase SHA-256 digest"}), 400
    case_path = _resolve_case_path(case_value)
    if case_path is None:
        return jsonify({"error": f"case.py not found: {case_value}"}), 404

    try:
        with _case_lock(case_path):
            artifacts = _case_artifacts(case_path)
            part, scene = _scene_from_artifacts(case_path, artifacts)
            if scene is None:
                return jsonify({"error": "STL requires ASSEMBLY_SPEC"}), 409
            part, scene, placements = _requested_prototype_geometry(
                case_path, artifacts, part, scene
            )
            current_revision = _editor_revision(case_path, scene, part)
            if revision != current_revision:
                return jsonify(
                    {
                        "status": "stale_revision",
                        "error": "STL revision is stale; reload the editor",
                        "editor_revision": current_revision,
                    }
                ), 409
            placements_error = _placements_match_error(scene, placements)
            if placements_error:
                raise ValueError(placements_error)
            validation_error = _prototype_validation_error(part, placements)
            if validation_error:
                raise ValueError(validation_error)
            from cadkit import orient_case_halves_for_print, snap_fit_pair
            from cadkit.render import export_stl_part

            assembly_top, assembly_bottom = snap_fit_pair(part)
            top, bottom = orient_case_halves_for_print(assembly_top, assembly_bottom)
            _validate_prototype_half("top", top)
            _validate_prototype_half("bottom", bottom)
            selected = top if part_name == "top" else bottom
            with tempfile.TemporaryDirectory(prefix="hf-editor-prototype-stl-") as directory:
                output = export_stl_part(selected, Path(directory) / f"case-{part_name}.stl")
                data = output.read_bytes()
            if not data:
                raise ValueError("STL export produced empty STL output")
    except Exception as exc:
        from cadkit.case_execution import SandboxUnavailable

        if isinstance(exc, SandboxUnavailable):
            return jsonify({"status": "sandbox_unavailable", "error": str(exc)}), 503
        return jsonify({"error": f"failed to export STL: {exc}"}), 400

    response = send_file(
        io.BytesIO(data),
        mimetype="model/stl",
        as_attachment=True,
        download_name=f"case-{part_name}.stl",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-HF-Editor-Revision"] = current_revision
    response.headers["X-HF-Artifact-Status"] = "validated"
    return response


def _scene_from_artifacts(path: Path, artifacts: Any) -> tuple[Any, Any | None]:
    scene = None
    if artifacts.assembly is not None:
        from cadkit.assembly import AssemblyScene

        scene = AssemblyScene.from_dict(artifacts.assembly)
        canonical_error = _canonical_scene_error(scene)
        if canonical_error:
            raise ValueError(canonical_error)
    else:
        from cadkit.manifest import has_assembly_spec

        if has_assembly_spec(path):
            raise ValueError("canonical ASSEMBLY_SPEC did not emit a scene artifact")
    return artifacts.part, scene


def _scene_for_case(path: Path) -> tuple[Any, Any | None]:
    return _scene_from_artifacts(path, _case_artifacts(path))


def _prototype_placements(artifacts: Any) -> Any:
    from cadkit.constraints import Placements

    placements = getattr(artifacts, "placements", None)
    if not isinstance(placements, Placements):
        raise ValueError("STL requires a structured placements artifact")
    return placements


def _protected_exterior_regions(
    scene: Any,
    *,
    moved_control_ids: set[str] | frozenset[str] = frozenset(),
) -> tuple[Any, ...]:
    """Build bounded Part regions from the canonical scene for exterior checks.

    Scene positions are case-local lower-left coordinates.  The only frame
    conversion here is the canonical ``case_local_to_build123d`` adapter; no
    receiving-controlled dimension is invented by the editor.
    """
    from cadkit.snap_geometry import AUTHORITATIVE_SNAP_GEOMETRY

    del moved_control_ids
    length, width, height, ligament, region_box = _protected_region_context(scene)

    protected = _control_protected_regions(
        scene,
        region_box,
        height,
        ligament,
    )

    snap = AUTHORITATIVE_SNAP_GEOMETRY
    split_z = min(height / 2.0, snap.max_lower_shell_height_mm)
    half_depth = snap.clip_depth_mm / 2.0
    root_ys = (
        snap.wall_thickness_mm + half_depth - snap.root_overlap_mm,
        width - snap.wall_thickness_mm - half_depth + snap.root_overlap_mm,
    )
    protected.extend(_snap_protected_regions(region_box, snap, length, root_ys, split_z))

    protected.extend(_usb_protected_regions(scene, region_box, ligament, snap))
    protected.extend(_adhesive_protected_regions(scene, region_box, snap.wall_thickness_mm))

    fixed_ids = (
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
    )
    protected.extend(_fixed_electronics_regions(scene, region_box, fixed_ids))

    routes = tuple(scene.routes)
    required_routes = {
        *(f"wire.control_{control_id}" for control_id in CONTROL_IDS),
        *(f"wire.control_ground_{control_id}" for control_id in CONTROL_IDS),
        "wire.feather_ground_rail",
        "wire.harness.header_12",
        "wire.harness.header_16",
    }
    if not routes or not required_routes <= {route.id for route in routes}:
        raise ValueError("canonical protected routes are unavailable")
    protected.extend(_route_protected_regions(scene, region_box, routes))
    return tuple(protected)


def _protected_region_context(scene: Any) -> tuple[float, float, float, float, Any]:
    from cadkit.constraints import MIN_LIGAMENT_MM

    dimensions = scene.case_dimensions
    shell = scene.node("case.shell")
    if shell.physical_dimensions != dimensions:
        raise ValueError("canonical shell dimensions do not match scene dimensions")
    length, width, height = dimensions.x, dimensions.y, dimensions.z
    if not all(
        math.isfinite(value) and 0.0 < value <= 1_000.0 for value in (length, width, height)
    ):
        raise ValueError("canonical case dimensions are unavailable or out of bounds")
    ligament = float(MIN_LIGAMENT_MM)
    return length, width, height, ligament, partial(_build_protected_box, dimensions)


def _build_protected_box(
    dimensions: Any,
    name: str,
    center: tuple[float, float, float],
    size: tuple[float, float, float],
    *,
    rotation_deg: float = 0.0,
) -> Any:
    from build123d import Align, Box, Location

    if len(center) != 3 or len(size) != 3:
        raise ValueError(f"protected region {name!r} has malformed dimensions")
    if any(not math.isfinite(value) for value in (*center, *size)):
        raise ValueError(f"protected region {name!r} has non-finite geometry")
    if any(value <= 0.0 or value > 1_000.0 for value in size):
        raise ValueError(f"protected region {name!r} has out-of-bounds dimensions")
    if not math.isfinite(rotation_deg):
        raise ValueError(f"protected region {name!r} has a non-finite rotation")
    from cadkit.assembly import case_local_to_build123d

    frame_center = case_local_to_build123d(center, dimensions)
    return Location(frame_center, (0.0, 0.0, rotation_deg)) * Box(
        *size,
        align=(Align.CENTER, Align.CENTER, Align.CENTER),
    )


def _named_protected_region(name: str, region: Any) -> Any:
    from cadkit.exterior_design import ProtectedExteriorRegion

    return ProtectedExteriorRegion(name, region)


def _control_protected_regions(
    scene: Any,
    region_box: Any,
    height: float,
    ligament: float,
) -> list[Any]:
    protected: list[Any] = []
    for control_id in CONTROL_IDS:
        element_id = f"control.{control_id}"
        node = scene.node(element_id)
        cutout = node.metadata.get("cutout_diameter_mm")
        land = node.metadata.get("mounting_land_diameter_mm")
        if isinstance(cutout, bool) or not isinstance(cutout, (int, float)):
            raise ValueError(f"{element_id} has no bounded cutout metadata")
        if isinstance(land, bool) or not isinstance(land, (int, float)):
            raise ValueError(f"{element_id} has no bounded mounting-land metadata")
        diameter = max(float(cutout), float(land)) + 2.0 * ligament
        name = f"{element_id}.land_opening_ligament"
        protected.append(
            _named_protected_region(
                name, region_box(name, node.transform.position, (diameter, diameter, height))
            )
        )
    return protected


def _snap_protected_regions(
    region_box: Any,
    snap: Any,
    length: float,
    root_ys: tuple[float, float],
    split_z: float,
) -> list[Any]:
    protected: list[Any] = []
    for x_index, x_fraction in enumerate(snap.x_fractions):
        if not 0.0 < x_fraction < 1.0:
            raise ValueError("authoritative snap-root fraction is unavailable")
        for y_index, y in enumerate(root_ys):
            name = f"snap.root.{x_index}.{y_index}.split_attachment"
            protected.append(
                _named_protected_region(
                    name,
                    region_box(
                        name,
                        (length * x_fraction, y, split_z),
                        (
                            snap.clip_length_mm + 2.0 * snap.wall_thickness_mm,
                            snap.clip_depth_mm + 2.0 * snap.wall_thickness_mm,
                            snap.clip_height_mm + 2.0 * snap.wall_thickness_mm,
                        ),
                    ),
                )
            )
    return protected


def _usb_protected_regions(scene: Any, region_box: Any, ligament: float, snap: Any) -> list[Any]:
    usb = scene.node("usb.opening")
    usb_size = usb.physical_dimensions
    name = "usb.opening_ligament"
    return [
        _named_protected_region(
            name,
            region_box(
                name,
                usb.transform.position,
                (
                    usb_size.x + 2.0 * ligament,
                    usb_size.y + 2.0 * ligament,
                    usb_size.z + snap.wall_thickness_mm,
                ),
                rotation_deg=usb.transform.rotation_deg[2],
            ),
        )
    ]


def _adhesive_protected_regions(scene: Any, region_box: Any, wall_thickness: float) -> list[Any]:
    backbone = scene.node("electronics.breadboard")
    if (
        backbone.metadata.get("supplier_sku") != "100058"
        or backbone.metadata.get("bond_surface") != "inside_bottom_shell"
        or backbone.metadata.get("adhesive_included") is not True
    ):
        raise ValueError("canonical adhesive/backbone footprint metadata is unavailable")
    board_size = backbone.physical_dimensions
    name = "adhesive.backbone_footprint"
    return [
        _named_protected_region(
            name,
            region_box(
                name,
                (
                    backbone.transform.position[0],
                    backbone.transform.position[1],
                    wall_thickness / 2.0,
                ),
                (board_size.x, board_size.y, wall_thickness),
                rotation_deg=backbone.transform.rotation_deg[2],
            ),
        )
    ]


def _fixed_electronics_regions(scene: Any, region_box: Any, node_ids: tuple[str, ...]) -> list[Any]:
    protected: list[Any] = []
    for node_id in node_ids:
        node = scene.node(node_id)
        size = node.keep_out_dimensions or node.physical_dimensions
        name = f"electronics.{node_id}.keepout"
        protected.append(
            _named_protected_region(
                name,
                region_box(
                    name,
                    node.transform.position,
                    (size.x, size.y, size.z),
                    rotation_deg=node.transform.rotation_deg[2],
                ),
            )
        )
    return protected


def _route_protected_regions(scene: Any, region_box: Any, routes: tuple[Any, ...]) -> list[Any]:
    protected: list[Any] = []
    for route in routes:
        waypoints = scene.route_waypoints(route)
        if (
            len(waypoints) < 2
            or not route.protected
            or not math.isfinite(route.min_bend_radius_mm)
            or route.min_bend_radius_mm <= 0.0
        ):
            raise ValueError(f"protected route {route.id!r} is malformed")
        corridor = max(1.0, float(route.min_bend_radius_mm))
        protected.extend(_route_segment_regions(route, waypoints, corridor, region_box))
    return protected


def _route_segment_regions(
    route: Any,
    waypoints: tuple[tuple[float, float, float], ...],
    corridor: float,
    region_box: Any,
) -> list[Any]:
    regions: list[Any] = []
    for segment_index, (start, end) in enumerate(itertools.pairwise(waypoints)):
        center = tuple((start[index] + end[index]) / 2.0 for index in range(3))
        horizontal_length = math.hypot(end[0] - start[0], end[1] - start[1])
        rotation_deg = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
        size = (
            max(horizontal_length + 2.0 * corridor, 1.0),
            max(2.0 * corridor, 1.0),
            max(abs(end[2] - start[2]) + 2.0 * corridor, 1.0),
        )
        name = f"route.{route.id}.{segment_index}.corridor"
        regions.append(
            _named_protected_region(
                name,
                region_box(
                    name,
                    cast(tuple[float, float, float], center),
                    size,
                    rotation_deg=rotation_deg,
                ),
            )
        )
    return regions


def _placements_match_error(scene: Any, placements: Any) -> str | None:
    from cadkit.assembly import placements_from_assembly

    expected = placements_from_assembly(scene)
    if placements.to_dict() != expected.to_dict():
        return "candidate PLACEMENTS must be derived exactly from candidate ASSEMBLY_SPEC"
    return None


_GRANDFATHERED_PREVIEW_RULES = frozenset({"keepout_overlap", "route_intersects_volume"})


def _violation_signature(violation: Any) -> tuple[str, str, str, str, str]:
    return (
        str(getattr(violation, "category", "")),
        str(getattr(violation, "rule", "")),
        str(getattr(violation, "severity", "")),
        str(getattr(violation, "message", "")),
        str(getattr(violation, "location", "")),
    )


def _prototype_errors_are_grandfathered(candidate: list[Any], baseline: list[Any]) -> bool:
    """Allow only exact unchanged instances of the two known preview findings."""
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
        return False
    for violation in candidate:
        if getattr(violation, "severity", None) != "error":
            continue
        if getattr(violation, "rule", None) not in _GRANDFATHERED_PREVIEW_RULES:
            return False
    return True


def _prototype_validation_error(
    part: Any,
    placements: Any,
    *,
    baseline_part: Any | None = None,
    baseline_placements: Any | None = None,
) -> str | None:
    from cadkit import constraints

    analysis: Any = constraints.validate(
        part,
        placements=placements,
        require_export_verification=False,
        whole_case_geometry=True,
    )
    if not isinstance(analysis, list):
        return "STL validation analysis is missing or malformed"
    violations = cast(list[Any], analysis)
    if any(not isinstance(violation, constraints.ConstraintViolation) for violation in violations):
        return "STL validation analysis is missing or malformed"
    if baseline_part is None:
        blocking = constraints.has_blocking_prototype_violations(
            cast(list[constraints.ConstraintViolation], violations)
        )
    else:
        baseline_analysis: Any = constraints.validate(
            baseline_part,
            placements=baseline_placements if baseline_placements is not None else placements,
            require_export_verification=False,
            whole_case_geometry=True,
        )
        if not isinstance(baseline_analysis, list):
            return "prototype baseline validation analysis is missing or malformed"
        baseline_items = cast(list[Any], baseline_analysis)
        if any(not isinstance(item, constraints.ConstraintViolation) for item in baseline_items):
            return "prototype baseline validation analysis is missing or malformed"
        blocking = not _prototype_errors_are_grandfathered(
            violations, cast(list[constraints.ConstraintViolation], baseline_items)
        )
    if blocking:
        return "STL validation has fatal constraint violations"
    return None


def _validate_prototype_half(part_name: str, half: Any) -> None:
    try:
        solids = half.solids()
        solid_count = len(solids)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"prototype {part_name} half must contain exactly one solid") from exc
    if solid_count != 1:
        raise ValueError(f"prototype {part_name} half must contain exactly one solid")
    try:
        raw_volume: Any = solids[0].volume
        volume = raw_volume() if callable(raw_volume) else raw_volume
        volume = float(cast(Any, volume))
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"prototype {part_name} half must have positive finite volume") from exc
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError(f"prototype {part_name} half must have positive finite volume")


def _validate_move() -> Any:
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    body = cast(JsonObject, raw)
    if "case" in body or "assembly" in body:
        return jsonify(
            {"error": "inline case and assembly payloads are not accepted; use case_path"}
        ), 400
    value = body.get("case_path")
    if not isinstance(value, str) or not value.strip():
        return jsonify({"error": "missing 'case_path'"}), 400
    path = _resolve_case_path(value.strip())
    if path is None:
        return jsonify({"error": f"case.py not found: {value}"}), 404
    moves = body.get("moves")
    with _case_lock(path):
        try:
            part, scene = _scene_for_case(path)
            if scene is None:
                return jsonify(
                    {
                        "valid": False,
                        "violations": [
                            _move_violation(
                                "canonical_scene_required",
                                "Move validation requires ASSEMBLY_SPEC.",
                            )
                        ],
                    }
                ), 409
            final_scene, accepted = apply_scene_moves(scene, moves)
            if accepted:
                preview_part, final_scene = _preview_part_for_moves(
                    path,
                    final_scene,
                    accepted,
                )
            else:
                preview_part = part
                final_scene = scene
            glb = _render_case_glb(preview_part, final_scene)
            editor_revision = _editor_revision(path, final_scene, preview_part)
            revision_moves = _revision_moves(scene, final_scene)
        except SceneMoveError as exc:
            result = dict(exc.result)
            result["move_index"] = exc.index
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": f"failed to load canonical scene: {exc}"}), 400
    response: dict[str, Any] = {
        "valid": True,
        "accepted_moves": accepted,
        "scene": final_scene.to_dict(),
        "glb_b64": base64.b64encode(glb).decode("ascii"),
        "editor_revision": editor_revision,
        "revision_moves": revision_moves,
    }
    if accepted:
        response["element_id"] = accepted[-1]["element_id"]
        response["new_position"] = accepted[-1]["new_position"]
    return jsonify(response)


def _snapshot() -> Any:
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    body = cast(JsonObject, raw)
    if "case" in body:
        return jsonify({"error": "inline case source is not accepted; use case_path"}), 400
    overlay = body.get("overlay")
    if not isinstance(overlay, str) or not overlay:
        return jsonify({"error": "missing 'overlay' (SVG string)"}), 400
    try:
        width, height = _render_dimensions(body)
        path = _resolve_case_path(str(body["part_path"])) if body.get("part_path") else None
        if body.get("part_path") and path is None:
            raise FileNotFoundError(f"case.py not found: {body['part_path']}")
        lock = _case_lock(path) if path is not None else contextlib.nullcontext()
        with lock:
            part, scene = _scene_for_case(path) if path is not None else (_synthetic_box(), None)
            glb = _render_case_glb(part, scene)
            base = _snapshot_base_png(body, width, height)
            snapshot = composite_snapshot(base, overlay, width, height)
            manifest = _snapshot_manifest(path, scene, part)
    except Exception as exc:
        from cadkit.case_execution import SandboxUnavailable

        if isinstance(exc, SandboxUnavailable):
            return jsonify({"status": "sandbox_unavailable", "error": str(exc)}), 503
        return jsonify({"error": f"failed to load or render snapshot: {exc}"}), 400
    return jsonify(
        {
            "glb_b64": base64.b64encode(glb).decode("ascii"),
            "snapshot_b64": base64.b64encode(snapshot).decode("ascii"),
            "width": width,
            "height": height,
            "part_label": getattr(part, "label", None),
            "render_mode": "assembled" if scene is not None else "single_part",
            "assembly": scene.to_dict() if scene is not None else None,
            "manifest": manifest,
        }
    )


def _snapshot_manifest(path: Path | None, scene: Any | None, part: Any) -> dict[str, Any] | None:
    if path is None or scene is None:
        return None
    return _manifest_with_revision(path, scene, part)


def _default_case_source(source: str) -> str:
    """Restore only the trusted controls and exterior bindings to workshop defaults."""
    from cadkit import ExteriorDesignSpec
    from cadkit.assembly import build_demo_assembly
    from cadkit.case_source import rewrite_controls_source, rewrite_exterior_design_source

    default_scene = build_demo_assembly()
    targets = {
        f"control.{control_id}": default_scene.node(f"control.{control_id}").transform.position[:2]
        for control_id in CONTROL_IDS
    }
    reset = rewrite_controls_source(source, targets)
    return rewrite_exterior_design_source(reset, ExteriorDesignSpec(profile="rounded"))


def _validated_reset_artifacts(candidate_path: Path) -> tuple[Any, Any]:
    """Execute and validate the trusted reset candidate before publication."""
    artifacts = _case_artifacts(candidate_path)
    part, scene = _scene_from_artifacts(candidate_path, artifacts)
    if scene is None:
        raise ValueError("reset requires a canonical ASSEMBLY_SPEC scene")
    placements = _prototype_placements(artifacts)
    placements_error = _placements_match_error(scene, placements)
    if placements_error:
        raise ValueError(placements_error)
    validation_error = _prototype_validation_error(
        part,
        placements,
        baseline_part=part,
        baseline_placements=placements,
    )
    if validation_error:
        raise ValueError(validation_error)
    from cadkit import snap_fit_pair

    top, bottom = snap_fit_pair(part)
    _validate_prototype_half("top", top)
    _validate_prototype_half("bottom", bottom)
    return part, scene


def _reset() -> Any:
    """Transactionally restore the canonical rounded case and control layout."""
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    body = cast(JsonObject, raw)
    if "case" in body or "assembly" in body:
        return jsonify({"error": "inline case and assembly payloads are not accepted"}), 400
    case_value = body.get("case_path")
    revision = body.get("editor_revision")
    if not isinstance(case_value, str) or not case_value.strip():
        return jsonify({"error": "missing 'case_path'"}), 400
    if (
        not isinstance(revision, str)
        or len(revision) != 64
        or any(character not in "0123456789abcdef" for character in revision)
    ):
        return jsonify({"error": "editor_revision must be a lowercase SHA-256 digest"}), 400
    case_path = _resolve_case_path(case_value.strip())
    if case_path is None:
        return jsonify({"error": f"case.py not found: {case_value}"}), 404

    try:
        with _case_lock(case_path):
            current_bytes = _read_case_bytes(case_path)
            current_source = current_bytes.decode("utf-8")
            current_part, current_scene = _scene_for_case(case_path)
            if current_scene is None:
                raise ValueError("reset requires a canonical ASSEMBLY_SPEC scene")
            current_revision = _editor_revision(case_path, current_scene, current_part)
            if revision != current_revision:
                return jsonify(
                    {
                        "status": "stale_revision",
                        "error": "reset revision is stale; reload the editor",
                        "editor_revision": current_revision,
                    }
                ), 409
            reset_source = _default_case_source(current_source)
            from cadkit.case_source import source_changes_outside_editable

            if source_changes_outside_editable(current_source, reset_source):
                raise ValueError(
                    "reset changed bytes outside trusted controls and exterior bindings"
                )
            with tempfile.TemporaryDirectory(
                dir=case_path.parent, prefix=".editor-reset-"
            ) as directory:
                candidate_path = Path(directory) / "case.py"
                candidate_path.write_text(reset_source, encoding="utf-8")
                part, scene = _validated_reset_artifacts(candidate_path)
                glb = _render_case_glb(part, scene)
            snapshot = composite_snapshot(
                make_test_base_png(800, 600),
                "<svg xmlns='http://www.w3.org/2000/svg'/>",
                800,
                600,
            )
            original_mode = stat.S_IMODE(case_path.stat().st_mode)
            try:
                _atomic_write_bytes(case_path, reset_source.encode("utf-8"), original_mode)
                manifest = _manifest_with_revision(case_path, scene, part)
            except Exception:
                _atomic_write_bytes(case_path, current_bytes, original_mode)
                raise
    except Exception as exc:
        from cadkit.case_execution import SandboxUnavailable

        if isinstance(exc, SandboxUnavailable):
            return jsonify({"status": "sandbox_unavailable", "error": str(exc)}), 503
        return jsonify({"error": f"reset failed: {exc}"}), 400
    return jsonify(
        {
            "status": "reset",
            "manifest": manifest,
            "snapshot_b64": base64.b64encode(snapshot).decode("ascii"),
            "glb_b64": base64.b64encode(glb).decode("ascii"),
            "render_mode": "assembled",
            "assembly": scene.to_dict(),
        }
    )


def _synthetic_box() -> Any:
    from build123d import Align, Box

    return Box(100, 100, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))


def _snapshot_base_png(body: JsonObject, width: int, height: int) -> bytes:
    value = body.get("base_png_b64")
    if value:
        if not isinstance(value, str):
            raise ValueError("base_png_b64 must be a base64 string")
        data = _decode_base64(value, "base_png_b64", MAX_SNAPSHOT_B64_BYTES)
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("base_png_b64 must contain a PNG")
        return data
    return make_test_base_png(width, height)


def _save_submission(
    case_path: Path, case_path_str: str, feedback: JsonObject, snapshot: str, submit_id: str
) -> Path:
    work_dir = _work_dir_for(case_path, submit_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    work_dir.chmod(0o700)
    _atomic_write_bytes(work_dir / "feedback.json", json.dumps(feedback, indent=2).encode())
    if snapshot:
        _atomic_write_bytes(
            work_dir / "snapshot.png",
            _decode_base64(snapshot, "snapshot_b64", MAX_SNAPSHOT_B64_BYTES),
        )
    _atomic_write_bytes(
        work_dir / "submit.json",
        json.dumps(
            {
                "submit_id": submit_id,
                "case_path": case_path_str,
                "received_at": _dt.datetime.now(_dt.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "feedback_path": str(work_dir / "feedback.json"),
                "snapshot_path": str(work_dir / "snapshot.png") if snapshot else None,
            },
            indent=2,
        ).encode(),
    )
    return work_dir


def _run_orchestrator(
    case_path: Path,
    work_dir: Path,
    *,
    base_scene: Any | None = None,
    editor_moves: list[dict[str, Any]] | None = None,
    base_revision: str | None = None,
) -> tuple[JsonObject | None, Exception | None]:
    try:
        from agent import loop as agent_loop

        candidate = work_dir / "case.py"
        before = case_path.read_bytes()
        before_mode = stat.S_IMODE(case_path.stat().st_mode)
        base_path = case_path
        if base_scene is not None and editor_moves:
            from cadkit.case_source import rewrite_case_source

            base_path = work_dir / ".editor-base.case.py"
            base_path.write_text(
                rewrite_case_source(case_path, base_scene, editor_moves),
                encoding="utf-8",
            )
        result = agent_loop.iterate_with_visual_feedback(
            base_path,
            work_dir,
            output_path=candidate,
            base_scene=base_scene,
            base_revision=base_revision,
            editor_moves_applied=base_path != case_path,
        )
        if editor_moves:
            result.setdefault("editor_moves", editor_moves)
        # Compatibility for legacy test/plugin orchestrators that still write
        # the supplied path: capture their result privately, then restore the
        # original before any validation or publication occurs.
        if not candidate.is_file():
            if case_path.read_bytes() != before:
                shutil.copy2(case_path, candidate)
            else:
                candidate.write_bytes(before)
        if (
            case_path.read_bytes() != before
            or stat.S_IMODE(case_path.stat().st_mode) != before_mode
        ):
            _atomic_write_bytes(case_path, before, before_mode)
        return result, None
    except Exception as exc:
        return None, exc


def _refresh_manifest(case_path: Path) -> tuple[Any, Exception | None]:
    try:
        from cadkit.manifest import from_case_path, has_assembly_spec

        manifest = from_case_path(case_path)
        if has_assembly_spec(case_path):
            artifacts = _case_artifacts(case_path)
            if artifacts.assembly is not None:
                from cadkit.assembly import AssemblyScene

                scene = AssemblyScene.from_dict(artifacts.assembly)
                manifest = _manifest_with_revision(case_path, scene, artifacts.part)
        return manifest, None
    except Exception as exc:
        return None, exc


def _render_submission(
    case_path: Path, manifest: Any
) -> tuple[tuple[str, str] | None, Exception | None]:
    del manifest
    try:
        part, scene = _scene_for_case(case_path)
        glb = _render_case_glb(part, scene)
        snapshot = composite_snapshot(
            make_test_base_png(800, 600), "<svg xmlns='http://www.w3.org/2000/svg'/>", 800, 600
        )
        return (base64.b64encode(snapshot).decode(), base64.b64encode(glb).decode()), None
    except Exception as exc:
        return None, exc


def _submit() -> Any:
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    body = cast(JsonObject, raw)
    value = body.get("case_path")
    if not isinstance(value, str) or not value.strip():
        return jsonify({"error": "missing 'case_path'"}), 400
    feedback = body.get("feedback", {})
    if not isinstance(feedback, dict):
        return jsonify({"error": "feedback must be a JSON object"}), 400
    feedback = cast(JsonObject, feedback)
    if len(json.dumps(feedback, ensure_ascii=False).encode()) > MAX_FEEDBACK_BYTES:
        return jsonify({"error": "feedback exceeds the request limit"}), 413
    snapshot = body.get("snapshot_b64", "")
    if not isinstance(snapshot, str):
        return jsonify({"error": "snapshot_b64 must be a base64 string"}), 400
    if snapshot:
        try:
            decoded = _decode_base64(snapshot, "snapshot_b64", MAX_SNAPSHOT_B64_BYTES)
            if not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("snapshot_b64 must contain a PNG")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    path = _resolve_case_path(value.strip())
    if path is None:
        return jsonify({"error": f"case_path not found: {value}"}), 404
    with _case_lock(path):
        return _submit_locked(path, value.strip(), feedback, snapshot)


def _feedback_moves(
    canonical_scene: Any | None, feedback: JsonObject
) -> tuple[Any | None, list[dict[str, Any]], Any | None]:
    if canonical_scene is None:
        if feedback.get("moves"):
            return (
                None,
                [],
                (
                    jsonify({"error": "submit moves require a canonical ASSEMBLY_SPEC scene"}),
                    409,
                ),
            )
        return None, [], None
    canonical_error = _canonical_scene_error(canonical_scene)
    if canonical_error:
        return None, [], (jsonify({"error": canonical_error}), 400)
    try:
        feedback_scene, accepted = apply_scene_moves(canonical_scene, feedback.get("moves", []))
    except SceneMoveError as exc:
        return (
            None,
            [],
            (
                jsonify(
                    {
                        "error": f"invalid feedback move {exc.index + 1}: {exc}",
                        "violations": exc.result["violations"],
                        "move_index": exc.index,
                    }
                ),
                400,
            ),
        )
    return feedback_scene, accepted, None


def _vision_failure(result: Any, submit_id: str, work_dir: Path) -> Any:
    result_dict = cast(JsonObject, result) if isinstance(result, dict) else None
    return jsonify(
        {
            "status": "vision_failed",
            "submit_id": submit_id,
            "error": result_dict.get("error") if result_dict is not None else "orchestrator failed",
            "violations": result_dict.get("violations", []) if result_dict is not None else [],
            "iterations": result_dict.get("iterations") if result_dict is not None else None,
            "work_dir": str(work_dir),
        }
    ), 500


def _publish_noncanonical(
    case_path: Path,
    candidate_path: Path,
    original: bytes,
    original_mode: int,
    submit_id: str,
    result: dict[str, Any],
    work_dir: Path,
) -> Any:
    candidate_source = _read_case_bytes(candidate_path)
    _atomic_write_bytes(case_path, candidate_source, original_mode)
    candidate_path.unlink(missing_ok=True)
    manifest, manifest_error = _refresh_manifest(case_path)
    if manifest_error is not None:
        _atomic_write_bytes(case_path, original, original_mode)
        return jsonify(
            {
                "status": "vision_ok_but_manifest_failed",
                "submit_id": submit_id,
                "error": f"{type(manifest_error).__name__}: {manifest_error}",
                "work_dir": str(work_dir),
            }
        ), 500
    rendered, render_error = _render_submission(case_path, manifest)
    if render_error is not None or rendered is None:
        _atomic_write_bytes(case_path, original, original_mode)
        return jsonify(
            {
                "status": "vision_ok_but_render_failed",
                "submit_id": submit_id,
                "manifest": manifest,
                "error": f"{type(render_error).__name__}: {render_error}",
                "work_dir": str(work_dir),
            }
        ), 500
    return jsonify(
        {
            "status": "ok",
            "submit_id": submit_id,
            "manifest": manifest,
            "snapshot_b64": rendered[0],
            "glb_b64": rendered[1],
            "iterations": result.get("iterations"),
            "violations": result.get("violations", []),
            "work_dir": str(work_dir),
        }
    )


def _publish_canonical(
    case_path: Path,
    candidate_path: Path,
    layout_baseline_part: Any,
    feedback_scene: Any,
    layout_baseline_scene: Any,
    accepted: list[dict[str, Any]],
    submit_id: str,
    result: dict[str, Any],
    work_dir: Path,
) -> Any:
    from cadkit.case_source import source_changes_outside_editable

    base_path = work_dir / ".editor-base.case.py"
    base_source = (
        base_path.read_text(encoding="utf-8")
        if base_path.is_file()
        else case_path.read_text(encoding="utf-8")
    )
    candidate_source = candidate_path.read_text(encoding="utf-8")
    if source_changes_outside_editable(base_source, candidate_source):
        raise RuntimeError(
            "candidate source changed bytes outside literal CONTROLS XY and EXTERIOR_DESIGN"
        )
    artifacts = _case_artifacts(candidate_path)
    candidate_part, candidate_scene = _scene_from_artifacts(candidate_path, artifacts)
    if candidate_scene is None:
        raise RuntimeError("candidate case.py has no canonical ASSEMBLY_SPEC scene")
    candidate_error = _canonical_scene_error(candidate_scene)
    if candidate_error:
        raise RuntimeError(candidate_error)
    llm_moves = result.get("llm_accepted_moves", [])
    if not isinstance(llm_moves, list):
        raise RuntimeError("orchestrator returned malformed LLM move records")
    move_error = _accepted_move_error(feedback_scene, candidate_scene, accepted + llm_moves)
    if move_error:
        raise RuntimeError(move_error)
    placements = _prototype_placements(artifacts)
    placements_error = _placements_match_error(candidate_scene, placements)
    if placements_error:
        raise RuntimeError(placements_error)
    try:
        exterior_violations = validate_layout_candidate(
            layout_baseline_part,
            candidate_part,
            layout_baseline_scene,
        )
    except Exception as exc:
        raise RuntimeError(f"exterior protected-geometry validation failed closed: {exc}") from exc
    if exterior_violations:
        raise RuntimeError(
            "candidate changed protected exterior geometry: "
            + "; ".join(violation.message for violation in exterior_violations)
        )
    from cadkit.assembly import placements_from_assembly

    validation_error = _prototype_validation_error(
        candidate_part,
        placements,
        baseline_part=layout_baseline_part,
        baseline_placements=placements_from_assembly(layout_baseline_scene),
    )
    if validation_error:
        raise RuntimeError(validation_error)
    from cadkit import snap_fit_pair

    top, bottom = snap_fit_pair(candidate_part)
    _validate_prototype_half("top", top)
    _validate_prototype_half("bottom", bottom)
    candidate_source_bytes = _read_case_bytes(candidate_path)
    _atomic_write_bytes(case_path, candidate_source_bytes)
    candidate_path.unlink(missing_ok=True)
    manifest = _manifest_with_revision(case_path, candidate_scene, candidate_part)
    glb = _render_case_glb(candidate_part, candidate_scene)
    base = make_test_base_png(800, 600)
    refreshed = composite_snapshot(base, "<svg xmlns='http://www.w3.org/2000/svg'/>", 800, 600)
    return jsonify(
        {
            "status": "ok",
            "submit_id": submit_id,
            "manifest": manifest,
            "snapshot_b64": base64.b64encode(refreshed).decode(),
            "glb_b64": base64.b64encode(glb).decode(),
            "iterations": result.get("iterations"),
            "violations": result.get("violations", []),
            "work_dir": str(work_dir),
            "render_mode": "assembled",
            "assembly": candidate_scene.to_dict(),
        }
    )


def _submission_base_revision(
    case_path: Path,
    canonical_scene: Any | None,
    canonical_part: Any | None,
    feedback_scene: Any | None,
    accepted: list[dict[str, Any]],
) -> str | None:
    if canonical_scene is None or canonical_part is None or feedback_scene is None:
        return None
    if not accepted:
        return _editor_revision(case_path, feedback_scene, canonical_part)
    from cadkit.assembly import placements_from_assembly

    feedback_part, _ = _preview_part_for_moves(
        case_path,
        feedback_scene,
        accepted,
        baseline_part=canonical_part,
        baseline_placements=placements_from_assembly(canonical_scene),
    )
    return _editor_revision(case_path, feedback_scene, feedback_part)


def _coalesce_control_moves(
    source_scene: Any,
    *batches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the final target for each control in a deterministic order."""
    merged: dict[str, dict[str, Any]] = {}
    for batch in batches:
        for move in batch:
            element_id = move.get("element_id")
            target = move.get("new_position")
            if not isinstance(element_id, str) or not isinstance(target, (list, tuple)):
                raise ValueError("accepted control move records are malformed")
            target_values = cast(list[Any] | tuple[Any, ...], target)
            if len(target_values) != 2:
                raise ValueError("accepted control move targets must be two-dimensional")
            expected = source_scene.node(element_id).transform.position[:2]
            merged[element_id] = {
                "element_id": element_id,
                "expected_position": list(expected),
                "new_position": list(target_values),
            }
    return list(merged.values())


def _moves_with_expected_positions(scene: Any, moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add source positions to move deltas returned by the candidate inspector."""
    result: list[dict[str, Any]] = []
    for move in moves:
        element_id = move.get("element_id")
        if not isinstance(element_id, str):
            result.append(move)
            continue
        expected = scene.node(element_id).transform.position[:2]
        record = dict(move)
        record["expected_position"] = list(expected)
        result.append(record)
    return result


def _submission_layout_baseline(
    case_path: Path,
    canonical_part: Any,
    canonical_scene: Any,
    feedback_scene: Any,
    accepted: list[dict[str, Any]],
    llm_moves: list[dict[str, Any]],
) -> tuple[Any, Any]:
    """Build the accepted-layout baseline before any exterior edit is applied."""
    _layout_scene, llm_accepted = _apply_revision_moves(
        feedback_scene,
        _moves_with_expected_positions(feedback_scene, llm_moves),
    )
    all_moves = _coalesce_control_moves(canonical_scene, accepted, llm_accepted)
    if not all_moves:
        return canonical_part, canonical_scene
    return build_layout_baseline(case_path, canonical_scene, all_moves)


def _submit_locked(case_path: Path, case_path_str: str, feedback: JsonObject, snapshot: str) -> Any:
    submit_id = str(uuid.uuid4())
    original: bytes | None = None
    original_mode = 0o600
    try:
        original = _read_case_bytes(case_path)
        original_mode = stat.S_IMODE(case_path.stat().st_mode)
        from cadkit.case_execution import require_sandbox

        require_sandbox()
        from cadkit.manifest import has_assembly_spec

        canonical_scene = None
        canonical_part = None
        if has_assembly_spec(case_path):
            canonical_part, canonical_scene = _scene_for_case(case_path)
        feedback_scene, accepted, feedback_error = _feedback_moves(canonical_scene, feedback)
        if feedback_error is not None:
            return feedback_error
        work_dir = _save_submission(case_path, case_path_str, feedback, snapshot, submit_id)
        base_revision = _submission_base_revision(
            case_path, canonical_scene, canonical_part, feedback_scene, accepted
        )
        result, error = _run_orchestrator(
            case_path,
            work_dir,
            base_scene=feedback_scene,
            editor_moves=accepted,
            base_revision=base_revision,
        )
        if error is not None:
            raise error
        if not isinstance(result, dict) or result.get("status") != "ok":
            return _vision_failure(result, submit_id, work_dir)
        candidate_path = work_dir / "case.py"
        if not candidate_path.is_file():
            raise RuntimeError("orchestrator did not produce a private candidate case.py")
        source_error = _source_contract_error(candidate_path)
        if source_error and canonical_scene is not None:
            raise RuntimeError(source_error)
        if canonical_scene is None:
            return _publish_noncanonical(
                case_path,
                candidate_path,
                original,
                original_mode,
                submit_id,
                result,
                work_dir,
            )
        llm_moves = result.get("llm_accepted_moves", [])
        if not isinstance(llm_moves, list):
            raise RuntimeError("orchestrator returned malformed LLM move records")
        layout_baseline_part, layout_baseline_scene = _submission_layout_baseline(
            case_path,
            canonical_part,
            canonical_scene,
            feedback_scene,
            accepted,
            cast(list[dict[str, Any]], llm_moves),
        )
        return _publish_canonical(
            case_path,
            candidate_path,
            layout_baseline_part,
            feedback_scene,
            layout_baseline_scene,
            accepted,
            submit_id,
            result,
            work_dir,
        )
    except Exception as exc:
        _restore_case_source(case_path, original, original_mode)
        from cadkit.case_execution import SandboxUnavailable

        if isinstance(exc, SandboxUnavailable):
            return jsonify({"status": "sandbox_unavailable", "error": str(exc)}), 503
        return jsonify(
            {
                "status": "vision_failed",
                "submit_id": submit_id,
                "error": f"{type(exc).__name__}: {exc}",
            }
        ), 500
    except BaseException:
        _restore_case_source(case_path, original, original_mode)
        raise


def _default_host() -> str:
    """Return the only address the unauthenticated student editor may use."""
    return "127.0.0.1"


def _editor_port() -> int:
    """Return a validated optional port override, defaulting to the workshop port."""
    raw_port = os.environ.get("HF_EDITOR_PORT", "5000")
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise SystemExit("HF_EDITOR_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("HF_EDITOR_PORT must be between 1 and 65535")
    return port


def _default_editor_ports() -> tuple[int, ...]:
    return tuple(range(DEFAULT_EDITOR_PORT, LAST_EDITOR_PORT + 1))


def _editor_ports() -> tuple[int, ...]:
    """Return the exact override or the bounded default fallback range."""
    if "HF_EDITOR_PORT" in os.environ:
        return (_editor_port(),)
    return _default_editor_ports()


def _make_editor_server(app: Flask, host: str) -> BaseWSGIServer:
    ports = _editor_ports()
    last_error: OSError | None = None
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((host, port))
                listener.listen()
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise
                last_error = exc
                continue
            return make_server(host, port, app, threaded=True, fd=listener.fileno())
    if "HF_EDITOR_PORT" in os.environ:
        message = f"HF_EDITOR_PORT {ports[0]} is already in use"
    else:
        message = f"No editor port is available in loopback range {ports[0]}-{ports[-1]}"
    raise SystemExit(message) from last_error


def _exec_case_path(path: str | Path) -> Any:  # pyright: ignore[reportUnusedFunction]
    """Compatibility wrapper for callers that already provide a safe path."""
    case_path = Path(path)
    if not case_path.exists():
        raise FileNotFoundError(f"case.py not found: {case_path}")
    from cadkit.case_execution import execute_case_path

    return execute_case_path(case_path)


def main() -> None:
    """Run the local editor in the foreground and exit cleanly on Ctrl-C."""
    host = _default_host()
    app = create_app()
    http_server = _make_editor_server(app, host)
    case_path = _SESSION_DIR / "examples" / "6-button-gamepad" / "case.py"
    original_case = case_path.read_bytes() if case_path.is_file() else None
    original_mode = stat.S_IMODE(case_path.stat().st_mode) if original_case is not None else 0o600
    print(f"Editor running at http://{host}:{http_server.port}", flush=True)
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        print("Editor stopped cleanly.", flush=True)
    finally:
        http_server.server_close()
        _restore_case_source(case_path, original_case, original_mode)


if __name__ == "__main__":
    main()
