"""Frozen geometry evidence — every required hard rule, sourced.

VS-04 review-blocker fix.

The procurement plan §5 row "validate()" requires every hard print /
fit rule to be sourced from **structured evidence**, not
constants-only. This module exposes an immutable
:class:`FeatureMetrics` dataclass that carries the measured /
frozen value for every required threshold. Production loads a
:class:`GeometryEvidence` bound to a real provenance JSON file via
:class:`FrozenEvidenceRef` (gate ``"geometry"``); design-time may
fall back to a synthetic ``None`` payload that emits advisory
warnings only.

The ``gate_name="geometry"`` binding is documented in
``docs/provenance-schema.md`` (the workshop gate enum is closed;
new gates must be added in a follow-up VS-XX). The orchestrator
augments the receiving checklist's existing gates with this new
``geometry`` gate, signs it after the slicer / manual analysis
completes, and the production pipeline binds to it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .evidence import (
    FrozenEvidenceRef,
)


# ---------------------------------------------------------------------------
# Feature metrics — the immutable record of every hard threshold
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FeatureMetrics:
    """Every required hard-rule threshold, sourced from frozen evidence.

    Fields correspond to procurement plan §5 rows "Structural
    thickness", "Decorative thickness", "Small features",
    "Ligament", "Supports", "Overhang", "Bridge", "Button
    mounting land", "Button keep-out", "USB", "Snap clearance".

    Any field set to ``None`` represents a missing metric. The
    validator surfaces a hard ``metric_unverified`` error when a
    metric required for the requested export grade is missing.
    """

    # Structural thicknesses (mm).
    wall_thickness_mm: float | None = None
    top_thickness_mm: float | None = None
    bottom_thickness_mm: float | None = None
    snap_root_thickness_mm: float | None = None

    # Decorative thickness (mm) and small-feature minima.
    decorative_thickness_mm: float | None = None
    through_hole_min_diameter_mm: float | None = None
    decorative_stroke_min_width_mm: float | None = None
    emboss_min_depth_mm: float | None = None
    engrave_min_depth_mm: float | None = None

    # Ligament + bridge + overhang.
    ligament_min_mm: float | None = None
    max_bridge_mm: float | None = None
    max_overhang_deg: float | None = None

    # Supports (bool). Required to be ``False`` for production; the
    # validator surfaces a hard ``supports_unverified`` error if
    # ``None``.
    supports_used: bool | None = None

    # Button + USB.
    button_local_mounting_land_thickness_mm: float | None = None
    usb_ligament_mm: float | None = None

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if name == "supports_used":
                if value is not None and not isinstance(value, bool):
                    raise ValueError("geometry metric supports_used must be a boolean or None")
                continue
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"geometry metric {name} must be a finite number or None")


@dataclass(frozen=True)
class GeometryBinding:
    """Hashes tying frozen geometry analysis to one production export."""

    source_sha: str
    top_stl_sha256: str
    bottom_stl_sha256: str
    printer_id: str
    profile_sha256: str = ""
    gcode_sha256: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", self.source_sha):
            raise ValueError("geometry binding source_sha must be a 7-64 character Git SHA")
        for name in ("top_stl_sha256", "bottom_stl_sha256", "profile_sha256", "gcode_sha256"):
            value = getattr(self, name)
            if value and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"geometry binding {name} must be lowercase 64-hex")


# ---------------------------------------------------------------------------
# GeometryEvidence — bound to a FrozenEvidenceRef
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GeometryEvidence:
    """A :class:`FrozenEvidenceRef` plus a parsed :class:`FeatureMetrics`.

    Construction is via :meth:`load`, which reads the file, validates
    the gate binding (``"geometry"``), and parses the
    ``computed.geometry_metrics`` block. Missing fields stay
    ``None``; the validator surfaces them as unverified errors at
    export time.
    """

    ref: FrozenEvidenceRef
    metrics: FeatureMetrics
    binding: GeometryBinding | None = None

    @classmethod
    def load(cls, path: FrozenEvidenceRef | Path | str) -> GeometryEvidence:
        """Load a real provenance JSON file and bind it to gate ``geometry``.

        Raises:
            ProvenanceError (and subclasses): when the file is
                missing / not FROZEN / has no ``geometry`` gate / has
                ``passed != True`` / has empty sign-off.
            KeyError / ValueError: when ``computed.geometry_metrics``
                is missing or has wrong types.
        """
        ref = (
            path
            if isinstance(path, FrozenEvidenceRef)
            else FrozenEvidenceRef.load(path, gate_name="geometry")
        )
        metrics = _parse_geometry_metrics(ref.content)
        return cls(ref=ref, metrics=metrics, binding=_parse_geometry_binding(ref.content))

    def revalidate(self) -> None:
        """Re-read the file and re-compute the SHA-256 (delegated)."""
        self.ref.revalidate()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def _parse_geometry_metrics(content: Mapping[str, Any]) -> FeatureMetrics:
    """Parse the ``computed.geometry_metrics`` block.

    Returns a :class:`FeatureMetrics` with every field ``None`` when
    the block is missing or empty. Each field is validated as a
    positive float (or boolean for ``supports_used``) — out-of-type
    values raise ``ValueError``.
    """
    computed = cast(Mapping[str, Any], content.get("computed") or {})
    block = cast(Mapping[str, Any], computed.get("geometry_metrics") or {})

    def _pos_float(key: str) -> float | None:
        value = block.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"computed.geometry_metrics.{key} must be a number; got {type(value).__name__}"
            )
        f = float(value)
        if not math.isfinite(f):
            raise ValueError(f"computed.geometry_metrics.{key} must be finite; got {value!r}")
        if f <= 0:
            raise ValueError(f"computed.geometry_metrics.{key}={f} must be positive")
        return f

    def _bool(key: str) -> bool | None:
        value = block.get(key)
        if value is None:
            return None
        if not isinstance(value, bool):
            raise ValueError(
                f"computed.geometry_metrics.{key} must be a boolean; got {type(value).__name__}"
            )
        return value

    return FeatureMetrics(
        wall_thickness_mm=_pos_float("wall_thickness_mm"),
        top_thickness_mm=_pos_float("top_thickness_mm"),
        bottom_thickness_mm=_pos_float("bottom_thickness_mm"),
        snap_root_thickness_mm=_pos_float("snap_root_thickness_mm"),
        decorative_thickness_mm=_pos_float("decorative_thickness_mm"),
        through_hole_min_diameter_mm=_pos_float("through_hole_min_diameter_mm"),
        decorative_stroke_min_width_mm=_pos_float("decorative_stroke_min_width_mm"),
        emboss_min_depth_mm=_pos_float("emboss_min_depth_mm"),
        engrave_min_depth_mm=_pos_float("engrave_min_depth_mm"),
        ligament_min_mm=_pos_float("ligament_min_mm"),
        max_bridge_mm=_pos_float("max_bridge_mm"),
        max_overhang_deg=_pos_float("max_overhang_deg"),
        supports_used=_bool("supports_used"),
        button_local_mounting_land_thickness_mm=_pos_float(
            "button_local_mounting_land_thickness_mm"
        ),
        usb_ligament_mm=_pos_float("usb_ligament_mm"),
    )


def _parse_geometry_binding(content: Mapping[str, Any]) -> GeometryBinding | None:
    """Parse optional compatibility binding; production validation requires it."""
    computed = cast(Mapping[str, Any], content.get("computed") or {})
    raw = computed.get("geometry_binding")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("computed.geometry_binding must be an object")
    raw = cast(Mapping[str, Any], raw)
    required = ("source_sha", "top_stl_sha256", "bottom_stl_sha256")
    if any(key not in raw for key in required):
        missing = next(key for key in required if key not in raw)
        raise ValueError(f"computed.geometry_binding is missing {missing!r}")
    return GeometryBinding(
        source_sha=str(raw["source_sha"]),
        top_stl_sha256=str(raw["top_stl_sha256"]),
        bottom_stl_sha256=str(raw["bottom_stl_sha256"]),
        printer_id=str(raw["printer_id"]),
        profile_sha256=str(raw.get("profile_sha256", "")),
        gcode_sha256=str(raw.get("gcode_sha256", "")),
    )


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------
def write_test_frozen_geometry_record(
    *,
    path: Path | str,
    id_slug: str,
    signed_by: str,
    signed_utc: str,
    geometry_metrics: Mapping[str, Any],
    geometry_binding: Mapping[str, Any] | None = None,
) -> Path:
    """Write a synthetic FROZEN provenance record with gate ``geometry``.

    Tests pass a ``geometry_metrics`` mapping (e.g. partial / fully
    populated) and the helper writes a JSON file that
    :meth:`GeometryEvidence.load` accepts. The signer defaults to
    :data:`FrozenEvidenceRef.TEST_SIGNER` when the caller omits it.
    """
    from .evidence import (
        PROVENANCE_SCHEMA_VERSION,
        write_test_frozen_record,
    )

    path = Path(path)
    path = write_test_frozen_record(
        path=path,
        id_slug=id_slug,
        gate_name="geometry",
        signed_by=signed_by,
        signed_utc=signed_utc,
        schema_version=PROVENANCE_SCHEMA_VERSION,
    )
    # Inject the geometry_metrics block via a re-read + write.
    import json as _json

    record = _json.loads(path.read_text())
    record["computed"]["geometry_metrics"] = dict(geometry_metrics)
    if geometry_binding is not None:
        record["computed"]["geometry_binding"] = dict(geometry_binding)
    # Default test records also bind the "coupon" gate so other
    # slices can stack synthetic records in the same file. VS-04
    # does not use it; tests for VS-04 leave it unbound (the
    # helper above only binds ``geometry``).
    path.write_text(_json.dumps(record, indent=2, sort_keys=True))
    return path


__all__ = [
    "FeatureMetrics",
    "GeometryBinding",
    "GeometryEvidence",
    "write_test_frozen_geometry_record",
]
