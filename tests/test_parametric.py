"""Focused tests for the exact-six CAD primitives."""

from __future__ import annotations

import math
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, cast

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

import cadkit  # noqa: E402
import pytest  # noqa: E402
from build123d import Align, Box, Keep, Location, Part, Plane  # noqa: E402
from cadkit import coupons  # noqa: E402
from cadkit.evidence import write_test_frozen_record  # noqa: E402
from cadkit.parametric import (  # noqa: E402
    ACTIVE_CONTROL_COMPONENT_IDS,
    CASE_MAX_MM,
    FEATHER_KEEP_OUT_MM,
    FEATHER_MOUNTING_HOLES_BR_MM,
    FEATHER_MOUNTING_HOLES_CENTRED_MM,
    FEATHER_NUT_ACROSS_FLATS_MM,
    FEATHER_NUT_DEPTH_MM,
    FEATHER_SCREW_HOLE_DIAMETER_MM,
    GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
    GUUZI_CUTOUT_GAUGE_MM,
    GUUZI_CUTOUT_PROVISIONAL_MM,
    PBS33B_CUTOUT_GAUGE_MM,
    PBS33B_CUTOUT_PROVISIONAL_MM,
    PBS33B_DIRECTIONAL_COMPONENT_ID,
    FrozenControlGeometry,
    captive_nut_pocket,
    control_cutout_gauge_plate,
    control_keepout,
    control_mount,
    control_mount_gauge,
    control_mounting_land,
    control_terminal_keepout,
    feather_cavity,
    feather_mount_assembly,
    feather_mount_boss,
    feather_mounting_holes_centred_mm,
    fillet_edges,
    gamepad_body,
    hex_pocket,
    hex_pocket_anti_rotation_diameter,
    hex_pocket_anti_rotation_flat_to_flat,
    reset_tool_hole,
    snap_fit_pair,
    status_led_view_hole,
    usb_cutout,
)
from cadkit.snap_geometry import AUTHORITATIVE_SNAP_GEOMETRY  # noqa: E402


def _signed_geometry(component_id: str, tmp_path: Path | None = None) -> FrozenControlGeometry:
    if component_id == PBS33B_DIRECTIONAL_COMPONENT_ID:
        values: dict[str, Any] = {
            "cutout_diameter_mm": 12.4,
            "mounting_land_diameter_mm": 18.0,
            "underside_keepout_mm": (18.0, 18.0, 10.69),
            "terminal_keepout_mm": (14.0, 5.83, 5.0),
            "minimum_thread_length_mm": 4.0,
        }
    else:
        values = {
            "cutout_diameter_mm": 12.4,
            "mounting_land_diameter_mm": 14.2,
            "underside_keepout_mm": (14.2, 14.2, 21.5),
            "terminal_keepout_mm": (8.0, 4.0, 6.0),
            "minimum_thread_length_mm": 7.5,
        }
    geometry = FrozenControlGeometry(
        component_id=component_id,
        mounting_land_thickness_mm=2.0,
        source="test-receiving-record",
        signed_by="Test signer",
        signed_utc="2026-08-28T00:00:00Z",
        signed_receiving_row=f"{component_id}-row",
        coupon_binding=f"{component_id}-coupon",
        terminal_route_min_bend_radius_mm=10.0,
        **values,
    )
    if tmp_path is None:
        return geometry
    record = write_test_frozen_record(
        tmp_path / f"{component_id}.json",
        id_slug=component_id,
        gate_name="button",
        extra_content={
            "computed": {
                "control_geometry": {
                    "component_id": component_id,
                    "cutout_diameter_mm": geometry.cutout_diameter_mm,
                    "mounting_land_diameter_mm": geometry.mounting_land_diameter_mm,
                    "mounting_land_thickness_mm": geometry.mounting_land_thickness_mm,
                    "underside_keepout_mm": list(geometry.underside_keepout_mm),
                    "terminal_keepout_mm": list(geometry.terminal_keepout_mm),
                    "minimum_thread_length_mm": geometry.minimum_thread_length_mm,
                    "terminal_route_min_bend_radius_mm": (
                        geometry.terminal_route_min_bend_radius_mm
                    ),
                    "signed_receiving_row": geometry.signed_receiving_row,
                    "coupon_binding": geometry.coupon_binding,
                }
            }
        },
    )
    return FrozenControlGeometry.from_frozen_evidence(record)


