"""Property tests for geometry and HID invariants."""

from __future__ import annotations

from cadkit.aabb import AABB
from firmware.gpio_map import CONTROL_IDS
from firmware.hid_report import (
    ACTION_A_HID_BUTTON,
    ACTION_B_HID_BUTTON,
    HID_AXIS_MAX,
    HID_AXIS_MIN,
    HID_AXIS_NEUTRAL,
    compute_hid_report,
)
from hypothesis import given
from hypothesis import strategies as st

_coordinate = st.floats(
    min_value=-1_000,
    max_value=1_000,
    allow_nan=False,
    allow_infinity=False,
)
_extent = st.floats(
    min_value=0.01,
    max_value=1_000,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _aabbs(draw: st.DrawFn) -> AABB:
    min_x = draw(_coordinate)
    min_y = draw(_coordinate)
    min_z = draw(_coordinate)
    return AABB(
        min_x=min_x,
        min_y=min_y,
        min_z=min_z,
        max_x=min_x + draw(_extent),
        max_y=min_y + draw(_extent),
        max_z=min_z + draw(_extent),
    )


@given(first=_aabbs(), second=_aabbs(), margin_mm=st.floats(min_value=0, max_value=100))
def test_aabb_queries_preserve_geometric_invariants(
    first: AABB,
    second: AABB,
    margin_mm: float,
) -> None:
    """Overlap/clearance are symmetric and expansion contains the source."""
    assert first.overlaps(second) is second.overlaps(first)
    assert first.clearance(second) == second.clearance(first)
    assert first.clearance(second) >= 0

    expanded = first.expand_uniform(margin_mm)
    assert expanded.contains_point(first.min_x, first.min_y, first.min_z)
    assert expanded.contains_point(first.max_x, first.max_y, first.max_z)
    assert expanded.volume_mm3() >= first.volume_mm3()


_control_states = st.fixed_dictionaries({control_id: st.booleans() for control_id in CONTROL_IDS})


@given(
    control_states=_control_states,
)
def test_hid_reports_preserve_axis_and_button_invariants(
    control_states: dict[str, bool],
) -> None:
    """All input states yield cardinal axes and a bounded, faithful mask."""
    report = compute_hid_report(control_states)

    assert report.x in {HID_AXIS_MIN, HID_AXIS_NEUTRAL, HID_AXIS_MAX}
    assert report.y in {HID_AXIS_MIN, HID_AXIS_NEUTRAL, HID_AXIS_MAX}
    if control_states["left"] and control_states["right"]:
        assert report.x == HID_AXIS_NEUTRAL
    if control_states["up"] and control_states["down"]:
        assert report.y == HID_AXIS_NEUTRAL

    action_a_mask = 1 << (ACTION_A_HID_BUTTON - 1)
    action_b_mask = 1 << (ACTION_B_HID_BUTTON - 1)
    expected_mask = (action_a_mask if control_states["action_a"] else 0) | (
        action_b_mask if control_states["action_b"] else 0
    )
    assert report.buttons == expected_mask
