"""Bounded, plan-view exterior silhouettes for MIG-04.

The exterior customizer is deliberately a strict value-object and finite
geometry grammar. It cannot receive arbitrary CSG, meshes, source code, URLs,
file paths, or Z coordinates. Every accepted profile is one closed XY face.
New profile presets retain the rounded baseline cavity, while bounded surface
reliefs remove material only from the 2 mm top-wall region.
"""

from __future__ import annotations

import io
import math
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from build123d import (
    Align,
    Box,
    BuildPart,
    BuildSketch,
    Cylinder,
    Face,
    GeomType,
    Line,
    Location,
    Locations,
    Part,
    Plane,
    Polygon,
    RadiusArc,
    RectangleRounded,
    Text,
    ThreePointArc,
    Wire,
    extrude,
    fillet,
    import_svg,
    loft,
    offset,
)

from .snap_geometry import AUTHORITATIVE_SNAP_GEOMETRY

ExteriorProfile = Literal[
    "rounded",
    "snes_inspired",
    "n64_inspired",
    "playstation_inspired",
    "xbox_inspired",
    "switch_inspired",
    "custom",
]
ProtectedOccupancy = Literal["solid", "void", "both"]
EXTERIOR_PROFILES: tuple[ExteriorProfile, ...] = (
    "rounded",
    "snes_inspired",
    "n64_inspired",
    "playstation_inspired",
    "xbox_inspired",
    "switch_inspired",
    "custom",
)
_RELIEF_PROFILE_NAMES = frozenset({"playstation_inspired", "xbox_inspired", "switch_inspired"})
EXTERIOR_WALL_THICKNESS_MM: float = AUTHORITATIVE_SNAP_GEOMETRY.wall_thickness_mm
EXTERIOR_GEOMETRY_TOLERANCE_MM: float = 1e-6

_MAX_ROUNDNESS_MM = 16.0
_MAX_SHOULDER_INSET_MM = 16.0
_MAX_SIDE_GRIP_LENGTH_MM = 35.0
_MAX_SIDE_GRIP_SPLAY_DEG = 30.0
_MAX_CENTER_GRIP_LENGTH_MM = 35.0
_MAX_MELT_DEPTH_MM = 1.5
_MAX_ENGRAVED_TEXT_LEN = 24
_MAX_GROOVE_COUNT = 8
_MAX_GROOVE_DEPTH_MM = 1.5
_MAX_CHAMFER_MM = 5.0
_MAX_TAPER_DEG = 30.0
_MIN_PLAN_PROPORTION = 0.95
_MAX_PLAN_PROPORTION = 0.98
_MIN_THICKNESS_PROPORTION = 0.97
_MAX_THICKNESS_PROPORTION = 0.975
_MAX_EMBLEM_SIZE_MM = 30.0
_MIN_EMBLEM_SIZE_MM = 4.0
_MAX_EMBLEM_SVG_BYTES = 8 * 1024
_MAX_EMBLEM_PATHS = 8
_MAX_EMBLEM_PATH_BYTES = 2 * 1024
_MAX_EMBLEM_PATH_NUMBERS = 256
_MAX_EMBLEM_COORDINATE = 100_000.0
_PROFILE_BOUND_TOLERANCE_MM = 1e-6
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_SVG_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_SVG_PATH_REMAINDER = re.compile(r"[\s,MmLlHhVvCcSsQqTtAaZz]*")
_SVG_FORBIDDEN = re.compile(
    r"<!DOCTYPE|<!ENTITY|<\?(?!xml(?:\s|\?|$))|@import\b|url\s*\(|"
    r"(?:https?|file|data|javascript):|//",
    re.IGNORECASE,
)


