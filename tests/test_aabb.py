"""Tests for the immutable AABB / Route / Volume records."""

from __future__ import annotations

import math
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
_session = _here.parent
sys.path.insert(0, str(_session / "tools"))

import pytest  # noqa: E402
from cadkit.aabb import AABB, Route, Volume  # noqa: E402

from tests._pytest_helpers import approx  # noqa: E402


# ---------------------------------------------------------------------------
# AABB — geometric queries
# ---------------------------------------------------------------------------
def test_aabb_overlaps_strictly():
    a = AABB(0, 0, 0, 10, 10, 10)
    b = AABB(5, 5, 5, 15, 15, 15)
    assert a.overlaps(b)
    assert b.overlaps(a)


def test_aabb_does_not_overlap_when_separated():
    a = AABB(0, 0, 0, 10, 10, 10)
    b = AABB(20, 20, 20, 30, 30, 30)
    assert not a.overlaps(b)
    assert not b.overlaps(a)


def test_aabb_touching_face_is_not_overlap():
    """Touching faces are not overlapping (zero-volume intersection)."""
    a = AABB(0, 0, 0, 10, 10, 10)
    b = AABB(10, 0, 0, 20, 10, 10)
    assert not a.overlaps(b)
    assert a.clearance(b) == 0.0


def test_aabb_clearance_when_separated_is_euclidean():
    a = AABB(0, 0, 0, 10, 10, 10)
    b = AABB(13, 14, 0, 23, 24, 10)
    assert a.clearance(b) == approx(5.0, abs=1e-9)  # 3-4-5


def test_aabb_clearance_when_overlapping_returns_zero():
    """Overlapping boxes have a 2D clearance of 0 (the closest
    edges coincide within the 2D intersection)."""
    a = AABB(0, 0, 0, 10, 10, 10)
    b = AABB(5, 5, 5, 15, 15, 15)
    assert a.clearance(b) == 0.0


def test_aabb_clearance_docstring_advises_explicit_gap_for_ligament():
    """The ``clearance`` docstring explicitly directs callers to
    compute the gap from ``min_*`` / ``max_*`` for ligament
    checks; using ``clearance`` for ligament is not supported.
    """
    import inspect

    src = inspect.getsource(AABB.clearance)
    assert "Do not" in src or "ligament" in src.lower()
    # The actual recommended formula is shown in the docstring.
    assert "min(self.min_x - other.max_x" in src or "min_x - other.max_x" in src


def test_aabb_contains_point_inside_and_outside():
    a = AABB(0, 0, 0, 10, 10, 10)
    assert a.contains_point(5, 5, 5)
    assert not a.contains_point(15, 5, 5)


def test_aabb_distance_to_edge():
    a = AABB(0, 0, 0, 10, 10, 10)
    # Inside the AABB: minimum perpendicular distance to an edge.
    assert a.distance_to_edge(5, 5) == 5.0  # 5 mm to nearest edge
    # Outside, projected to the right edge.
    assert a.distance_to_edge(13, 5) == approx(3.0, abs=1e-9)
    # Outside, diagonal.
    assert a.distance_to_edge(13, 14) == approx(5.0, abs=1e-9)


def test_aabb_expand_uniform():
    a = AABB(0, 0, 0, 10, 10, 10)
    b = a.expand_uniform(2.0)
    assert b.min_x == -2.0 and b.max_x == 12.0
    assert b.min_y == -2.0 and b.max_y == 12.0


def test_aabb_rejects_empty_box():
    with pytest.raises(ValueError, match="min > max"):
        AABB(10, 0, 0, 0, 10, 10)


# ---------------------------------------------------------------------------
# Volume — labelled AABB with role
# ---------------------------------------------------------------------------
def test_volume_role_distinctions():
    keep = Volume("feather", AABB(0, 0, 0, 57, 29, 28.7), is_keep_out=True)
    cutout = Volume("usb", AABB(0, 0, 0, 14, 8, 4), is_keep_out=False)
    corridor = Volume(
        "cable",
        AABB(0, 0, 0, 12, 8, 100),
        is_keep_out=False,
        is_cable_corridor=True,
    )
    assert keep.is_keep_out is True
    assert cutout.is_keep_out is False
    assert corridor.is_keep_out is False
    assert corridor.is_cable_corridor is True


def test_volume_rejects_keepout_and_corridor_together():
    with pytest.raises(ValueError, match="both a keep-out and a cable corridor"):
        Volume("bad", AABB(0, 0, 0, 1, 1, 1), is_keep_out=True, is_cable_corridor=True)


# ---------------------------------------------------------------------------
# Route — bend radius + keep-out crossing
# ---------------------------------------------------------------------------
def test_route_minimum_bend_radius_straight_line():
    route = Route(
        "straight",
        waypoints=((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
        min_bend_radius_mm=10.0,
    )
    assert route.minimum_bend_radius() == math.inf


def test_route_minimum_bend_radius_right_angle():
    """A 90° turn with 10 mm segments has a 5 mm minimum bend radius.

    Closed-form: min(n1, n2) / (2 sin(angle/2)) = 10 / (2 sin(45°)) ≈ 7.07.
    """
    route = Route(
        "right_angle",
        waypoints=((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0)),
        min_bend_radius_mm=10.0,
    )
    observed = route.minimum_bend_radius()
    assert observed == approx(10.0 / (2 * math.sin(math.pi / 4)), abs=1e-9)


def test_route_crosses_aabb_inside_inside():
    aabb = AABB(0, 0, 0, 10, 10, 10)
    route = Route(
        "through",
        waypoints=((5.0, 5.0, 5.0), (5.0, 5.0, 5.0)),  # degenerate
        min_bend_radius_mm=1.0,
    )
    # Two-waypoints don't form a segment; the helper needs >=3 to
    # compute bends. Two-waypoint routes still check straight-line
    # AABB intersection via ``crosses_aabb``.
    assert route.crosses_aabb(aabb)


def test_route_crosses_aabb_misses():
    aabb = AABB(100, 100, 100, 110, 110, 110)
    route = Route(
        "miss",
        waypoints=((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0)),
        min_bend_radius_mm=10.0,
    )
    assert not route.crosses_aabb(aabb)


def test_route_requires_at_least_two_waypoints():
    with pytest.raises(ValueError, match="at least two waypoints"):
        Route("bad", waypoints=((0.0, 0.0, 0.0),), min_bend_radius_mm=10.0)