def test_public_cad_api_has_no_retired_control_generators() -> None:
    retired = (
        "dpad_cutout",
        "dpad_keepout",
        "dpad_mount",
        "dpad_cable_keepout",
        "validate_dpad_placement",
        "button_label_metadata",
        "button_mount",
        "button_mount_gauge",
        "button_keepout",
    )
    assert all(not hasattr(cadkit, name) for name in retired)
    assert all(not hasattr(cadkit.parametric, name) for name in retired)
    assert not hasattr(coupons, "dpad_pattern_coupon")


def test_active_component_order_is_exactly_four_pbs_then_two_guuzi() -> None:
    assert ACTIVE_CONTROL_COMPONENT_IDS == (
        PBS33B_DIRECTIONAL_COMPONENT_ID,
        PBS33B_DIRECTIONAL_COMPONENT_ID,
        PBS33B_DIRECTIONAL_COMPONENT_ID,
        PBS33B_DIRECTIONAL_COMPONENT_ID,
        GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
        GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
    )
    assert "dpad" not in cadkit.AVAILABLE_MATERIALS
    assert cadkit.AVAILABLE_MATERIALS["buttons"] == {
        PBS33B_DIRECTIONAL_COMPONENT_ID,
        GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
    }


def test_control_mount_requires_matching_signed_geometry(tmp_path: Path) -> None:
    pbs = _signed_geometry(PBS33B_DIRECTIONAL_COMPONENT_ID, tmp_path)
    guuzi = _signed_geometry(GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID, tmp_path)
    assert control_mount(component_id=pbs.component_id, geometry=pbs).bounding_box().size.X == 12.4
    assert (
        control_mount(component_id=guuzi.component_id, geometry=guuzi).bounding_box().size.X == 12.4
    )
    with pytest.raises(ValueError, match="does not match"):
        control_mount(component_id=PBS33B_DIRECTIONAL_COMPONENT_ID, geometry=guuzi)
    with pytest.raises(ValueError, match="requires a FrozenControlGeometry"):
        control_mount(component_id=PBS33B_DIRECTIONAL_COMPONENT_ID, geometry=cast(Any, None))


def test_provisional_control_geometry_is_explicitly_non_production() -> None:
    pbs = control_mount_gauge(component_id=PBS33B_DIRECTIONAL_COMPONENT_ID)
    guuzi = control_mount_gauge(component_id=GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID)
    assert pbs.bounding_box().size.X == 12.4
    assert guuzi.bounding_box().size.X == 12.4
    with pytest.raises(ValueError, match="matching receiving-signed geometry"):
        control_keepout(component_id=PBS33B_DIRECTIONAL_COMPONENT_ID)
    with pytest.raises(ValueError, match="matching receiving-signed geometry"):
        control_terminal_keepout(component_id=GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID)
    with pytest.raises(ValueError, match="does not match"):
        control_mount(
            component_id=PBS33B_DIRECTIONAL_COMPONENT_ID,
            geometry=_signed_geometry(PBS33B_DIRECTIONAL_COMPONENT_ID),
        )