def _bounded_number(value: Any, label: str, maximum: float) -> float:
    """Parse one JSON scalar without accepting bools or non-finite values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0 or numeric > maximum:
        raise ValueError(f"{label} must be between 0 and {maximum} mm")
    return numeric


def _optional_bounded_number(value: Any, label: str, maximum: float) -> float | None:
    if value is None:
        return None
    return _bounded_number(value, label, maximum)


def _bounded_proportion(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or (numeric != 1.0 and not minimum <= numeric <= maximum):
        raise ValueError(f"{label} must be 1.0 or between {minimum} and {maximum}")
    return numeric


def _svg_tag(element: Any) -> str:
    raw_tag = str(element.tag)
    if raw_tag.startswith("{"):
        namespace, _, name = raw_tag[1:].partition("}")
        if namespace != _SVG_NAMESPACE:
            raise ValueError("emblem_svg contains an unsupported XML namespace")
        return name
    return raw_tag


def _validate_svg_path_data(path_data: str) -> None:
    if not path_data or len(path_data.encode("utf-8")) > _MAX_EMBLEM_PATH_BYTES:
        raise ValueError("emblem_svg path data exceeds the complexity limit")
    numbers = _SVG_NUMBER.findall(path_data)
    if len(numbers) > _MAX_EMBLEM_PATH_NUMBERS:
        raise ValueError("emblem_svg path data exceeds the complexity limit")
    if any(
        not math.isfinite(number) or abs(number) > _MAX_EMBLEM_COORDINATE
        for number in map(float, numbers)
    ):
        raise ValueError("emblem_svg path coordinates exceed the bounded range")
    remainder = _SVG_NUMBER.sub("", path_data)
    if _SVG_PATH_REMAINDER.fullmatch(remainder) is None or not re.search(r"[Mm]", path_data):
        raise ValueError("emblem_svg contains unsupported SVG path data")
    if not re.search(r"[Zz]", path_data):
        raise ValueError("emblem_svg paths must be closed")


def _parse_emblem_svg(value: Any) -> Any:
    if type(value) is not str or not value.strip():
        raise ValueError("emblem_svg must be a non-empty inline SVG string when present")
    if len(value.encode("utf-8")) > _MAX_EMBLEM_SVG_BYTES:
        raise ValueError("emblem_svg exceeds the payload limit")
    if _SVG_FORBIDDEN.search(value):
        raise ValueError("emblem_svg external references and declarations are not allowed")
    try:
        from defusedxml import ElementTree as ET

        root = ET.fromstring(value)
    except Exception as exc:
        raise ValueError("emblem_svg is invalid or unsafe") from exc
    if _svg_tag(root) != "svg" or set(root.attrib) != {"viewBox"}:
        raise ValueError("emblem_svg requires only a bounded viewBox on its SVG root")
    if (root.text and root.text.strip()) or (root.tail and root.tail.strip()):
        raise ValueError("emblem_svg root cannot contain text")
    return root


def _validate_svg_viewbox(root: Any) -> None:
    viewbox = root.attrib["viewBox"].replace(",", " ").split()
    if len(viewbox) != 4:
        raise ValueError("emblem_svg viewBox must contain four numbers")
    try:
        viewbox_numbers = tuple(float(item) for item in viewbox)
    except ValueError as exc:
        raise ValueError("emblem_svg viewBox must contain four numbers") from exc
    if (
        any(not math.isfinite(item) or abs(item) > 4096.0 for item in viewbox_numbers)
        or viewbox_numbers[2] <= 0.0
        or viewbox_numbers[3] <= 0.0
    ):
        raise ValueError("emblem_svg viewBox exceeds the bounded range")


def _validate_svg_paths(root: Any) -> None:
    paths = list(root)
    if not paths or len(paths) > _MAX_EMBLEM_PATHS:
        raise ValueError("emblem_svg must contain between one and eight paths")
    for path in paths:
        if _svg_tag(path) != "path" or set(path.attrib) - {"d", "fill-rule"}:
            raise ValueError("emblem_svg may contain only path elements")
        if (path.text and path.text.strip()) or (path.tail and path.tail.strip()):
            raise ValueError("emblem_svg path elements cannot contain text")
        if set(path.attrib) == {"fill-rule"} or path.attrib.get("fill-rule", "nonzero") not in {
            "evenodd",
            "nonzero",
        }:
            raise ValueError("emblem_svg paths require bounded path data")
        _validate_svg_path_data(path.attrib.get("d", ""))


def _validate_emblem_svg(value: Any) -> str | None:
    if value is None:
        return None
    root = _parse_emblem_svg(value)
    _validate_svg_viewbox(root)
    _validate_svg_paths(root)
    return value


@dataclass(frozen=True)
class ExteriorDesignSpec:
    """Immutable JSON-friendly request for a bounded XY exterior profile.

    Numeric custom parameters are millimetres unless named as degrees or
    proportions. A custom request whose complete parameter set is a no-op is
    rejected. Parameters on a named built-in profile are rejected rather than
    silently ignored. SVG emblems are inline, path-only data; URLs and paths
    never cross this value-object boundary.
    """

    profile: ExteriorProfile = "rounded"
    roundness_mm: float = 0.0
    shoulder_inset_mm: float = 0.0
    side_grip_length_mm: float = 0.0
    side_grip_splay_deg: float = 0.0
    center_grip_length_mm: float | None = None
    melt_depth_mm: float = 0.0
    engraved_text: str | None = None
    groove_count: int = 0
    groove_depth_mm: float = 0.0
    chamfer_mm: float = 0.0
    taper_deg: float = 0.0
    length_proportion: float = 1.0
    width_proportion: float = 1.0
    thickness_proportion: float = 1.0
    emblem_svg: str | None = None
    emblem_size_mm: float = 0.0

    def __post_init__(self) -> None:
        if type(self.profile) is not str or self.profile not in EXTERIOR_PROFILES:
            raise ValueError(f"unknown exterior profile {self.profile!r}")
        numeric_changed = _normalise_numeric_fields(self)
        center = _normalise_center_grip(self)
        proportions_changed = _normalise_proportions(self)
        _validate_grooves(self)
        engraved_present = _validate_engraving_text(self.engraved_text)
        emblem_present = _normalise_emblem(self)
        if self.profile != "custom":
            _reject_named_parameters(
                self,
                numeric_changed=numeric_changed,
                center=center,
                proportions_changed=proportions_changed,
                engraved_present=engraved_present,
                emblem_present=emblem_present,
            )
            return
        _validate_loft_controls(self, proportions_changed)
        _validate_custom_request(
            self,
            numeric_changed=numeric_changed,
            center=center,
            proportions_changed=proportions_changed,
            engraved_present=engraved_present,
            emblem_present=emblem_present,
        )

    def to_dict(self) -> dict[str, str | float | int | None]:
        """Return the exact bounded representation used by JSON edit plans."""
        return {
            "profile": self.profile,
            "roundness_mm": self.roundness_mm,
            "shoulder_inset_mm": self.shoulder_inset_mm,
            "side_grip_length_mm": self.side_grip_length_mm,
            "side_grip_splay_deg": self.side_grip_splay_deg,
            "center_grip_length_mm": self.center_grip_length_mm,
            "melt_depth_mm": self.melt_depth_mm,
            "engraved_text": self.engraved_text,
            "groove_count": self.groove_count,
            "groove_depth_mm": self.groove_depth_mm,
            "chamfer_mm": self.chamfer_mm,
            "taper_deg": self.taper_deg,
            "length_proportion": self.length_proportion,
            "width_proportion": self.width_proportion,
            "thickness_proportion": self.thickness_proportion,
            "emblem_svg": self.emblem_svg,
            "emblem_size_mm": self.emblem_size_mm,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExteriorDesignSpec:
        """Parse a complete or defaulted JSON-friendly exterior object."""
        raw_value: Any = value
        if not isinstance(raw_value, Mapping):
            raise ValueError("exterior_design must be an object")
        allowed = {
            "profile",
            "roundness_mm",
            "shoulder_inset_mm",
            "side_grip_length_mm",
            "side_grip_splay_deg",
            "center_grip_length_mm",
            "melt_depth_mm",
            "engraved_text",
            "groove_count",
            "groove_depth_mm",
            "chamfer_mm",
            "taper_deg",
            "length_proportion",
            "width_proportion",
            "thickness_proportion",
            "emblem_svg",
            "emblem_size_mm",
        }
        if set(value) - allowed:
            raise ValueError("exterior_design has unknown fields")
        if "profile" not in value:
            raise ValueError("exterior_design requires profile")
        return cls(
            profile=cast(ExteriorProfile, value["profile"]),
            roundness_mm=value.get("roundness_mm", 0.0),
            shoulder_inset_mm=value.get("shoulder_inset_mm", 0.0),
            side_grip_length_mm=value.get("side_grip_length_mm", 0.0),
            side_grip_splay_deg=value.get("side_grip_splay_deg", 0.0),
            center_grip_length_mm=value.get("center_grip_length_mm"),
            melt_depth_mm=value.get("melt_depth_mm", 0.0),
            engraved_text=value.get("engraved_text"),
            groove_count=value.get("groove_count", 0),
            groove_depth_mm=value.get("groove_depth_mm", 0.0),
            chamfer_mm=value.get("chamfer_mm", 0.0),
            taper_deg=value.get("taper_deg", 0.0),
            length_proportion=value.get("length_proportion", 1.0),
            width_proportion=value.get("width_proportion", 1.0),
            thickness_proportion=value.get("thickness_proportion", 1.0),
            emblem_svg=value.get("emblem_svg"),
            emblem_size_mm=value.get("emblem_size_mm", 0.0),
        )


def _normalise_numeric_fields(spec: ExteriorDesignSpec) -> bool:
    values = (
        ("roundness_mm", spec.roundness_mm, _MAX_ROUNDNESS_MM),
        ("shoulder_inset_mm", spec.shoulder_inset_mm, _MAX_SHOULDER_INSET_MM),
        ("side_grip_length_mm", spec.side_grip_length_mm, _MAX_SIDE_GRIP_LENGTH_MM),
        ("side_grip_splay_deg", spec.side_grip_splay_deg, _MAX_SIDE_GRIP_SPLAY_DEG),
        ("melt_depth_mm", spec.melt_depth_mm, _MAX_MELT_DEPTH_MM),
        ("groove_depth_mm", spec.groove_depth_mm, _MAX_GROOVE_DEPTH_MM),
        ("chamfer_mm", spec.chamfer_mm, _MAX_CHAMFER_MM),
        ("taper_deg", spec.taper_deg, _MAX_TAPER_DEG),
        ("emblem_size_mm", spec.emblem_size_mm, _MAX_EMBLEM_SIZE_MM),
    )
    normalised: list[float] = []
    for name, value, maximum in values:
        numeric = _bounded_number(value, name, maximum)
        object.__setattr__(spec, name, numeric)
        normalised.append(numeric)
    return any(value != 0.0 for value in normalised)


def _normalise_center_grip(spec: ExteriorDesignSpec) -> float | None:
    center = _optional_bounded_number(
        spec.center_grip_length_mm,
        "center_grip_length_mm",
        _MAX_CENTER_GRIP_LENGTH_MM,
    )
    object.__setattr__(spec, "center_grip_length_mm", center)
    return center


def _normalise_proportions(spec: ExteriorDesignSpec) -> bool:
    proportions = (
        (
            "length_proportion",
            spec.length_proportion,
            _MIN_PLAN_PROPORTION,
            _MAX_PLAN_PROPORTION,
        ),
        (
            "width_proportion",
            spec.width_proportion,
            _MIN_PLAN_PROPORTION,
            _MAX_PLAN_PROPORTION,
        ),
        (
            "thickness_proportion",
            spec.thickness_proportion,
            _MIN_THICKNESS_PROPORTION,
            _MAX_THICKNESS_PROPORTION,
        ),
    )
    normalised: list[float] = []
    for name, value, minimum, maximum in proportions:
        proportion = _bounded_proportion(value, name, minimum, maximum)
        object.__setattr__(spec, name, proportion)
        normalised.append(proportion)
    return any(value != 1.0 for value in normalised)


def _validate_grooves(spec: ExteriorDesignSpec) -> None:
    if type(spec.groove_count) is not int or isinstance(spec.groove_count, bool):
        raise ValueError("groove_count must be an integer")
    if not 0 <= spec.groove_count <= _MAX_GROOVE_COUNT:
        raise ValueError(f"groove_count must be between 0 and {_MAX_GROOVE_COUNT}")
    if spec.groove_depth_mm > 0.0 and spec.groove_count == 0:
        raise ValueError("groove_depth_mm requires a positive groove_count")
    if spec.groove_count > 0 and spec.groove_depth_mm == 0.0:
        raise ValueError("a positive groove_count requires groove_depth_mm")


def _validate_engraving_text(engraved: Any) -> bool:
    if engraved is None:
        return False
    if type(engraved) is not str or not engraved:
        raise ValueError("engraved_text must be a non-empty string when present")
    if len(engraved) > _MAX_ENGRAVED_TEXT_LEN:
        raise ValueError(f"engraved_text must be at most {_MAX_ENGRAVED_TEXT_LEN} characters")
    if not engraved.isprintable():
        raise ValueError("engraved_text must contain only printable characters")
    return True


def _normalise_emblem(spec: ExteriorDesignSpec) -> bool:
    emblem = _validate_emblem_svg(spec.emblem_svg)
    object.__setattr__(spec, "emblem_svg", emblem)
    if emblem is None and spec.emblem_size_mm != 0.0:
        raise ValueError("emblem_size_mm requires emblem_svg")
    if emblem is not None and spec.emblem_size_mm < _MIN_EMBLEM_SIZE_MM:
        raise ValueError(
            f"emblem_size_mm must be between {_MIN_EMBLEM_SIZE_MM} and "
            f"{_MAX_EMBLEM_SIZE_MM} when emblem_svg is present"
        )
    return emblem is not None


def _reject_named_parameters(
    spec: ExteriorDesignSpec,
    *,
    numeric_changed: bool,
    center: float | None,
    proportions_changed: bool,
    engraved_present: bool,
    emblem_present: bool,
) -> None:
    changed = any(
        (
            numeric_changed,
            center is not None,
            engraved_present,
            spec.groove_count != 0,
            proportions_changed,
            emblem_present,
        )
    )
    if changed:
        raise ValueError(f"custom parameters are irrelevant for profile {spec.profile!r}")


def _validate_loft_controls(spec: ExteriorDesignSpec, proportions_changed: bool) -> None:
    plan_changed = spec.length_proportion != 1.0 or spec.width_proportion != 1.0
    if spec.taper_deg > 0.0 and proportions_changed:
        raise ValueError("taper_deg and explicit case proportions are alternative loft controls")
    if plan_changed and spec.thickness_proportion == 1.0:
        raise ValueError("case length/width proportions require thickness_proportion below 1")
    if spec.thickness_proportion != 1.0 and not plan_changed:
        raise ValueError("thickness_proportion requires a length or width proportion")


def _validate_custom_request(
    spec: ExteriorDesignSpec,
    *,
    numeric_changed: bool,
    center: float | None,
    proportions_changed: bool,
    engraved_present: bool,
    emblem_present: bool,
) -> None:
    if spec.side_grip_length_mm == 0.0 and spec.side_grip_splay_deg != 0.0:
        raise ValueError("side_grip_splay_deg requires a positive side_grip_length_mm")
    top_effect_count = sum(
        (
            spec.melt_depth_mm > 0.0,
            engraved_present,
            spec.groove_count > 0,
            spec.taper_deg > 0.0 or proportions_changed,
            emblem_present,
        )
    )
    if top_effect_count > 1:
        raise ValueError(
            "melt, engraving, grooves, taper/proportions, and emblem top-panel effects are "
            "alternatives"
        )
    changed = any(
        (
            numeric_changed,
            center not in (None, 0.0),
            engraved_present,
            spec.groove_count != 0,
            proportions_changed,
            emblem_present,
        )
    )
    if not changed:
        raise ValueError("custom exterior design cannot be a no-op")


@dataclass(frozen=True)
class ProtectedExteriorRegion:
    """A caller-supplied solid/void occupancy region for geometry comparison."""

    name: str
    region: Part
    occupancy: ProtectedOccupancy = "both"

    def __post_init__(self) -> None:
        raw_name: Any = self.name
        raw_region: Any = self.region
        if not isinstance(raw_name, str) or not raw_name or len(raw_name) > 128:
            raise ValueError("protected region name must be a bounded non-empty string")
        if not isinstance(raw_region, Part):
            raise ValueError("protected region must be a Build123d Part")
        if self.occupancy not in ("solid", "void", "both"):
            raise ValueError("protected region occupancy must be 'solid', 'void', or 'both'")

    @property
    def part(self) -> Part:
        """Compatibility spelling for callers that call regions ``part``."""
        return self.region


def _spec(value: ExteriorDesignSpec | Mapping[str, Any]) -> ExteriorDesignSpec:
    if type(value) is ExteriorDesignSpec:
        return value
    raw_value: Any = value
    if isinstance(raw_value, Mapping):
        return ExteriorDesignSpec.from_dict(cast(Mapping[str, Any], raw_value))
    raise ValueError("exterior_design must be an ExteriorDesignSpec or JSON object")


def _custom_points(
    spec: ExteriorDesignSpec, half_length: float, half_width: float
) -> tuple[tuple[float, float], ...]:
    corner = max(2.0, spec.roundness_mm)
    shoulder = spec.shoulder_inset_mm
    if corner >= min(half_length, half_width) - EXTERIOR_WALL_THICKNESS_MM:
        raise ValueError("custom roundness leaves no valid 2 mm cavity")
    if corner + shoulder >= half_length / 2.0:
        raise ValueError("custom shoulder inset would remove an authoritative snap wall")
    side_length = max(8.0, spec.side_grip_length_mm)
    if side_length > 2.0 * half_width - 2.0 * corner - 4.0:
        raise ValueError("custom side-grip length does not fit the caller envelope")
    splay = min(
        15.0,
        side_length * math.tan(math.radians(spec.side_grip_splay_deg)) / 2.0,
    )
    center = spec.center_grip_length_mm or 0.0
    top_half = max(half_length / 2.0 + 2.0, half_length - corner - shoulder - center / 2.0)
    top_half = min(top_half, half_length - corner)
    shoulder_drop = min(shoulder, half_width / 3.0)
    grip_upper = min(half_width - shoulder_drop - 2.0, -half_width + corner + side_length)
    shoulder_transition = min(8.0, max(3.0, shoulder_drop + 2.0))
    side_x = half_length - splay
    if shoulder_drop == 0.0 and top_half >= side_x:
        # A splayed side can reach the top inboard of ``top_half``. Keeping
        # both points would retrace the top edge and make the polygon invalid.
        top_half = side_x
    points = [
        (-half_length + corner, -half_width),
        (half_length - corner, -half_width),
        (half_length, -half_width + corner),
        (half_length, grip_upper),
        (side_x, grip_upper + shoulder_transition),
        (side_x, half_width - shoulder_drop),
    ]
    if points[-1] != (top_half, half_width):
        points.append((top_half, half_width))
    points.append((-top_half, half_width))
    left_shoulder = (-side_x, half_width - shoulder_drop)
    if points[-1] != left_shoulder:
        points.append(left_shoulder)
    points.extend(
        [
            (-side_x, grip_upper + shoulder_transition),
            (-half_length, grip_upper),
            (-half_length, -half_width + corner),
        ]
    )
    return tuple(points)


def _single_profile_face(points: tuple[tuple[float, float], ...]) -> Face:
    if len(points) < 3 or len(set(points)) != len(points):
        raise ValueError("exterior profile must contain distinct closed-boundary points")
    try:
        with BuildSketch(Plane.XY) as sketch:
            Polygon(*points, align=Align.NONE)
        faces = sketch.sketch.faces()
    except Exception as exc:
        raise ValueError("exterior profile could not be constructed") from exc
    if len(faces) != 1 or not faces[0].is_valid or faces[0].area <= 0.0:
        raise ValueError("exterior profile must be one valid connected face")
    return faces[0]


def _custom_profile_face(spec: ExteriorDesignSpec, length: float, width: float) -> Face:
    """Round a bounded custom outline so ``roundness_mm`` is geometric."""
    outline_values = (
        spec.roundness_mm,
        spec.shoulder_inset_mm,
        spec.center_grip_length_mm or 0.0,
    )
    if not any(outline_values):
        return _rounded_profile_face(length, width)
    profile = _single_profile_face(_custom_points(spec, length / 2.0, width / 2.0))
    if spec.roundness_mm == 0.0:
        return profile
    remaining = spec.roundness_mm
    while remaining >= 0.25:
        try:
            rounded = profile.fillet_2d(remaining, profile.vertices())
            if rounded.is_valid and any(
                edge.geom_type == GeomType.CIRCLE for edge in rounded.edges()
            ):
                return rounded
        except Exception:  # noqa: S110 - reduce the bounded radius and retry
            pass
        remaining = round(remaining / 2.0, 3)
    return profile


def _rounded_profile_face(length: float, width: float, *, radius_mm: float = 12.0) -> Face:
    radius = min(radius_mm, width / 2.0 - 2.5)
    try:
        with BuildSketch(Plane.XY) as sketch:
            RectangleRounded(length, width, radius)
        faces = sketch.sketch.faces()
    except Exception as exc:
        raise ValueError("rounded exterior profile could not be constructed") from exc
    if len(faces) != 1 or not faces[0].is_valid or faces[0].area <= 0.0:
        raise ValueError("rounded exterior profile must be one valid connected face")
    return faces[0]


def _snes_profile_face(length: float, width: float) -> Face:
    """Build the smooth, oval-like SNES plan-view silhouette."""
    return _rounded_profile_face(length, width, radius_mm=18.0)


def _n64_profile_face(length: float, width: float) -> Face:
    """Build a bounded three-lobed lower edge without entering protected space."""
    half_length, half_width = length / 2.0, width / 2.0
    if (half_length, half_width) != (65.0, 45.0):
        raise ValueError("n64_inspired currently requires the canonical case frame")
    edges = (
        RadiusArc((-65.0, -33.0), (-53.0, -45.0), 12.0),
        Line((-53.0, -45.0), (-17.0, -45.0)),
        ThreePointArc((-17.0, -45.0), (-9.0, -44.0), (-1.0, -45.0)),
        Line((-1.0, -45.0), (1.0, -45.0)),
        ThreePointArc((1.0, -45.0), (9.0, -44.0), (17.0, -45.0)),
        Line((17.0, -45.0), (53.0, -45.0)),
        RadiusArc((53.0, -45.0), (65.0, -33.0), 12.0),
        Line((65.0, -33.0), (65.0, 33.0)),
        RadiusArc((65.0, 33.0), (53.0, 45.0), 12.0),
        Line((53.0, 45.0), (-53.0, 45.0)),
        RadiusArc((-53.0, 45.0), (-65.0, 33.0), 12.0),
        Line((-65.0, 33.0), (-65.0, -33.0)),
    )
    try:
        profile = Face(Wire(edges))
    except Exception as exc:
        raise ValueError("n64_inspired exterior profile could not be constructed") from exc
    if len(profile.faces()) != 1 or not profile.is_valid or profile.area <= 0.0:
        raise ValueError("n64_inspired exterior profile must be one valid connected face")
    return profile


def _melted_profile_face(length: float, width: float, depth: float) -> Face:
    """Make a visibly drooped lower silhouette within the fixed case frame."""
    half_length, half_width = length / 2.0, width / 2.0
    corner = min(12.0, half_length / 4.0, half_width / 4.0)
    outer_x = half_length - corner
    lobe_x = half_length * 17.0 / 65.0
    valley_x = half_length * 9.0 / 65.0
    centre_x = half_length / 65.0
    valley_y = -half_width + min(1.0, depth)
    edges = (
        RadiusArc((-half_length, -half_width + corner), (-outer_x, -half_width), corner),
        Line((-outer_x, -half_width), (-lobe_x, -half_width)),
        ThreePointArc(
            (-lobe_x, -half_width),
            (-valley_x, valley_y),
            (-centre_x, -half_width),
        ),
        Line((-centre_x, -half_width), (centre_x, -half_width)),
        ThreePointArc(
            (centre_x, -half_width),
            (valley_x, valley_y),
            (lobe_x, -half_width),
        ),
        Line((lobe_x, -half_width), (outer_x, -half_width)),
        RadiusArc((outer_x, -half_width), (half_length, -half_width + corner), corner),
        Line((half_length, -half_width + corner), (half_length, half_width - corner)),
        RadiusArc((half_length, half_width - corner), (outer_x, half_width), corner),
        Line((outer_x, half_width), (-outer_x, half_width)),
        RadiusArc((-outer_x, half_width), (-half_length, half_width - corner), corner),
        Line((-half_length, half_width - corner), (-half_length, -half_width + corner)),
    )
    try:
        profile = Face(Wire(edges))
    except Exception as exc:
        raise ValueError("melted exterior profile could not be constructed") from exc
    if len(profile.faces()) != 1 or not profile.is_valid or profile.area <= 0.0:
        raise ValueError("melted exterior profile must be one valid connected face")
    return profile


def _playstation_profile_face(length: float, width: float) -> Face:
    """Retain the safe rounded frame beneath PlayStation-inspired relief."""
    return _rounded_profile_face(length, width)


def _xbox_profile_face(length: float, width: float) -> Face:
    """Retain the safe rounded frame beneath Xbox-inspired relief."""
    return _rounded_profile_face(length, width)


def _switch_profile_face(length: float, width: float) -> Face:
    """Retain the safe rounded frame beneath Switch-inspired relief."""
    return _rounded_profile_face(length, width)


def _profile_face(spec: ExteriorDesignSpec, length: float, width: float) -> Face:
    builders = {
        "rounded": _rounded_profile_face,
        "snes_inspired": _snes_profile_face,
        "n64_inspired": _n64_profile_face,
        "playstation_inspired": _playstation_profile_face,
        "xbox_inspired": _xbox_profile_face,
        "switch_inspired": _switch_profile_face,
    }
    builder = builders.get(spec.profile)
    if builder is not None:
        return builder(length, width)
    outline_parameters = (
        spec.roundness_mm,
        spec.shoulder_inset_mm,
        spec.side_grip_length_mm,
        spec.side_grip_splay_deg,
        spec.center_grip_length_mm or 0.0,
    )
    if spec.melt_depth_mm > 0.0 and not any(outline_parameters):
        return _melted_profile_face(length, width, spec.melt_depth_mm)
    return _custom_profile_face(spec, length, width)


def build_exterior_profile(
    design: ExteriorDesignSpec | Mapping[str, Any],
    *,
    length_mm: float,
    width_mm: float,
) -> Face:
    """Build one validated closed XY face inside the caller's exact frame."""
    spec = _spec(design)
    length = _bounded_number(length_mm, "length_mm", 1_000_000.0)
    width = _bounded_number(width_mm, "width_mm", 1_000_000.0)
    if min(length, width) <= 2.0 * EXTERIOR_WALL_THICKNESS_MM:
        raise ValueError("exterior frame is too small for the authoritative 2 mm wall")
    face = _profile_face(spec, length, width)
    bounds = face.bounding_box()
    expected = (length, width)
    if any(
        abs(observed - target) > _PROFILE_BOUND_TOLERANCE_MM
        for observed, target in zip((bounds.size.X, bounds.size.Y), expected, strict=True)
    ):
        raise ValueError("exterior profile changed the caller-provided XY envelope")
    return face


