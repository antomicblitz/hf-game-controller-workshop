"""Functional exact-six CAD fixtures shared by endpoint tests."""

from __future__ import annotations

from functools import lru_cache
from typing import cast

from build123d import (
    Align,
    BuildPart,
    Cylinder,
    Locations,
    Mode,
    Part,
    add,  # pyright: ignore[reportUnknownVariableType]
)
from cadkit.assembly import AssemblyScene, build_demo_assembly, placements_from_assembly
from cadkit.constraints import Placements
from cadkit.parametric import gamepad_body

_DEFAULT_SCENE = build_demo_assembly()
_DEFAULT_PLACEMENTS = placements_from_assembly(_DEFAULT_SCENE)
_DEFAULT_FIXTURE_KEY = (
    (
        _DEFAULT_SCENE.case_dimensions.x,
        _DEFAULT_SCENE.case_dimensions.y,
        _DEFAULT_SCENE.case_dimensions.z,
    ),
    _DEFAULT_PLACEMENTS.control_centres_xy,
    _DEFAULT_PLACEMENTS.control_cutout_diameters_mm,
)


@lru_cache(maxsize=1)
def canonical_placements() -> Placements:
    """Return the active controller placement record used by integration tests."""
    return placements_from_assembly(build_demo_assembly())


@lru_cache(maxsize=1)
def synthetic_tall_placements() -> Placements:
    """Return a viable 98 mm synthetic placement record for production-gate tests."""
    return placements_from_assembly(build_demo_assembly((130.0, 90.0, 98.0)))


def exact_six_case_part(scene: AssemblyScene | None = None) -> Part:
    """Build a hollow test case whose six panel openings match ``scene``."""
    active_scene = scene or build_demo_assembly()
    dimensions = active_scene.case_dimensions
    placements = placements_from_assembly(active_scene)
    key = (
        (dimensions.x, dimensions.y, dimensions.z),
        placements.control_centres_xy,
        placements.control_cutout_diameters_mm,
    )
    if key == _DEFAULT_FIXTURE_KEY:
        return _default_exact_six_case_part()
    return _build_exact_six_case_part(*key)


@lru_cache(maxsize=1)
def _default_exact_six_case_part() -> Part:
    """Retain only the canonical BRep reused by most integration tests."""
    return _build_exact_six_case_part(*_DEFAULT_FIXTURE_KEY)


def _build_exact_six_case_part(
    dimensions: tuple[float, float, float],
    control_centres_xy: tuple[tuple[float, float], ...],
    control_cutout_diameters_mm: tuple[float, ...],
) -> Part:
    """Build one functional fixture for a non-canonical moved scene."""
    length, width, thickness = dimensions
    body = gamepad_body(
        length_mm=length,
        width_mm=width,
        thickness_mm=thickness,
        fillet_radius_mm=0.0,
    )
    with BuildPart() as build:
        add(body)
        for (x, y), diameter in zip(
            control_centres_xy,
            control_cutout_diameters_mm,
            strict=True,
        ):
            with Locations((x - length / 2, y - width / 2, thickness - 4)):
                Cylinder(
                    diameter / 2,
                    4.0,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                    mode=Mode.SUBTRACT,
                )
    return cast(Part, build.part)


__all__ = ["canonical_placements", "exact_six_case_part", "synthetic_tall_placements"]