def test_component_specific_lands_and_terminal_envelopes_use_signed_record(
    tmp_path: Path,
) -> None:
    pbs = _signed_geometry(PBS33B_DIRECTIONAL_COMPONENT_ID, tmp_path)
    guuzi = _signed_geometry(GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID, tmp_path)
    assert (
        control_mounting_land(component_id=pbs.component_id, geometry=pbs).bounding_box().size.X
        == 18.0
    )
    assert (
        control_mounting_land(component_id=guuzi.component_id, geometry=guuzi).bounding_box().size.X
        == 14.2
    )
    assert (
        control_keepout(component_id=pbs.component_id, geometry=pbs).bounding_box().size.Z == 10.69
    )
    assert (
        control_terminal_keepout(component_id=guuzi.component_id, geometry=guuzi)
        .bounding_box()
        .size.Z
        == 6.0
    )
    with pytest.raises(ValueError, match="unsupported active control"):
        control_mount_gauge(component_id="historical_dpad")


def test_frozen_control_geometry_rejects_unknown_component_and_bad_gauge() -> None:
    with pytest.raises(ValueError, match="unsupported active control component"):
        _signed_geometry("historical_dpad")
    with pytest.raises(ValueError, match="selected from its receiving gauge"):
        FrozenControlGeometry(
            component_id=PBS33B_DIRECTIONAL_COMPONENT_ID,
            cutout_diameter_mm=max(PBS33B_CUTOUT_GAUGE_MM) + 0.1,
            mounting_land_diameter_mm=18.0,
            mounting_land_thickness_mm=2.0,
            underside_keepout_mm=(18.0, 18.0, 10.69),
            terminal_keepout_mm=(14.0, 5.83, 5.0),
            source="source",
            signed_by="signer",
            signed_utc="2026-08-28T00:00:00Z",
            signed_receiving_row="row",
            coupon_binding="coupon",
        )


@pytest.mark.parametrize(
    ("component_id", "provisional_diameter", "gauge_values"),
    [
        (
            PBS33B_DIRECTIONAL_COMPONENT_ID,
            PBS33B_CUTOUT_PROVISIONAL_MM,
            PBS33B_CUTOUT_GAUGE_MM,
        ),
        (
            GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
            GUUZI_CUTOUT_PROVISIONAL_MM,
            GUUZI_CUTOUT_GAUGE_MM,
        ),
    ],
)
def test_current_aperture_targets_are_selected_gauge_candidates(
    component_id: str,
    provisional_diameter: float,
    gauge_values: tuple[float, ...],
) -> None:
    assert provisional_diameter == PBS33B_CUTOUT_PROVISIONAL_MM
    assert provisional_diameter in gauge_values
    assert (
        provisional_diameter == control_mount_gauge(component_id=component_id).bounding_box().size.X
    )


@pytest.mark.parametrize(
    ("component_id", "stale_diameter"),
    [
        (PBS33B_DIRECTIONAL_COMPONENT_ID, 11.2),
        (GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID, 11.9),
    ],
)
def test_issue47_stale_frozen_apertures_fail_closed(
    component_id: str,
    stale_diameter: float,
) -> None:
    with pytest.raises(ValueError, match="selected from its receiving gauge"):
        replace(_signed_geometry(component_id), cutout_diameter_mm=stale_diameter)


def test_component_gauge_coupons_keep_separate_equal_candidates() -> None:
    pbs = coupons.pbs33b_directional_gauge_coupon()
    guuzi = coupons.guuzi_b09bmrdptn_action_gauge_coupon()
    assert PBS33B_CUTOUT_GAUGE_MM == (12.2, 12.3, 12.4, 12.5, 12.6, 12.7)
    assert GUUZI_CUTOUT_GAUGE_MM == PBS33B_CUTOUT_GAUGE_MM
    assert max(PBS33B_CUTOUT_GAUGE_MM) + 4.0 + 5 * 15.0 == pbs.bounding_box().size.X
    assert max(GUUZI_CUTOUT_GAUGE_MM) + 4.0 + 5 * 15.0 == guuzi.bounding_box().size.X
    assert pbs.bounding_box().size.X == guuzi.bounding_box().size.X

    def expected_volume(gauge_values: tuple[float, ...]) -> float:
        length = max(70.0, max(gauge_values) + 4.0 + (len(gauge_values) - 1) * 15.0)
        width = max(24.0, max(gauge_values) + 4.0)
        holes = math.pi * 10.0 / 4.0 * sum(diameter**2 for diameter in gauge_values)
        return length * width * 10.0 - holes

    assert math.isclose(pbs.volume, expected_volume(PBS33B_CUTOUT_GAUGE_MM), abs_tol=1e-6)
    assert math.isclose(guuzi.volume, expected_volume(GUUZI_CUTOUT_GAUGE_MM), abs_tol=1e-6)


