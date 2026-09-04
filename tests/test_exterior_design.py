"""Behavioral tests for the bounded MIG-04 exterior-design kernel."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import cast

import pytest
from build123d import Align, Box, GeomType, Location, Part

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

from cadkit import (  # noqa: E402
    ExteriorDesignSpec,
    ProtectedExteriorRegion,
    build_exterior_profile,
    gamepad_body,
    orient_case_halves_for_print,
    snap_fit_pair,
    validate_exterior_geometry,
)


def _design(profile: str) -> ExteriorDesignSpec:
    return ExteriorDesignSpec(profile=profile)  # type: ignore[arg-type]


def test_named_profiles_change_geometry_not_only_metadata() -> None:
    bodies = [
        gamepad_body(exterior_design=_design(profile))
        for profile in (
            "rounded",
            "snes_inspired",
            "n64_inspired",
            "playstation_inspired",
            "xbox_inspired",
            "switch_inspired",
        )
    ]

    assert len({round(body.volume, 3) for body in bodies}) == 6
    assert all(len(body.solids()) == 1 and body.is_valid for body in bodies)


def test_new_named_profiles_only_subtract_from_the_rounded_shell_and_keep_its_cavity() -> None:
    baseline = gamepad_body(exterior_design=ExteriorDesignSpec(profile="rounded"))

    for profile in ("playstation_inspired", "xbox_inspired", "switch_inspired"):
        candidate = gamepad_body(exterior_design=_design(profile))
        assert cast(Part, candidate - baseline).volume < 1e-5
        assert cast(Part, baseline - candidate).volume >= 150.0
        assert not candidate.is_inside((0.0, 0.0, 32.5))


def test_rounded_profile_contains_actual_curved_plan_view_edges() -> None:
    profile = build_exterior_profile(_design("rounded"), length_mm=130.0, width_mm=90.0)

    assert any(edge.geom_type == GeomType.CIRCLE for edge in profile.edges())


@pytest.mark.parametrize(
    "profile",
    [
        "snes_inspired",
        "n64_inspired",
        "playstation_inspired",
        "xbox_inspired",
        "switch_inspired",
    ],
)
def test_named_profiles_have_smooth_plan_view_edges(profile: str) -> None:
    profile_face = build_exterior_profile(_design(profile), length_mm=130.0, width_mm=90.0)

    assert sum(edge.geom_type == GeomType.CIRCLE for edge in profile_face.edges()) >= 2


def test_n64_profile_has_three_distinct_lower_grip_lobes() -> None:
    profile = build_exterior_profile(_design("n64_inspired"), length_mm=130.0, width_mm=90.0)

    # The two smooth valleys leave left, centre, and right lower lobes.
    assert profile.is_inside((-30.0, -44.5, 0.0))
    assert not profile.is_inside((-9.0, -44.5, 0.0))
    assert profile.is_inside((0.0, -44.5, 0.0))
    assert not profile.is_inside((9.0, -44.5, 0.0))
    assert profile.is_inside((30.0, -44.5, 0.0))


def test_custom_profile_keeps_exact_frame_and_authoritative_shell_stack() -> None:
    design = ExteriorDesignSpec(
        profile="custom",
        roundness_mm=8.0,
        shoulder_inset_mm=6.0,
        side_grip_length_mm=20.0,
        side_grip_splay_deg=10.0,
        center_grip_length_mm=15.0,
    )
    body = gamepad_body(
        length_mm=130.0,
        width_mm=90.0,
        thickness_mm=65.0,
        exterior_design=design,
    )
    profile = build_exterior_profile(design, length_mm=130.0, width_mm=90.0)

    assert tuple(round(value, 6) for value in body.bounding_box().size) == (130.0, 90.0, 65.0)
    assert any(edge.geom_type == GeomType.CIRCLE for edge in profile.edges())
    assert body.is_inside((0.0, 0.0, 1.0))
    assert not body.is_inside((0.0, 0.0, 32.5))
    assert body.is_inside((0.0, 0.0, 64.0))
    assert len(body.solids()) == 1


def test_custom_profile_applies_fillet_strictly_instead_of_falling_back() -> None:
    design = ExteriorDesignSpec(
        profile="custom",
        roundness_mm=8.0,
        shoulder_inset_mm=6.0,
        side_grip_length_mm=20.0,
        side_grip_splay_deg=10.0,
        center_grip_length_mm=15.0,
    )
    sharp = gamepad_body(exterior_design=design, fillet_radius_mm=0.0)
    filleted = gamepad_body(exterior_design=design, fillet_radius_mm=2.0)

    assert not math.isclose(filleted.volume, sharp.volume, abs_tol=1e-6)
    with pytest.raises(ValueError, match="fillet_radius_mm"):
        gamepad_body(exterior_design=design, fillet_radius_mm=-1.0)


def test_custom_profile_supports_a_bounded_splayed_side_grip() -> None:
    splayed = ExteriorDesignSpec(
        profile="custom",
        side_grip_length_mm=20.0,
        side_grip_splay_deg=30.0,
    )

    profile = build_exterior_profile(splayed, length_mm=130.0, width_mm=90.0)

    assert profile.is_valid and profile.area > 0.0
    bounds = profile.bounding_box().size
    assert (round(bounds.X, 6), round(bounds.Y, 6)) == (130.0, 90.0)


def test_custom_splayed_side_grip_can_build_one_shell_body() -> None:
    design = ExteriorDesignSpec(
        profile="custom",
        roundness_mm=6.0,
        side_grip_length_mm=20.0,
        side_grip_splay_deg=5.0,
    )

    body = gamepad_body(exterior_design=design)

    assert tuple(round(value, 6) for value in body.bounding_box().size) == (130.0, 90.0, 65.0)
    assert body.is_valid and len(body.solids()) == 1


def _assert_top_wall_subtraction(baseline: Part, candidate: Part) -> None:
    added = cast(Part, candidate - baseline)
    removed = cast(Part, baseline - candidate)

    assert added.volume < 1e-5
    assert removed.volume > 1e-3
    assert removed.bounding_box().min.Z >= 63.0 - 1e-5
    assert tuple(round(value, 6) for value in candidate.bounding_box().size) == (
        130.0,
        90.0,
        65.0,
    )


def test_side_grip_request_carves_symmetric_wing_relief_in_the_top_wall() -> None:
    baseline = gamepad_body(exterior_design=ExteriorDesignSpec(profile="rounded"))
    winged = gamepad_body(
        exterior_design=ExteriorDesignSpec(
            profile="custom",
            side_grip_length_mm=20.0,
            side_grip_splay_deg=10.0,
        )
    )

    _assert_top_wall_subtraction(baseline, winged)
    for x in (-46.8, 46.8):
        assert not winged.is_inside((x, 30.0, 64.6))


def test_taper_deg_uses_a_subtractive_top_wall_loft() -> None:
    baseline_design = ExteriorDesignSpec(profile="custom", roundness_mm=4.0)
    tapered_design = ExteriorDesignSpec(profile="custom", roundness_mm=4.0, taper_deg=18.0)
    baseline = gamepad_body(exterior_design=baseline_design)
    tapered = gamepad_body(exterior_design=tapered_design)

    _assert_top_wall_subtraction(baseline, tapered)
    assert not tapered.is_inside((0.0, 0.0, 32.5))


def test_case_proportions_independently_shape_the_bounded_upper_wall() -> None:
    baseline = gamepad_body(exterior_design=ExteriorDesignSpec(profile="custom", roundness_mm=4.0))
    length_shaped = gamepad_body(
        exterior_design=ExteriorDesignSpec(
            profile="custom",
            roundness_mm=4.0,
            length_proportion=0.98,
            thickness_proportion=0.975,
        )
    )
    width_shaped = gamepad_body(
        exterior_design=ExteriorDesignSpec(
            profile="custom",
            roundness_mm=4.0,
            width_proportion=0.98,
            thickness_proportion=0.975,
        )
    )
    deeper_loft = gamepad_body(
        exterior_design=ExteriorDesignSpec(
            profile="custom",
            roundness_mm=4.0,
            length_proportion=0.98,
            thickness_proportion=0.97,
        )
    )

    for candidate in (length_shaped, width_shaped, deeper_loft):
        _assert_top_wall_subtraction(baseline, candidate)
    assert deeper_loft.volume < length_shaped.volume


EMBLEM_SVG = '<svg viewBox="0 0 20 20"><path d="M 10 1 L 19 10 L 10 19 L 1 10 Z"/></svg>'


def test_inline_svg_emblem_is_a_bounded_centred_subtractive_engraving() -> None:
    baseline_design = ExteriorDesignSpec(profile="custom", roundness_mm=4.0)
    baseline = gamepad_body(exterior_design=baseline_design)
    small = gamepad_body(
        exterior_design=ExteriorDesignSpec(
            profile="custom",
            roundness_mm=4.0,
            emblem_svg=EMBLEM_SVG,
            emblem_size_mm=12.0,
        )
    )
    large = gamepad_body(
        exterior_design=ExteriorDesignSpec(
            profile="custom",
            roundness_mm=4.0,
            emblem_svg=EMBLEM_SVG,
            emblem_size_mm=24.0,
        )
    )

    _assert_top_wall_subtraction(baseline, small)
    _assert_top_wall_subtraction(baseline, large)
    assert large.volume < small.volume
    assert not small.is_inside((0.0, 30.0, 64.5))
    assert small.is_inside((8.0, 30.0, 64.5))


def test_loft_and_svg_kernel_failures_step_down_without_raw_cad_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cadkit.exterior_design as exterior_module

    baseline = gamepad_body(exterior_design=ExteriorDesignSpec(profile="custom", roundness_mm=4.0))

    def fail_cad(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("raw kernel failure")

    monkeypatch.setattr(exterior_module, "loft", fail_cad)
    tapered = gamepad_body(
        exterior_design=ExteriorDesignSpec(profile="custom", roundness_mm=4.0, taper_deg=18.0)
    )
    monkeypatch.setattr(exterior_module, "import_svg", fail_cad)
    emblem = gamepad_body(
        exterior_design=ExteriorDesignSpec(
            profile="custom",
            roundness_mm=4.0,
            emblem_svg=EMBLEM_SVG,
            emblem_size_mm=24.0,
        )
    )

    assert math.isclose(tapered.volume, baseline.volume, abs_tol=1e-6)
    assert math.isclose(emblem.volume, baseline.volume, abs_tol=1e-6)


def test_surface_decorations_build_and_stay_inside_the_frame() -> None:
    for design in (
        ExteriorDesignSpec(profile="custom", melt_depth_mm=1.0),
        ExteriorDesignSpec(profile="custom", engraved_text="SNES"),
        ExteriorDesignSpec(profile="custom", groove_count=4, groove_depth_mm=1.0),
        ExteriorDesignSpec(profile="custom", chamfer_mm=3.0),
        ExteriorDesignSpec(
            profile="custom",
            melt_depth_mm=1.0,
            chamfer_mm=2.0,
        ),
    ):
        body = gamepad_body(exterior_design=design)
        assert body.is_valid and len(body.solids()) == 1
        bounds = body.bounding_box().size
        assert bounds.X <= 130.001 and bounds.Y <= 90.001 and bounds.Z <= 65.001
        top, bottom = snap_fit_pair(body)
        assert top.is_valid and bottom.is_valid


def test_overlapping_top_panel_effects_are_rejected_instead_of_becoming_geometry_noops() -> None:
    with pytest.raises(ValueError, match="top-panel effects are alternatives"):
        ExteriorDesignSpec(
            profile="custom",
            melt_depth_mm=1.0,
            engraved_text="ABC",
        )


def test_melt_deforms_a_broad_top_strip_instead_of_a_hidden_local_dimple() -> None:
    baseline = gamepad_body(exterior_design=ExteriorDesignSpec(profile="rounded"))
    melted = gamepad_body(exterior_design=ExteriorDesignSpec(profile="custom", melt_depth_mm=1.5))

    removed = cast(Part, baseline - melted)

    assert baseline.is_inside((9.0, -44.5, 32.5))
    assert not melted.is_inside((9.0, -44.5, 32.5))
    top_removed = cast(
        Part,
        removed
        & Location((0.0, 0.0, 64.0))
        * Box(130.0, 90.0, 2.0, align=(Align.CENTER, Align.CENTER, Align.MIN)),
    )
    assert top_removed.bounding_box().size.X >= 80.0
    assert top_removed.bounding_box().size.Y >= 10.0


def test_short_engraving_removes_enough_material_to_read_in_the_preview() -> None:
    baseline = gamepad_body(exterior_design=ExteriorDesignSpec(profile="rounded"))
    engraved = gamepad_body(
        exterior_design=ExteriorDesignSpec(profile="custom", engraved_text="TEST")
    )

    removed = cast(Part, baseline - engraved)

    assert removed.volume >= 40.0
    assert removed.bounding_box().size.X >= 30.0


def test_groove_and_chamfer_only_requests_produce_visible_subtraction() -> None:
    baseline = gamepad_body(exterior_design=ExteriorDesignSpec(profile="custom", roundness_mm=4.0))
    for design in (
        ExteriorDesignSpec(
            profile="custom",
            roundness_mm=4.0,
            groove_count=4,
            groove_depth_mm=1.0,
        ),
        ExteriorDesignSpec(profile="custom", roundness_mm=4.0, chamfer_mm=3.0),
    ):
        decorated = gamepad_body(exterior_design=design)
        assert decorated.volume < baseline.volume - 1e-3
        assert decorated.is_valid and len(decorated.solids()) == 1


def test_named_profiles_reject_custom_decoration_parameters() -> None:
    for profile in (
        "rounded",
        "snes_inspired",
        "n64_inspired",
        "playstation_inspired",
        "xbox_inspired",
        "switch_inspired",
    ):
        for kwargs in (
            {"melt_depth_mm": 1.0},
            {"engraved_text": "HI"},
            {"groove_count": 2, "groove_depth_mm": 1.0},
            {"chamfer_mm": 2.0},
            {"taper_deg": 10.0},
            {"length_proportion": 0.98, "thickness_proportion": 0.975},
            {"width_proportion": 0.98, "thickness_proportion": 0.975},
            {"emblem_svg": EMBLEM_SVG, "emblem_size_mm": 12.0},
        ):
            with pytest.raises(ValueError, match="irrelevant for profile"):
                ExteriorDesignSpec(profile=profile, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "profile",
    [
        "rounded",
        "snes_inspired",
        "n64_inspired",
        "playstation_inspired",
        "xbox_inspired",
        "switch_inspired",
    ],
)
def test_named_profiles_split_and_orient_as_support_free_shells(profile: str) -> None:
    body = gamepad_body(exterior_design=_design(profile))
    top, bottom = snap_fit_pair(body)
    print_top, print_bottom = orient_case_halves_for_print(top, bottom)

    assert len(top.solids()) == 1 and len(bottom.solids()) == 1
    assert abs(print_top.bounding_box().min.Z) <= 1e-6
    assert abs(print_bottom.bounding_box().min.Z) <= 1e-6
    assert len(print_top.solids()) == 1 and len(print_bottom.solids()) == 1


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"profile": "free_form"}, "unknown exterior profile"),
        ({"profile": "rounded", "roundness_mm": True}, "roundness_mm"),
        ({"profile": "rounded", "roundness_mm": math.nan}, "roundness_mm"),
        ({"profile": "rounded", "side_grip_length_mm": 2.0}, "irrelevant"),
        (
            {
                "profile": "custom",
                "roundness_mm": 0.0,
                "shoulder_inset_mm": 0.0,
                "side_grip_length_mm": 0.0,
                "side_grip_splay_deg": 0.0,
            },
            "no-op",
        ),
        (
            {
                "profile": "custom",
                "roundness_mm": 0.0,
                "shoulder_inset_mm": 0.0,
                "side_grip_length_mm": 0.0,
                "side_grip_splay_deg": 8.0,
            },
            "requires",
        ),
        ({"profile": "custom", "taper_deg": 31.0}, "taper_deg"),
        (
            {
                "profile": "custom",
                "length_proportion": 0.94,
                "thickness_proportion": 0.975,
            },
            "length_proportion",
        ),
        ({"profile": "custom", "width_proportion": 0.94}, "width_proportion"),
        ({"profile": "custom", "thickness_proportion": 0.96}, "thickness_proportion"),
        ({"profile": "custom", "length_proportion": 0.98}, "require"),
        ({"profile": "custom", "thickness_proportion": 0.975}, "requires"),
        ({"profile": "custom", "emblem_size_mm": 12.0}, "requires emblem_svg"),
        ({"profile": "custom", "emblem_svg": EMBLEM_SVG}, "emblem_size_mm"),
        (
            {
                "profile": "custom",
                "emblem_svg": '<svg viewBox="0 0 10 10"><script/></svg>',
                "emblem_size_mm": 12.0,
            },
            "only path",
        ),
        (
            {
                "profile": "custom",
                "emblem_svg": '<svg viewBox="0 0 10 10"><path href="https://example.test/x" '
                'd="M 0 0 L 1 0 L 1 1 Z"/></svg>',
                "emblem_size_mm": 12.0,
            },
            "external references",
        ),
    ],
)
def test_exterior_spec_rejects_invalid_json_values(value: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ExteriorDesignSpec.from_dict(value)


def test_exterior_profile_rejects_aggressive_parameters_for_small_frame() -> None:
    aggressive = ExteriorDesignSpec(
        profile="custom",
        roundness_mm=16.0,
        shoulder_inset_mm=16.0,
        side_grip_length_mm=35.0,
        side_grip_splay_deg=30.0,
    )

    with pytest.raises(ValueError, match=r"valid 2 mm cavity|does not fit"):
        build_exterior_profile(aggressive, length_mm=40.0, width_mm=30.0)


def test_exterior_comparison_preserves_protected_occupancy_and_rejects_changes() -> None:
    baseline = gamepad_body(exterior_design=_design("rounded"))
    candidate = gamepad_body(exterior_design=_design("snes_inspired"))
    protected = ProtectedExteriorRegion(
        "top-control-opening",
        Location((55.0, 44.0, 4.0)) * Box(2.0, 2.0, 2.0, align=Align.CENTER),
        occupancy="both",
    )

    assert validate_exterior_geometry(baseline, baseline, protected_regions=[protected]) == []
    violations = validate_exterior_geometry(baseline, candidate, protected_regions=[protected])
    assert any(violation.rule == "exterior_protected_occupancy_changed" for violation in violations)

    changed_frame = gamepad_body(length_mm=129.0, exterior_design=_design("rounded"))
    frame_violations = validate_exterior_geometry(baseline, changed_frame)
    assert any(violation.rule == "exterior_envelope_changed" for violation in frame_violations)


def test_exterior_comparison_rejects_equal_volume_material_relocation() -> None:
    frame = Box(20.0, 20.0, 10.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    left_notch = Location((-8.0, 0.0, 0.0)) * Box(
        4.0,
        4.0,
        10.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    right_notch = Location((8.0, 0.0, 0.0)) * Box(
        4.0,
        4.0,
        10.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    baseline = cast(Part, frame - left_notch)
    candidate = cast(Part, frame - right_notch)
    protected = Box(20.0, 8.0, 10.0, align=(Align.CENTER, Align.CENTER, Align.MIN))

    assert math.isclose((baseline & protected).volume, (candidate & protected).volume, abs_tol=1e-6)
    violations = validate_exterior_geometry(baseline, candidate, protected_regions=[protected])

    assert any(violation.rule == "exterior_protected_occupancy_changed" for violation in violations)


def test_exterior_comparison_rejects_multiple_solids_and_invalid_regions() -> None:
    baseline = gamepad_body(exterior_design=_design("rounded"))
    with_two_solids = Part(
        [
            baseline,
            Location((200.0, 0.0, 0.0)) * Box(1.0, 1.0, 1.0, align=Align.CENTER),
        ]
    )
    topology = validate_exterior_geometry(baseline, with_two_solids)
    assert any(violation.rule == "exterior_topology_invalid" for violation in topology)

    invalid_region = validate_exterior_geometry(baseline, baseline, protected_regions=[None])  # type: ignore[list-item]
    assert any(
        violation.rule == "exterior_protected_region_invalid" for violation in invalid_region
    )


SANDBOX_SOURCE_FIXTURE = """
from cadkit import ExteriorDesignSpec, gamepad_body

case = gamepad_body(
    length_mm=40.0,
    width_mm=30.0,
    thickness_mm=20.0,
    exterior_design=ExteriorDesignSpec(profile="rounded"),
    fillet_radius_mm=0.0,
)
assert tuple(round(value, 6) for value in case.bounding_box().size) == (40.0, 30.0, 20.0)
assert len(case.solids()) == 1
"""


def test_sandbox_source_fixture_imports_and_executes_public_api(tmp_path: Path) -> None:
    from build123d import Part
    from cadkit.case_execution import execute_case_path, sandbox_status

    if not sandbox_status().available:
        pytest.skip("OS sandbox backend unavailable")
    source = tmp_path / "case.py"
    source.write_text(SANDBOX_SOURCE_FIXTURE, encoding="utf-8")
    artifact = execute_case_path(source)

    assert isinstance(artifact, Part)
    assert tuple(round(value, 6) for value in artifact.bounding_box().size) == (40.0, 30.0, 20.0)
    assert len(artifact.solids()) == 1