def build_exterior_body(
    design: ExteriorDesignSpec | Mapping[str, Any],
    *,
    length_mm: float,
    width_mm: float,
    thickness_mm: float,
    fillet_radius_mm: float = 2.0,
) -> Part:
    """Build a strict, one-solid shell from a bounded exterior profile.

    ``fillet_radius_mm`` is applied to both extrusions with bounded step-down
    fallback when OpenCascade cannot construct the requested radius.
    """
    length = _bounded_number(length_mm, "length_mm", 1_000_000.0)
    width = _bounded_number(width_mm, "width_mm", 1_000_000.0)
    thickness = _bounded_number(thickness_mm, "thickness_mm", 1_000_000.0)
    fillet_radius = _bounded_number(fillet_radius_mm, "fillet_radius_mm", 1_000_000.0)
    if min(length, width, thickness) <= 2.0 * EXTERIOR_WALL_THICKNESS_MM:
        raise ValueError("exterior dimensions cannot contain a 2 mm shell wall and cavity")
    spec = _spec(design)
    profile = build_exterior_profile(spec, length_mm=length, width_mm=width)
    try:
        outer = _strict_fillet(extrude(profile, amount=thickness), fillet_radius, "outer")
        inward = offset(profile, amount=-EXTERIOR_WALL_THICKNESS_MM)
        inner_faces = inward.faces()
        if len(inner_faces) != 1 or not inner_faces[0].is_valid:
            raise ValueError("2 mm inward offset must produce one valid connected face")
        cavity = Location((0.0, 0.0, EXTERIOR_WALL_THICKNESS_MM)) * _strict_fillet(
            extrude(inner_faces[0], amount=thickness - 2.0 * EXTERIOR_WALL_THICKNESS_MM),
            fillet_radius,
            "cavity",
        )
        body = cast(Part, outer - cavity)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("exterior profile offset or extrusion failed") from exc
    if len(outer.solids()) != 1 or len(cavity.solids()) != 1:
        raise ValueError("exterior shell construction must contain one outer and cavity solid")
    if not body.is_valid or len(body.solids()) != 1 or body.volume <= 0.0:
        raise ValueError("exterior shell construction produced invalid or multiple solids")
    bounds = body.bounding_box()
    expected_bounds = (length, width, thickness)
    if any(
        abs(observed - expected) > _PROFILE_BOUND_TOLERANCE_MM
        for observed, expected in zip(
            (bounds.size.X, bounds.size.Y, bounds.size.Z), expected_bounds, strict=True
        )
    ):
        raise ValueError("exterior shell changed the caller-provided envelope or Z stack")
    if body.volume >= outer.volume:
        raise ValueError("exterior shell cavity collapsed")
    if _has_body_customization(spec):
        body = _apply_decorations(
            body,
            profile,
            spec,
            length=length,
            width=width,
            thickness=thickness,
            edge_rolloff=fillet_radius,
        )
    return body


