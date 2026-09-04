"""Simple immutable AABB / volume / route records.

VS-04 review-blocker fix.

The validator's bounding-box / volume / surface heuristics are
**not** geometric proofs (procurement plan §5 row "validate()"). For
production evidence the constraint library uses explicit
:class:`AABB` records with deterministic overlap / distance math —
no Build123d bbox inspection, no float-arithmetic-on-mesh.

Three small immutable records:

* :class:`AABB` — axis-aligned bounding box ``(min_x, min_y, min_z,
  max_x, max_y, max_z)``. Methods: :meth:`overlaps`,
  :meth:`clearance`, :meth:`contains`.
* :class:`Volume` — AABB + name + ``is_keep_out``. The validator
  uses the ``is_keep_out`` flag to apply the right rule
  (keep-outs must not overlap each other; cutouts must not overlap
  keep-outs).
* :class:`Route` — ordered sequence of waypoints + minimum bend
  radius. Used for protected exact-six control and ground routes.
  :meth:`minimum_bend_radius` returns the smallest observed radius.

Math is closed-form, exact (no approximation), and documented.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# AABB
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AABB:
    """Axis-aligned bounding box.

    All six coordinates are in millimetres. The box is closed
    (``min <= max``); an empty box (``min > max`` on any axis) is
    rejected at construction.
    """

    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    def __post_init__(self) -> None:
        if self.min_x > self.max_x or self.min_y > self.max_y or self.min_z > self.max_z:
            raise ValueError(
                f"AABB min > max on at least one axis: "
                f"({self.min_x}, {self.min_y}, {self.min_z}) -> "
                f"({self.max_x}, {self.max_y}, {self.max_z})"
            )

    # ----- Geometric queries -----
    def overlaps(self, other: AABB) -> bool:
        """True iff the two AABBs share any volume.

        Touching faces (zero-volume intersection) are **not**
        considered overlapping — the cutout's edge may lie exactly
        on the keep-out's edge without penalty. Use ``clearance``
        to measure the gap.
        """
        return (
            self.min_x < other.max_x
            and self.max_x > other.min_x
            and self.min_y < other.max_y
            and self.max_y > other.min_y
            and self.min_z < other.max_z
            and self.max_z > other.min_z
        )

    def clearance(self, other: AABB) -> float:
        """Minimum 2D (x, y) gap (mm) between two AABBs.

        Returns 0.0 when the boxes share an edge or face
        (touching). Returns a positive float when the boxes are
        separated (the Euclidean 2D distance between the closest
        edges). Returns 0.0 — not a positive value — when the boxes
        overlap (the 2D intersection is non-empty, so the closest
        2D gap is by definition 0).

        The z axis is **not** considered: the procurement-plan
        validator uses the 3D-aware :meth:`overlaps` and
        edge-to-edge arithmetic in :mod:`cadkit.constraints`
        directly. **Do not** use this method for ligament
        calculations; the semantics are not strong enough.

        Production callers that need a signed gap (positive when
        separated, negative when protruding) should compute the
        gap explicitly from the AABB's ``min_*`` / ``max_*``
        attributes, e.g.::

            gap = min(
                self.min_x - other.max_x,
                other.min_x - self.max_x,
                self.min_y - other.max_y,
                other.min_y - self.max_y,
            )
        """
        # 2D gap on the (x, y) plane.
        dx = max(self.min_x - other.max_x, other.min_x - self.max_x, 0.0)
        dy = max(self.min_y - other.max_y, other.min_y - self.max_y, 0.0)
        if dx == 0.0 and dy == 0.0:
            return 0.0  # touching face
        return math.hypot(dx, dy)

    def contains_point(self, x: float, y: float, z: float) -> bool:
        return (
            self.min_x <= x <= self.max_x
            and self.min_y <= y <= self.max_y
            and self.min_z <= z <= self.max_z
        )

    def distance_to_edge(self, x: float, y: float) -> float:
        """Minimum 2D distance from a point to the nearest AABB edge.

        For an interior point, this is the minimum perpendicular
        distance to one of the four edges:

        ``min(x - min_x, max_x - x, y - min_y, max_y - y)``.

        For an exterior point, it is the Euclidean distance to the
        nearest corner. Used by the cutout / outer-edge ligament
        check.
        """
        dx_left = x - self.min_x
        dx_right = self.max_x - x
        dy_bot = y - self.min_y
        dy_top = self.max_y - y
        if dx_left >= 0 and dx_right >= 0 and dy_bot >= 0 and dy_top >= 0:
            # Interior — perpendicular distance to the nearest edge.
            return min(dx_left, dx_right, dy_bot, dy_top)
        # Exterior — Euclidean distance to the rectangle.
        dx = max(self.min_x - x, 0.0, x - self.max_x)
        dy = max(self.min_y - y, 0.0, y - self.max_y)
        return math.hypot(dx, dy)

    def volume_mm3(self) -> float:
        return (self.max_x - self.min_x) * (self.max_y - self.min_y) * (self.max_z - self.min_z)

    def expand_uniform(self, margin_mm: float) -> AABB:
        """Return a new AABB expanded by ``margin_mm`` on every axis."""
        return AABB(
            min_x=self.min_x - margin_mm,
            min_y=self.min_y - margin_mm,
            min_z=self.min_z - margin_mm,
            max_x=self.max_x + margin_mm,
            max_y=self.max_y + margin_mm,
            max_z=self.max_z + margin_mm,
        )


# ---------------------------------------------------------------------------
# Volume (AABB + name + role)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Volume:
    """A labelled AABB with a keep-out / cutout role.

    The validator distinguishes protected volumes, cutouts, and a
    historical compatibility corridor role:

    * ``is_keep_out=True`` — a protected zone (control underside,
      Feather keep-out, snap receiver, USB cutout). Two keep-outs
      must not overlap.
    * ``is_keep_out=False`` — a hollow volume (button panel
      cutout, USB cutout). The validator checks that the cutout
      does not overlap any keep-out.
    * ``is_cable_corridor=True`` — a retained historical schema flag.
      Current exact-six placements emit a neutral null corridor field
      and do not validate corridor volumes.
    """

    name: str
    aabb: AABB
    is_keep_out: bool = True
    is_cable_corridor: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Volume.name must be non-empty.")
        if self.is_keep_out and self.is_cable_corridor:
            raise ValueError("Volume cannot be both a keep-out and a cable corridor")


# ---------------------------------------------------------------------------
# Route (ordered waypoints + bend radius)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Route:
    """A polyline route with a minimum bend radius.

    Used for protected exact-six signal and common-ground routes. The
    validator enforces:

    * The smallest bend radius between consecutive segments is
      ``>= min_bend_radius_mm``.
    * No segment crosses any keep-out / cutout volume.
    """

    name: str
    waypoints: tuple[tuple[float, float, float], ...]
    min_bend_radius_mm: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Route.name must be non-empty.")
        if len(self.waypoints) < 2:
            raise ValueError(
                f"Route {self.name!r} needs at least two waypoints; got {len(self.waypoints)}"
            )
        if self.min_bend_radius_mm <= 0:
            raise ValueError(
                f"Route {self.name!r}: min_bend_radius_mm "
                f"({self.min_bend_radius_mm}) must be positive"
            )

    def minimum_bend_radius(self) -> float:
        """Return the smallest observed bend radius along the route.

        A bend is the angle between two consecutive segments. The
        radius is computed from the segment length and the turning
        angle; smaller angles = sharper bends.

        Returns ``math.inf`` when the route is a straight line
        (no direction change).
        """
        radii = list(_segment_bend_radii(self.waypoints))
        if not radii:
            return math.inf
        return min(radii)

    def segments(self) -> Iterable[tuple[tuple[float, float, float], tuple[float, float, float]]]:
        """Yield consecutive (start, end) pairs."""
        for i in range(len(self.waypoints) - 1):
            yield self.waypoints[i], self.waypoints[i + 1]

    def crosses_aabb(self, aabb: AABB) -> bool:
        """True iff any segment intersects the AABB.

        Segments are straight lines; we use Liang–Barsky clipping
        for the 3D AABB intersection (closed-form). A segment
        whose endpoints are both inside the AABB counts as
        crossing.
        """
        return any(_segment_intersects_aabb(start, end, aabb) for start, end in self.segments())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _segment_bend_radii(
    waypoints: Sequence[tuple[float, float, float]],
) -> Iterable[float]:
    """Yield bend radii between consecutive segments (mm)."""
    if len(waypoints) < 3:
        return
    for i in range(len(waypoints) - 2):
        a, b, c = waypoints[i], waypoints[i + 1], waypoints[i + 2]
        # Vector b -> a (incoming), b -> c (outgoing).
        v1 = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
        v2 = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
        n1 = math.hypot(*v1)
        n2 = math.hypot(*v2)
        if n1 == 0.0 or n2 == 0.0:
            continue
        cos_t = max(-1.0, min(1.0, sum(v1[k] * v2[k] for k in range(3)) / (n1 * n2)))
        if cos_t >= 0.999999:
            continue  # straight line
        # Turning angle (radians). A right angle -> 90°.
        angle = math.acos(cos_t)
        if angle < 1e-9:
            continue
        # Bend radius = min segment length / 2 sin(angle/2).
        seg_len = min(n1, n2)
        yield seg_len / (2.0 * math.sin(angle / 2.0))


def _segment_intersects_aabb(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    aabb: AABB,
) -> bool:
    """Liang–Barsky-style 3D AABB clipping."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dz = end[2] - start[2]
    t0, t1 = 0.0, 1.0
    for o, d, mn, mx in (
        (start[0], dx, aabb.min_x, aabb.max_x),
        (start[1], dy, aabb.min_y, aabb.max_y),
        (start[2], dz, aabb.min_z, aabb.max_z),
    ):
        if d == 0.0:
            if o < mn or o > mx:
                return False
            continue
        t_enter = (mn - o) / d
        t_exit = (mx - o) / d
        if t_enter > t_exit:
            t_enter, t_exit = t_exit, t_enter
        if t_enter > t0:
            t0 = t_enter
        if t_exit < t1:
            t1 = t_exit
        if t0 > t1:
            return False
    return True


__all__ = ["AABB", "Route", "Volume"]