def test_gauge_plate_rejects_empty_or_unenclosed_candidates() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        control_cutout_gauge_plate(
            plate_length_mm=70.0,
            plate_width_mm=24.0,
            gauge_values=(),
        )
    with pytest.raises(ValueError, match="too short"):
        control_cutout_gauge_plate(
            plate_length_mm=20.0,
            plate_width_mm=24.0,
            gauge_values=PBS33B_CUTOUT_GAUGE_MM,
        )


def test_feather_cavity_and_usb_are_pid4516_geometry() -> None:
    cavity = feather_cavity()
    assert tuple(round(value, 6) for value in cavity.bounding_box().size) == FEATHER_KEEP_OUT_MM
    assert usb_cutout().bounding_box().size.X == 14.0
    assert abs(usb_cutout().bounding_box().size.Y - 8.0) < 1e-6
    assert status_led_view_hole().bounding_box().size.X == 3.0
    assert reset_tool_hole().bounding_box().size.X == 3.0
    with pytest.raises(ValueError, match="only 'nrf52840_sense_pid4516'"):
        feather_cavity(board="itsybitsy_32u4")


def test_feather_mounting_coordinates_and_boss_fastener_geometry() -> None:
    assert feather_mounting_holes_centred_mm() == FEATHER_MOUNTING_HOLES_CENTRED_MM
    assert FEATHER_MOUNTING_HOLES_BR_MM == (
        (2.54, 2.54),
        (48.26, 2.54),
        (2.54, 20.32),
        (48.26, 20.32),
    )
    boss = feather_mount_boss(position_xy=(0.0, 0.0))
    expected_outer = hex_pocket_anti_rotation_diameter(FEATHER_NUT_ACROSS_FLATS_MM) / 2 + 1.0
    assert abs(boss.bounding_box().size.X - 2 * expected_outer) < 1e-3
    assert boss.bounding_box().size.Z == 3.0
    assert boss.volume < math.pi * expected_outer**2 * 3.0
    assert feather_mount_assembly().volume > boss.volume
    assert FEATHER_SCREW_HOLE_DIAMETER_MM == 2.5
    assert FEATHER_NUT_DEPTH_MM == 2.2
    with pytest.raises(ValueError, match=r"thread must be 'M2\.5'"):
        feather_mount_assembly(thread="M3")


def test_hex_pocket_is_real_hex_and_captive_threads_are_bounded() -> None:
    pocket = hex_pocket(across_flats_mm=5.3, depth_mm=2.2)
    assert abs(pocket.bounding_box().size.X - 5.3 / math.cos(math.pi / 6)) < 1e-3
    assert abs(pocket.bounding_box().size.Y - hex_pocket_anti_rotation_flat_to_flat(5.3)) < 1e-3
    expected_volume = math.sqrt(3) / 2 * 5.3**2 * 2.2
    assert abs(pocket.volume - expected_volume) < 1e-3
    assert captive_nut_pocket(thread="M2.5", across_flats_mm=5.3, depth_mm=2.2).volume > 0
    with pytest.raises(ValueError, match="Unknown active captive-nut thread"):
        captive_nut_pocket(thread="M4", across_flats_mm=5.3, depth_mm=2.2)
    with pytest.raises(ValueError, match=r"only 'M2\.5'"):
        captive_nut_pocket(thread="M3", across_flats_mm=5.8, depth_mm=2.6)