def _has_body_customization(spec: ExteriorDesignSpec) -> bool:
    return any(
        (
            spec.melt_depth_mm > 0.0,
            spec.engraved_text is not None,
            spec.groove_count > 0,
            spec.chamfer_mm > 0.0,
            spec.taper_deg > 0.0,
            spec.length_proportion != 1.0,
            spec.width_proportion != 1.0,
            spec.emblem_svg is not None,
            spec.side_grip_length_mm > 0.0,
            spec.profile in _RELIEF_PROFILE_NAMES,
        )
    )


def _apply_decorations(
    body: Part,
    profile: Face,
    spec: ExteriorDesignSpec,
    *,
    length: float,
    width: float,
    thickness: float,
    edge_rolloff: float,
) -> Part:
    """Apply bounded surface decorations to a validated hollow shell."""
    decorated = body
    if spec.profile in _RELIEF_PROFILE_NAMES:
        decorated = _apply_named_profile_relief(
            decorated,
            spec.profile,
            length,
            width,
            thickness,
        )
    if spec.side_grip_length_mm > 0.0:
        decorated = _apply_wing_relief(
            decorated,
            spec.side_grip_length_mm,
            spec.side_grip_splay_deg,
            length,
            width,
            thickness,
        )
    if spec.taper_deg > 0.0 or spec.length_proportion != 1.0 or spec.width_proportion != 1.0:
        decorated = _apply_upper_loft(
            decorated,
            spec,
            width=width,
            thickness=thickness,
        )
    if spec.melt_depth_mm > 0.0:
        decorated = _apply_melt(decorated, spec.melt_depth_mm, length, width, thickness)
    if spec.engraved_text is not None:
        decorated = _apply_engraving(decorated, spec.engraved_text, length, width, thickness)
    if spec.groove_count > 0:
        decorated = _apply_grooves(
            decorated, spec.groove_count, spec.groove_depth_mm, length, width, thickness
        )
    if spec.chamfer_mm > 0.0:
        decorated = _apply_chamfer(
            decorated,
            profile,
            spec.chamfer_mm,
            length=length,
            width=width,
            thickness=thickness,
            edge_rolloff=edge_rolloff,
        )
    if spec.emblem_svg is not None:
        decorated = _apply_svg_emblem(
            decorated,
            spec.emblem_svg,
            spec.emblem_size_mm,
            width,
            thickness,
        )
    if not decorated.is_valid or len(decorated.solids()) != 1 or decorated.volume <= 0.0:
        raise ValueError("exterior decoration produced invalid or multiple solids")
    return decorated


