"""Tests for exact-six structured volumes and protected routes."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import cast

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

from build123d import Box, BuildPart, Part  # noqa: E402
from cadkit.aabb import AABB, Route, Volume  # noqa: E402
from cadkit.constraints import Placements, validate  # noqa: E402
from cadkit.parametric import gamepad_body  # noqa: E402

from tests._cad_helpers import synthetic_tall_placements  # noqa: E402

CONTROL_IDS = ("up", "down", "right", "left", "action_a", "action_b")
CONTROL_ROLES = ("UP", "DOWN", "RIGHT", "LEFT", "ACTION_A", "ACTION_B")


def _active_placements(
    *,
    volumes: tuple[Volume, ...] | None = None,
    routes: tuple[Route, ...] | None = None,
) -> Placements:
    placements = synthetic_tall_placements()
    return dataclasses.replace(
        placements,
        **({} if volumes is None else {"volumes": volumes}),
        **({} if routes is None else {"routes": routes}),
    )


def _export_violations(placements: Placements) -> set[str]:
    return {
        violation.rule
        for violation in validate(
            gamepad_body(
                thickness_mm=placements.case_thickness_mm,
                fillet_radius_mm=0.0,
            ),
            placements=placements,
            overhang_verified=True,
            bridge_verified=True,
            supports_used=False,
            require_export_verification=True,
        )
        if violation.severity == "error"
    }


def test_aabb_touching_is_not_overlap_and_clearance_is_exact() -> None:
    first = AABB(0.0, 0.0, 0.0, 10.0, 10.0, 10.0)
    touching = AABB(10.0, 0.0, 0.0, 20.0, 10.0, 10.0)
    separated = AABB(12.0, 14.0, 0.0, 20.0, 20.0, 10.0)
    assert not first.overlaps(touching)
    assert first.clearance(touching) == 0.0
    assert first.clearance(separated) == 5**0.5 * 2


def test_route_bend_radius_is_measured_from_real_waypoints() -> None:
    sharp = Route(
        "wire.control_up",
        ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0)),
        min_bend_radius_mm=10.0,
    )
    wide = Route(
        "wire.control_up",
        ((0.0, 0.0, 0.0), (30.0, 0.0, 0.0), (30.0, 30.0, 0.0)),
        min_bend_radius_mm=10.0,
    )
    assert sharp.minimum_bend_radius() < 10.0
    assert wide.minimum_bend_radius() > 10.0


def test_active_export_rejects_overlapping_control_keepouts() -> None:
    volumes = (
        Volume("control.up.keepout", AABB(10, 10, 0, 30, 30, 12)),
        Volume("control.down.keepout", AABB(20, 20, 0, 40, 40, 12)),
    )
    assert "keepout_overlap" in _export_violations(_active_placements(volumes=volumes))


def test_active_export_rejects_routes_crossing_protected_volumes() -> None:
    volumes = (Volume("control.up.keepout", AABB(50, 20, 0, 70, 60, 12)),)
    routes = (Route("wire.control_up", ((0, 40, 6), (120, 40, 6)), 1.0),)
    rules = _export_violations(_active_placements(volumes=volumes, routes=routes))
    assert "route_intersects_volume" in rules


def test_active_export_requires_each_control_keepout_and_cutout() -> None:
    volumes = (
        Volume("feather_cavity", AABB(1, 1, 0, 30, 30, 20)),
        Volume("usb_cutout", AABB(100, 5, 0, 114, 13, 4), is_keep_out=False),
        Volume("snap_receiver", AABB(5, 40, 0, 15, 50, 10)),
    )
    rules = _export_violations(_active_placements(volumes=volumes))
    assert "control_keepout_unverified" in rules
    assert "control_cutout_unverified" in rules


def test_active_routes_round_trip_without_legacy_corridor_dimensions() -> None:
    placements = _active_placements(
        routes=(Route("wire.control_up", ((10, 10, 1), (11, 10, 1)), 1.0),)
    )
    payload = placements.to_dict()
    restored = Placements.from_dict(payload)
    assert payload["dpad_centre_xy"] is None
    assert payload["dpad_cable_corridor_mm"] is None
    assert restored.routes == placements.routes
    assert restored.to_dict() == payload


def test_cutout_ligament_at_exact_boundary_passes_and_below_fails() -> None:
    exact = Volume("control.up.cutout", AABB(3.0, 3.0, 0, 20.0, 20.0, 4.0), is_keep_out=False)
    below = Volume("control.up.cutout", AABB(2.999, 3.0, 0, 20.0, 20.0, 4.0), is_keep_out=False)
    assert "cutout_outer_ligament" not in _export_violations(Placements(volumes=(exact,)))
    assert "cutout_outer_ligament" in _export_violations(Placements(volumes=(below,)))


def test_missing_placements_is_fail_closed_at_export() -> None:
    with BuildPart() as bp:
        Box(130.0, 90.0, 98.0)
    violations = validate(
        cast(Part, bp.part),
        placements=None,
        overhang_verified=True,
        bridge_verified=True,
        supports_used=False,
        require_export_verification=True,
    )
    assert "placements_unverified" in {violation.rule for violation in violations}


def test_historical_corridor_data_is_not_a_current_validation_contract() -> None:
    corridor = Volume(
        "dpad_cable_corridor",
        AABB(10, 10, 0, 30, 20, 8),
        is_keep_out=False,
        is_cable_corridor=True,
    )
    placements = Placements(
        volumes=(corridor,),
        routes=(Route("dpad_cable_corridor", ((11, 11, 4), (31, 11, 4)), 10.0),),
    )
    rules = _export_violations(placements)
    assert not any("dpad" in rule or "corridor" in rule for rule in rules)