def test_gamepad_body_inclusive_envelope_and_snap_pair() -> None:
    body = gamepad_body(
        length_mm=CASE_MAX_MM[0],
        width_mm=CASE_MAX_MM[1],
        thickness_mm=CASE_MAX_MM[2],
        fillet_radius_mm=0.0,
    )
    assert tuple(round(value, 6) for value in body.bounding_box().size) == CASE_MAX_MM
    with pytest.raises(ValueError, match="exceeds CASE_MAX_MM"):
        gamepad_body(thickness_mm=CASE_MAX_MM[2] + 0.001, fillet_radius_mm=0.0)
    top, bottom = snap_fit_pair(gamepad_body(thickness_mm=20.0))
    assert top.bounding_box().max.Z == 20.0
    assert abs(bottom.bounding_box().min.Z) < 1e-6
    assert fillet_edges(body, 0.0) == body


def test_gamepad_body_is_a_hollow_printable_shell() -> None:
    body = gamepad_body(
        length_mm=130.0,
        width_mm=80.0,
        thickness_mm=35.5,
        fillet_radius_mm=0.0,
    )
    bounding_volume = 130.0 * 80.0 * 35.5

    assert body.volume < bounding_volume * 0.25
    assert body.is_inside((0.0, 0.0, 1.0))
    assert not body.is_inside((0.0, 0.0, 35.5 / 2))
    assert body.is_inside((0.0, 0.0, 34.5))

    top, bottom = snap_fit_pair(body)
    assert len(top.solids()) == 1
    assert len(bottom.solids()) == 1


def test_snap_clips_have_fixed_45_degree_support_gussets() -> None:
    body = gamepad_body(
        length_mm=130.0,
        width_mm=80.0,
        thickness_mm=35.5,
        fillet_radius_mm=0.0,
    )
    bounds = body.bounding_box()
    split_z = bounds.size.Z / 2
    raw_top = cast(Part, body.split(Plane.XY.offset(split_z), keep=Keep.TOP))

    top, _ = snap_fit_pair(body)

    clip_volume = 6.0 * 3.0 * 4.0
    gusset_volume = 6.0 * 2.5 * 2.5 / 2
    assert math.isclose(
        top.volume - raw_top.volume,
        4 * (clip_volume + gusset_volume),
        abs_tol=1e-3,
    )

    clip_x = bounds.min.X + bounds.size.X * 0.25
    inner_wall_y = bounds.min.Y + 2.0
    assert top.is_inside((clip_x, inner_wall_y - 0.45, split_z - 2.0))
    assert not top.is_inside((clip_x, inner_wall_y - 0.55, split_z - 2.0))
    assert not raw_top.is_inside((clip_x, inner_wall_y + 0.75, split_z + 1.25))
    assert top.is_inside((clip_x, inner_wall_y + 0.75, split_z + 1.25))
    assert not top.is_inside((clip_x, inner_wall_y + 1.50, split_z + 1.25))


def test_snap_clip_geometry_is_not_a_public_parameter() -> None:
    body = gamepad_body(fillet_radius_mm=0.0)
    mutable_snap_fit_pair = cast(Any, snap_fit_pair)

    for keyword in ("clip_width_mm", "clip_height_mm", "wall_thickness_mm"):
        with pytest.raises(TypeError, match=keyword):
            mutable_snap_fit_pair(body, **{keyword: 8.0})

    frozen_geometry = cast(Any, AUTHORITATIVE_SNAP_GEOMETRY)
    with pytest.raises(FrozenInstanceError):
        frozen_geometry.clip_depth_mm = 8.0


@pytest.mark.parametrize("clearance_mm", [0.20, 0.40, math.inf, math.nan, True])
def test_snap_pair_accepts_only_receiving_clearance_candidates(clearance_mm: float) -> None:
    body = gamepad_body(fillet_radius_mm=0.0)

    with pytest.raises(ValueError, match="tolerance_mm"):
        snap_fit_pair(body, tolerance_mm=clearance_mm)