def _try_wing_relief(
    body: Part,
    grip_length: float,
    splay_deg: float,
    length: float,
    width: float,
    thickness: float,
    step: float,
) -> Part | None:
    relief_length = min(18.0, max(8.0, grip_length * 0.75)) * step
    depth = 1.4 * step
    x_offset = min(length * 0.36, length / 2.0 - relief_length / 2.0 - 4.0)
    result = body
    try:
        tool = Box(
            relief_length,
            6.0,
            depth,
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        )
        for x, angle in ((-x_offset, -splay_deg), (x_offset, splay_deg)):
            placed = Location((x, width / 3.0, thickness - depth / 2.0), (0.0, 0.0, angle)) * tool
            result = cast(Part, result - placed)
    except Exception:
        return None
    if not result.is_valid or len(result.solids()) != 1 or result.volume >= body.volume - 1e-6:
        return None
    return result


def _apply_wing_relief(
    body: Part,
    grip_length: float,
    splay_deg: float,
    length: float,
    width: float,
    thickness: float,
) -> Part:
    """Carve a symmetric pair of top-wall wing accents without moving the outline."""
    step = 1.0
    while step >= 0.25:
        result = _try_wing_relief(
            body,
            grip_length,
            splay_deg,
            length,
            width,
            thickness,
            step,
        )
        if result is not None:
            return result
        step /= 2.0
    return body


