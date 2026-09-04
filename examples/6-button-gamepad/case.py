"""Approved exact-six gamepad case for the student workshop.

The active controller has four JZK PBS-33B directional buttons and two GUUZI
B09BMRDPTN action buttons.  The canonical case-local assembly is the source of
truth for all control positions, routes, and placement metadata.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypedDict, cast

_session = Path(__file__).resolve().parents[2]
_tools = _session / "tools"
sys.path.insert(0, str(_tools))

from build123d import (  # noqa: E402
    BuildPart,
    Location,
    Locations,
    Mode,
    Part,
    add,  # pyright: ignore[reportUnknownVariableType]
)
from cadkit import (  # noqa: E402
    ExteriorDesignSpec,
    control_mount_gauge,
    gamepad_body,
    orient_case_halves_for_print,
    snap_fit_pair,
)
from cadkit.assembly import (  # noqa: E402
    AssemblyScene,
    build_demo_assembly,
    case_local_to_build123d,
    placements_from_assembly,
)
from cadkit.component_proxies import shared_usb_opening  # noqa: E402
from cadkit.constraints import (  # noqa: E402
    has_fatal,
    validate,
)
from cadkit.render import export_stl_part  # noqa: E402

FEATHER_VARIANT = "nrf52840_sense_pid4516"
CASE_LENGTH_MM = 130.0
CASE_WIDTH_MM = 90.0
CASE_THICKNESS_MM = 65.0
FILLET_RADIUS_MM = 6.0
EXTERIOR_DESIGN = ExteriorDesignSpec(profile="rounded")


class ControlSpec(TypedDict):
    id: str
    role: str
    component: str
    x: float
    y: float
    size_mm: float


CONTROLS: list[ControlSpec] = [
    {
        "id": "up",
        "role": "UP",
        "component": "pbs33b_directional",
        "x": 35.0,
        "y": 60.0,
        "size_mm": 12.4,
    },
    {
        "id": "down",
        "role": "DOWN",
        "component": "pbs33b_directional",
        "x": 35.0,
        "y": 20.0,
        "size_mm": 12.4,
    },
    {
        "id": "right",
        "role": "RIGHT",
        "component": "pbs33b_directional",
        "x": 55.0,
        "y": 40.0,
        "size_mm": 12.4,
    },
    {
        "id": "left",
        "role": "LEFT",
        "component": "pbs33b_directional",
        "x": 15.0,
        "y": 40.0,
        "size_mm": 12.4,
    },
    {
        "id": "action_a",
        "role": "ACTION_A",
        "component": "guuzi_b09bmrdptn_action",
        "x": 105.0,
        "y": 30.0,
        "size_mm": 12.4,
    },
    {
        "id": "action_b",
        "role": "ACTION_B",
        "component": "guuzi_b09bmrdptn_action",
        "x": 105.0,
        "y": 55.0,
        "size_mm": 12.4,
    },
]

USB_SIDE = "left"
CONTROL_POSITIONS: list[tuple[float, float]] = [
    (control["x"], control["y"]) for control in CONTROLS
]
CONTROL_DIAMETERS: list[float] = [control["size_mm"] for control in CONTROLS]

ASSEMBLY_SPEC: AssemblyScene = build_demo_assembly(
    (CASE_LENGTH_MM, CASE_WIDTH_MM, CASE_THICKNESS_MM),
    buttons=CONTROLS,
    case_fillet_radius_mm=FILLET_RADIUS_MM,
)
PLACEMENTS = placements_from_assembly(ASSEMBLY_SPEC)


def _local_location(
    position: tuple[float, float, float],
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Location:
    centered = case_local_to_build123d(
        position,
        (CASE_LENGTH_MM, CASE_WIDTH_MM, CASE_THICKNESS_MM),
    )
    return Location(centered, rotation_deg)


def _assembly_position(node_id: str) -> tuple[float, float, float]:
    return ASSEMBLY_SPEC.node(node_id).transform.position


def build_case() -> Part:
    """Build the exact-six case body and its cavities."""
    body = gamepad_body(
        length_mm=CASE_LENGTH_MM,
        width_mm=CASE_WIDTH_MM,
        thickness_mm=CASE_THICKNESS_MM,
        palm_rest_angle_deg=15.0,
        fillet_radius_mm=FILLET_RADIUS_MM,
        feather_variant=FEATHER_VARIANT,
        exterior_design=EXTERIOR_DESIGN,
    )
    opening = ASSEMBLY_SPEC.node("usb.opening")
    opening_dimensions = opening.physical_dimensions
    opening_position = opening.transform.position
    opening_tool = shared_usb_opening(
        (opening_dimensions.x, opening_dimensions.y, opening_dimensions.z)
    )

    with BuildPart() as build:
        add(body)

        for control in CONTROLS:
            position = _assembly_position(f"control.{control['id']}")
            mount = control_mount_gauge(component_id=control["component"])
            cutout_start_z = CASE_THICKNESS_MM - mount.bounding_box().size.Z
            with Locations(_local_location((position[0], position[1], cutout_start_z))):
                add(mount, mode=Mode.SUBTRACT)

        # The USB opening is authored once on the unsplit shell.  Its z-span
        # straddles the authoritative split plane, so snap_fit_pair() carries
        # the same aligned opening into both the lower and upper halves.
        with Locations(_local_location(opening_position, opening.transform.rotation_deg)):
            add(
                opening_tool,
                mode=Mode.SUBTRACT,
            )

    return cast(Part, build.part)


case: Part = build_case()
violations = validate(case, placements=PLACEMENTS)


def main() -> int:
    """Build and export both snap-fit halves when run as a script."""
    case_for_main = case
    print("Building exact-six gamepad case...")
    print(f"  body bounding box: {case_for_main.bounding_box().size}")
    print(f"  violations: {len(violations)}")
    for violation in violations:
        print(
            f"    [{violation.severity}] {violation.category}/{violation.rule}: {violation.message}"
        )

    if has_fatal(violations):
        print("FATAL violations — fix before printing.")
        return 1

    assembly_top, assembly_bottom = snap_fit_pair(case_for_main)
    top, bottom = orient_case_halves_for_print(assembly_top, assembly_bottom)
    out_dir = _session / "controller" / "default-stl"
    out_dir.mkdir(parents=True, exist_ok=True)
    export_stl_part(top, out_dir / "case-top.stl")
    export_stl_part(bottom, out_dir / "case-bottom.stl")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