def test_snap_receiver_uses_the_full_one_sided_clearance_depth() -> None:
    body = gamepad_body(fillet_radius_mm=0.0)
    bounds = body.bounding_box()
    split_z = min(
        bounds.size.Z / 2,
        AUTHORITATIVE_SNAP_GEOMETRY.max_lower_shell_height_mm,
    )
    clearance = 0.30

    _, bottom = snap_fit_pair(body, tolerance_mm=clearance)

    clip_x = bounds.min.X + bounds.size.X * 0.25
    wall_probe_y = bounds.min.Y + 2.0 - 0.25
    receiver_floor = split_z - AUTHORITATIVE_SNAP_GEOMETRY.clip_height_mm - clearance
    assert not bottom.is_inside((clip_x, wall_probe_y, receiver_floor + 0.05))
    assert bottom.is_inside((clip_x, wall_probe_y, receiver_floor - 0.05))


def test_snap_pair_requires_the_fixed_wall_at_every_attachment() -> None:
    outer = cast(
        Part,
        Box(130.0, 80.0, 35.5, align=(Align.CENTER, Align.CENTER, Align.MIN)),
    )
    cavity = cast(
        Part,
        Location((0.0, 0.0, 3.0))
        * Box(124.0, 74.0, 29.5, align=(Align.CENTER, Align.CENTER, Align.MIN)),
    )
    wrong_wall = cast(Part, outer - cavity)

    with pytest.raises(ValueError, match="fixed 2 mm shell wall"):
        snap_fit_pair(wrong_wall)


def test_case_halves_are_oriented_broad_face_down_for_printing() -> None:
    body = gamepad_body(
        length_mm=130.0,
        width_mm=80.0,
        thickness_mm=35.5,
        fillet_radius_mm=0.0,
    )
    assembly_top, assembly_bottom = snap_fit_pair(body)
    original_top_bounds = (
        tuple(assembly_top.bounding_box().min),
        tuple(assembly_top.bounding_box().max),
    )

    print_top, print_bottom = cadkit.parametric.orient_case_halves_for_print(
        assembly_top,
        assembly_bottom,
    )

    assert abs(print_top.bounding_box().min.Z) < 1e-6
    assert abs(print_bottom.bounding_box().min.Z) < 1e-6
    assert print_top.is_inside((0.0, 0.0, 1.0))
    assert not print_top.is_inside((0.0, 0.0, 3.0))
    assert print_bottom.is_inside((0.0, 0.0, 1.0))
    assert not print_bottom.is_inside((0.0, 0.0, 3.0))
    assert (
        tuple(assembly_top.bounding_box().min),
        tuple(assembly_top.bounding_box().max),
    ) == original_top_bounds
    assert len(print_top.solids()) == 1
    assert len(print_bottom.solids()) == 1


def test_gamepad_body_wall_thickness_is_not_a_public_parameter() -> None:
    mutable_gamepad_body = cast(Any, gamepad_body)

    with pytest.raises(TypeError, match="wall_thickness_mm"):
        mutable_gamepad_body(wall_thickness_mm=3.0)


def test_coupon_keepouts_are_non_production_visualizations() -> None:
    assert coupons.feather_keepout_coupon().bounding_box().size.X == 57.0
    assert coupons.pbs33b_directional_keepout_coupon().bounding_box().size.Z == 10.69
    assert coupons.guuzi_b09bmrdptn_action_keepout_coupon().bounding_box().size.Z == 21.5
    assert coupons.pbs33b_terminal_keepout_coupon().bounding_box().size.Z == 5.0
    assert coupons.guuzi_action_terminal_keepout_coupon().bounding_box().size.Z == 6.0
    assert coupons.cube_20mm_coupon().bounding_box().size.Z == 20.0