def _top_panel_parameters(spec: ExteriorDesignSpec) -> tuple[float, float, float, float]:
    panel_length = 36.0 * spec.length_proportion
    panel_width = 16.0 * spec.width_proportion
    depth = 1.0 if spec.taper_deg > 0.0 else min(1.5, 40.0 * (1.0 - spec.thickness_proportion))
    draft = max(0.4, depth * math.tan(math.radians(spec.taper_deg)))
    return panel_length, panel_width, depth, draft


def _top_panel_face(length: float, width: float, y: float, z: float) -> Face:
    with BuildSketch(Plane.XY.offset(z)) as panel_sketch, Locations((0.0, y)):
        RectangleRounded(length, width, min(2.0, width / 4.0))
    faces = panel_sketch.sketch.faces()
    if len(faces) != 1 or not faces[0].is_valid:
        raise ValueError("top-panel loft section must be one valid face")
    return faces[0]


def _try_top_panel_loft(
    body: Part,
    *,
    panel_length: float,
    panel_width: float,
    panel_y: float,
    thickness: float,
    depth: float,
    draft: float,
    step: float,
) -> Part | None:
    cut_depth = depth * step
    inset = draft * step
    try:
        lower = _top_panel_face(
            panel_length - 2.0 * inset,
            panel_width - 2.0 * inset,
            panel_y,
            thickness - cut_depth,
        )
        upper = _top_panel_face(panel_length, panel_width, panel_y, thickness + 0.05)
        result = cast(Part, body - loft([lower, upper]))
    except Exception:
        return None
    if not result.is_valid or len(result.solids()) != 1 or result.volume >= body.volume - 1e-6:
        return None
    return result


def _try_upper_loft(
    body: Part,
    profile: Face,
    *,
    length: float,
    width: float,
    thickness: float,
    start_z: float,
    inset_x: float,
    inset_y: float,
) -> Part | None:
    scale_x = (length - 2.0 * inset_x) / length
    scale_y = (width - 2.0 * inset_y) / width
    try:
        lower = extrude(profile, amount=start_z)
        lower_section = Location((0.0, 0.0, start_z)) * profile
        top_profile = profile.scale((scale_x, scale_y, 1.0), about=(0.0, 0.0, 0.0))
        upper_section = Location((0.0, 0.0, thickness)) * top_profile
        envelope = cast(Part, lower + loft([lower_section, upper_section]))
        result = cast(Part, body & envelope)
    except Exception:
        return None
    if (
        not result.is_valid
        or len(result.solids()) != 1
        or result.volume <= 0.0
        or result.volume >= body.volume - 1e-6
    ):
        return None
    return result


def _apply_upper_loft(
    body: Part,
    spec: ExteriorDesignSpec,
    *,
    width: float,
    thickness: float,
) -> Part:
    """Carve a symmetric lofted upper panel without moving the fixed frame."""
    panel_length, panel_width, depth, draft = _top_panel_parameters(spec)
    step = 1.0
    while step >= 0.125:
        result = _try_top_panel_loft(
            body,
            panel_length=panel_length,
            panel_width=panel_width,
            panel_y=width / 3.0,
            thickness=thickness,
            depth=depth,
            draft=draft,
            step=step,
        )
        if result is not None:
            return result
        step /= 2.0
    return body


def _apply_melt(body: Part, depth: float, length: float, width: float, thickness: float) -> Part:
    """Sag the top panel into a shallow concave dish, staying inside the frame.

    A large horizontal cylinder scoops a trough across the top so the surface
    reads as softly melted/drooped.  The cut is subtractive and ``depth`` is
    bounded to stay within the 2 mm top wall, so the electronics cavity and the
    caller envelope are unchanged.
    """
    sag_span = min(90.0, length - 24.0)
    radius = (sag_span * sag_span) / (8.0 * depth) + depth / 2.0
    try:
        with BuildPart() as sag_build, Locations((0.0, 0.0, thickness + radius - depth)):
            Cylinder(radius, width + 40.0, rotation=(90.0, 0.0, 0.0))
        with BuildPart() as mask_build, Locations((0.0, width / 2.0 - 7.0, thickness - depth)):
            Box(
                sag_span,
                min(12.0, width - 8.0),
                depth + 2.0,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
        sag = cast(Part, cast(Part, sag_build.part) & cast(Part, mask_build.part))
    except Exception as exc:
        raise ValueError("melt sag could not be constructed") from exc
    if not sag.is_valid or len(sag.solids()) != 1:
        raise ValueError("melt sag solid could not be constructed")
    try:
        result = cast(Part, body - sag)
    except Exception as exc:
        raise ValueError("melt decoration could not be applied") from exc
    if not result.is_valid or len(result.solids()) != 1:
        raise ValueError("melt decoration produced invalid geometry; use a smaller melt_depth_mm")
    return result


def _apply_engraving(body: Part, text: str, length: float, width: float, thickness: float) -> Part:
    """Subtract a shallow centred text engraving from the top panel."""
    engrave_depth = 1.5
    font_size = min(15.0, 60.0 / max(1, len(text)))
    font = "Arial" if sys.platform in {"darwin", "win32"} else "Liberation Sans"
    try:
        with (
            BuildSketch(Plane.XY.offset(thickness - engrave_depth)) as sketch,
            Locations((0.0, width / 3.0)),
        ):
            Text(text, font_size=font_size, font=font, align=(Align.CENTER, Align.CENTER))
        faces = sketch.sketch.faces()
        if not faces or not all(face.is_valid for face in faces):
            raise ValueError("text sketch must produce valid faces")
        text_solid = extrude(sketch.sketch, amount=engrave_depth)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("text engraving could not be constructed") from exc
    try:
        result = cast(Part, body - text_solid)
    except Exception as exc:
        raise ValueError("text engraving could not be applied") from exc
    if not result.is_valid or len(result.solids()) != 1:
        raise ValueError("text engraving produced invalid geometry; use shorter text")
    return result


def _try_svg_emblem(
    body: Part,
    svg: str,
    size_mm: float,
    width: float,
    thickness: float,
) -> Part | None:
    try:
        imported = import_svg(
            io.StringIO(svg),
            align=(Align.CENTER, Align.CENTER),
        )
        if (
            not imported
            or len(imported) > _MAX_EMBLEM_PATHS
            or not all(
                isinstance(shape, Face) and shape.is_valid and shape.area > 0.0
                for shape in imported
            )
        ):
            return None
        bounds = [shape.bounding_box() for shape in imported]
        span_x = max(bound.max.X for bound in bounds) - min(bound.min.X for bound in bounds)
        span_y = max(bound.max.Y for bound in bounds) - min(bound.min.Y for bound in bounds)
        source_size = max(span_x, span_y)
        if not math.isfinite(source_size) or source_size <= 0.0:
            return None
        scale = size_mm / source_size
        result = body
        for imported_face in imported:
            face = cast(Face, imported_face)
            scaled = face.scale(scale, about=(0.0, 0.0, 0.0))
            cutting_face = Location((0.0, width / 3.0, thickness - 1.0)) * scaled
            result = cast(Part, result - extrude(cutting_face, amount=1.05))
    except Exception:
        return None
    if (
        not result.is_valid
        or len(result.solids()) != 1
        or result.volume <= 0.0
        or result.volume >= body.volume - 1e-6
    ):
        return None
    return result


def _apply_svg_emblem(
    body: Part,
    svg: str,
    size_mm: float,
    width: float,
    thickness: float,
) -> Part:
    """Engrave validated inline SVG paths at the symmetric top-panel centre."""
    remaining_size = size_mm
    while remaining_size >= _MIN_EMBLEM_SIZE_MM:
        result = _try_svg_emblem(body, svg, remaining_size, width, thickness)
        if result is not None:
            return result
        remaining_size = round(remaining_size / 2.0, 3)
    return body


def _apply_grooves(
    body: Part, count: int, depth: float, length: float, width: float, thickness: float
) -> Part:
    """Carve ``count`` parallel horizontal grooves into the top panel."""
    groove_width = 1.5
    groove_length = min(36.0, length - 8.0)
    groove_span = min(12.0, width / 4.0)
    gap = groove_span / (count + 1)
    center_y = width / 3.0
    with BuildPart() as groove_build:
        for index in range(1, count + 1):
            y = center_y - groove_span / 2.0 + gap * index
            with Locations((0.0, y, thickness - depth / 2.0)):
                Box(
                    groove_length,
                    groove_width,
                    depth,
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                )
    grooves = cast(Part, groove_build.part)
    if not grooves.is_valid:
        raise ValueError("groove decoration could not be constructed")
    try:
        result = cast(Part, body - grooves)
    except Exception as exc:
        raise ValueError("groove decoration could not be applied") from exc
    if not result.is_valid or len(result.solids()) != 1:
        raise ValueError("groove decoration produced invalid geometry")
    return result


def _apply_named_profile_relief(
    body: Part,
    profile: ExteriorProfile,
    length: float,
    width: float,
    thickness: float,
) -> Part:
    """Apply conservative style cues while retaining the rounded 2 mm shell."""
    if profile == "playstation_inspired":
        return _apply_wing_relief(body, 18.0, 10.0, length, width, thickness)
    if profile == "xbox_inspired":
        return _apply_grooves(body, 2, 1.5, length, width, thickness)
    return _apply_grooves(body, 4, 1.0, length, width, thickness)


def _apply_chamfer(
    body: Part,
    profile: Face,
    chamfer_mm: float,
    *,
    length: float,
    width: float,
    thickness: float,
    edge_rolloff: float,
) -> Part:
    """Remove a symmetric top-edge bevel without touching the cavity."""
    step = 1.0
    while step >= 0.125:
        result = _try_upper_loft(
            body,
            profile,
            length=length,
            width=width,
            thickness=thickness,
            start_z=thickness - EXTERIOR_WALL_THICKNESS_MM,
            inset_x=(edge_rolloff + chamfer_mm) * step,
            inset_y=(edge_rolloff + chamfer_mm) * step,
        )
        if result is not None:
            return result
        step /= 2.0
    return body


def _strict_fillet(part: Part, radius: float, label: str) -> Part:
    """Fillet ``part``, stepping down the radius on failure rather than rejecting.

    A requested radius may not fit a profile with sharp shoulders or grips.
    Instead of failing the whole exterior, the largest successful radius (down to
    zero) is applied so the shell still builds and only its roundness is reduced.
    """
    if radius == 0.0:
        return part
    remaining = radius
    while remaining >= 0.25:
        try:
            raw_result: Any = fillet(part.edges(), remaining)
            if (
                isinstance(raw_result, Part)
                and raw_result.is_valid
                and len(raw_result.solids()) == 1
            ):
                return raw_result
        except Exception:  # noqa: S110 - step down the radius on failure
            pass
        remaining = round(remaining / 2.0, 3)
    return part


exterior_profile = build_exterior_profile


__all__ = [
    "EXTERIOR_GEOMETRY_TOLERANCE_MM",
    "EXTERIOR_PROFILES",
    "EXTERIOR_WALL_THICKNESS_MM",
    "ExteriorDesignSpec",
    "ExteriorProfile",
    "ProtectedExteriorRegion",
    "ProtectedOccupancy",
    "build_exterior_body",
    "build_exterior_profile",
    "exterior_profile",
]
