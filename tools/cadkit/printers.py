"""Printer qualification records (VS-06).

The workshop has **three** active software printer templates:

* :data:`ENDER_3_0_4` — Creality Ender-3, 0.4 mm nozzle, 0.28 mm
  layer height, 220 × 220 × 250 mm envelope, ≤ 4 h per complete
  controller job, supports disabled.
* :data:`ENDER_3_PRO_0_4` — Creality Ender-3 Pro, 0.4 mm nozzle, 0.28 mm
  layer height, 220 × 220 × 250 mm envelope, ≤ 4 h per complete
  controller job, supports disabled.
* :data:`ENDER_3_S1_PRO_0_4` — Creality Ender-3 S1 Pro, 0.4 mm nozzle,
  0.28 mm layer height, 220 × 220 × 270 mm envelope, ≤ 4 h per complete
  controller job, supports disabled.

The retained :data:`PRUSA_XL_0_2` profile is compatibility-only and is not in
the active production registry.

All templates ship with ``qualified=False``. Production export and
the slicer wrapper require a :class:`PrinterQualification` record that
satisfies procurement plan §5 row "Required qualification artifacts"
(procurement plan §5 list, items 1–6) **and** the printer-specific cube
gate (20 mm cube → 19.8–20.2 mm on X/Y/Z).

The cross-slice blockers are:

* :data:`BLK-PRINTER-QUALIFICATION` — physical gate that signs the
  qualification record. Lifted once per printer.
* :data:`BLK-PRINTER-NOZZLE` — Ender-3 Pro 0.4 mm nozzle physical
  measurement (procurement plan §7 risk 6). Lifts only the Ender
  template.
* :data:`BLK-GALAXY-BLACK-XL` — Prusament Galaxy Black passes the XL
  0.2 mm extrusion/clog test (procurement plan §7 risk 7). Lifts the
  XL template only for Galaxy Black; Vanilla White is approved
  without this gate.

API design notes
----------------

* Every record is a ``@dataclass(frozen=True)``. There is no process-
  global mutable qualification state. The orchestrator / agent loop
  passes the qualified record into the slicer / export functions.
* :class:`PrinterProfile` is the **template** (the unmeasured, default
  shape). :class:`QualifiedPrinter` is the template + a
  :class:`PrinterQualification` that satisfies every gate. The
  helper :func:`qualify` enforces the gate; production callers
  receive ``QualifiedPrinter`` only when every required field is
  present and within tolerance.
* :class:`SliceEvidence` is an immutable record of what the slicer
  actually emitted. It carries the printed layer height, the
  observed support-material setting, the parsed print time, and the
  success flag. Production 3MF/G-code export requires a
  ``SliceEvidence(success=True, supports_used=False)`` whose profile
  matches the qualified printer's profile name — anything else
  fails closed.

Hard-coded numerical constants here are **software defaults**. They
are not physical evidence; the procurement-plan receiving checklist
must sign the corresponding receiving row before a production
geometry may rely on a qualified printer.
"""

from __future__ import annotations

import hashlib
import math
import re

# ``agent.profiles`` lives under ``tools/agent/``; the agent.profiles
# module is on sys.path via the same convention used by the existing
# parametric module (when ``tools/`` is on sys.path, ``agent.*`` and
# ``cadkit.*`` are both importable).
import sys as _sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in _sys.path:
    _sys.path.insert(0, str(_TOOLS))
from agent.profiles import (  # type: ignore[import-not-found]  # noqa: E402
    ENDER_3_0_4_TEMPLATE,
    ENDER_3_PRO_0_4_TEMPLATE,
    ENDER_3_S1_PRO_0_4_TEMPLATE,
    PRUSA_XL_0_2_TEMPLATE,
)

# The runtime import path is identical to the type-checking path; we
# inline the import so the forward-reference strings resolve at runtime
# when ``from __future__ import annotations`` is active.
from .evidence import FrozenEvidenceRef as _FrozenEvidenceRef  # noqa: E402


# ---------------------------------------------------------------------------
# Physical printer asset registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PrinterAsset:
    """Immutable workshop asset label bound to one model profile."""

    printer_id: str
    profile_name: str


# Stable labels are operator-planned identities, not qualification claims.
PRINTER_ASSETS: tuple[PrinterAsset, ...] = (
    PrinterAsset("ender3-pro-01", "ender3_pro_0.4"),
    PrinterAsset("ender3-pro-02", "ender3_pro_0.4"),
    PrinterAsset("ender3-pro-03", "ender3_pro_0.4"),
    PrinterAsset("ender3-01", "ender3_0.4"),
    PrinterAsset("ender3-s1-pro-01", "ender3_s1_pro_0.4"),
)


def printer_asset_for(printer_id: str, profile_name: str | None = None) -> PrinterAsset:
    """Resolve one planned physical printer and optionally verify its profile."""
    asset = next((item for item in PRINTER_ASSETS if item.printer_id == printer_id), None)
    if asset is None:
        raise ValueError(
            f"Unknown printer asset ID {printer_id!r}; known assets are "
            f"{tuple(item.printer_id for item in PRINTER_ASSETS)}"
        )
    if profile_name is not None and asset.profile_name != profile_name:
        raise ValueError(
            f"printer asset {printer_id!r} is bound to profile {asset.profile_name!r}, "
            f"not {profile_name!r}"
        )
    return asset


# ---------------------------------------------------------------------------
# Cross-slice blockers (named in IMPLEMENTATION-STATUS.md)
# ---------------------------------------------------------------------------
#: Physical gate that lifts a printer template to ``qualified=True``.
BLK_PRINTER_QUALIFICATION: str = "BLK-PRINTER-QUALIFICATION"
#: Physical gate that the intended Ender nozzle is verified.
BLK_PRINTER_NOZZLE: str = "BLK-PRINTER-NOZZLE"
#: Physical gate that Galaxy Black passes the XL 0.2 mm extrusion/clog
#: test (procurement plan §7 risk 7).
BLK_GALAXY_BLACK_XL: str = "BLK-GALAXY-BLACK-XL"


# ---------------------------------------------------------------------------
# Cube gate — procurement plan §5 row "Required qualification artifacts" item 1
# ---------------------------------------------------------------------------
CUBE_NOMINAL_MM: float = 20.0
CUBE_MIN_MM: float = 19.8
CUBE_MAX_MM: float = 20.2

# ---------------------------------------------------------------------------
# Strict filament registry — procurement plan §2.12 / §2.13.
# ---------------------------------------------------------------------------
#: Approved filament identifier for Prusament Galaxy Black (ASIN
#: B0BJXPNG4M). Used for the Ender-3 Pro 0.4 mm profile and (after
#: the clog-test gate) the Prusa XL 0.2 mm profile.
FILAMENT_ID_GALAXY_BLACK: str = "Prusament_Galaxy_Black_ASIN_B0BJXPNG4M"
#: Approved filament identifier for Prusament Vanilla White (ASIN
#: B0CJ22RCXS). Default XL material until the clog-test gate
#: lifts; always approved for the Ender-3 Pro 0.4 mm profile.
FILAMENT_ID_VANILLA_WHITE: str = "Prusament_Vanilla_White_ASIN_B0CJ22RCXS"
#: Tuple of every workshop-approved filament identifier. The
#: validator rejects any filament outside this set with a hard
#: ``filament_unapproved`` error. The set is **closed**: adding a
#: filament is a procurement-plan update, not a code-side change.
APPROVED_FILAMENT_IDS: frozenset[str] = frozenset(
    {
        FILAMENT_ID_GALAXY_BLACK,
        FILAMENT_ID_VANILLA_WHITE,
    }
)


# ---------------------------------------------------------------------------
# Printer profile templates (UNQUALIFIED defaults)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PrinterProfile:
    """A software printer profile template.

    The template carries the *default* values for layer height, build
    volume, maximum print duration, and the path to the slicer
    ``.ini`` file. ``qualified`` is **always** ``False`` for a
    template; production callers must wrap a template in a
    :class:`QualifiedPrinter` via :func:`qualify` before invoking the
    slicer.

    The template's ``supports_off`` field is structurally ``True``;
    templates that permit supports are not allowed by procurement plan
    §5 row "Supports".
    """

    name: str
    nozzle_mm: float
    build_volume_mm: tuple[float, float, float]
    layer_height_mm: float
    supports_off: bool
    max_print_minutes: float
    profile_path: str
    filament: str = "PLA"
    deprecated: bool = False
    qualified: bool = False
    note: str = ""
    printer_model: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("PrinterProfile.name must be non-empty.")
        if self.nozzle_mm <= 0:
            raise ValueError(f"PrinterProfile.nozzle_mm={self.nozzle_mm} must be > 0.")
        if any(v <= 0 for v in self.build_volume_mm):
            raise ValueError(
                f"PrinterProfile.build_volume_mm={self.build_volume_mm} must be positive."
            )
        if self.layer_height_mm <= 0:
            raise ValueError(f"PrinterProfile.layer_height_mm={self.layer_height_mm} must be > 0.")
        if not self.supports_off:
            raise ValueError(
                "PrinterProfile.supports_off must be True; supports cannot "
                "rescue a rejected design (procurement plan §5)."
            )
        if self.max_print_minutes <= 0:
            raise ValueError(
                f"PrinterProfile.max_print_minutes={self.max_print_minutes} must be > 0."
            )
        if not self.profile_path:
            raise ValueError("PrinterProfile.profile_path must be a non-empty path.")
        if self.deprecated and self.note == "":
            raise ValueError("Deprecated PrinterProfile must include a 'note' explaining why.")


def _ender_profile(
    *,
    name: str,
    printer_model: str,
    profile_path: str,
    build_volume_z_mm: float,
    note: str,
) -> PrinterProfile:
    """Build one unqualified Ender software template."""
    return PrinterProfile(
        name=name,
        nozzle_mm=0.4,
        build_volume_mm=(220.0, 220.0, build_volume_z_mm),
        layer_height_mm=0.28,
        supports_off=True,
        max_print_minutes=4 * 60,
        profile_path=profile_path,
        filament="PLA",
        note=note,
        printer_model=printer_model,
    )


#: Ender-3 0.4 mm template. **Template only — unqualified.**
ENDER_3_0_4: PrinterProfile = _ender_profile(
    name="ender3_0.4",
    printer_model="Ender3",
    profile_path=ENDER_3_0_4_TEMPLATE,
    build_volume_z_mm=250.0,
    note=(
        "Ender-3 0.4 mm software default. It is not physical qualification; "
        "both halves must slice support-free before production."
    ),
)

#: Reference 0.4 mm printer. **Template only — unqualified.**
ENDER_3_PRO_0_4: PrinterProfile = _ender_profile(
    name="ender3_pro_0.4",
    printer_model="Ender3Pro",
    profile_path=ENDER_3_PRO_0_4_TEMPLATE,
    build_volume_z_mm=250.0,
    note=(
        "Ender-3 Pro 0.4 mm software default. It is not physical qualification; "
        "both halves must slice support-free before production."
    ),
)

#: Ender-3 S1 Pro 0.4 mm template. **Template only — unqualified.**
ENDER_3_S1_PRO_0_4: PrinterProfile = _ender_profile(
    name="ender3_s1_pro_0.4",
    printer_model="Ender3S1Pro",
    profile_path=ENDER_3_S1_PRO_0_4_TEMPLATE,
    build_volume_z_mm=270.0,
    note=(
        "Ender-3 S1 Pro 0.4 mm software default. It is not physical "
        "qualification; both halves must slice support-free before production."
    ),
)

#: Overnight overflow (procurement plan §5 / §6). **Template only —
#: unqualified.** Galaxy Black requires the clog-test gate
#: :data:`BLK_GALAXY_BLACK_XL` before this profile is allowed for
#: Galaxy Black; Vanilla White is approved without that gate.
PRUSA_XL_0_2: PrinterProfile = PrinterProfile(
    name="prusa_xl_0.2",
    nozzle_mm=0.2,
    build_volume_mm=(360.0, 360.0, 360.0),
    layer_height_mm=0.15,
    supports_off=True,
    max_print_minutes=12 * 60,
    profile_path=PRUSA_XL_0_2_TEMPLATE,
    filament="PLA",
    deprecated=True,
    printer_model="PrusaXL",
    note=(
        "Overnight overflow on Prusa XL with 0.2 mm nozzle. Both halves must "
        "slice support-free; job must finish by 08:00 Saturday (procurement "
        "plan §6)."
    ),
)

#: Convenience: the canonical workshop profile set.
WORKSHOP_PRINTER_PROFILES: tuple[PrinterProfile, ...] = (
    ENDER_3_0_4,
    ENDER_3_PRO_0_4,
    ENDER_3_S1_PRO_0_4,
)

#: Historical profiles retained for compatibility but refused for production.
COMPATIBILITY_PRINTER_PROFILES: tuple[PrinterProfile, ...] = (PRUSA_XL_0_2,)


def get_profile(name: str) -> PrinterProfile:
    """Return the canonical workshop profile for ``name``.

    Raises:
        ValueError: if ``name`` is not one of the workshop templates.
        Production callers must use :func:`get_qualified_profile`
        once the qualification record exists.
    """
    for profile in WORKSHOP_PRINTER_PROFILES:
        if profile.name == name:
            return profile
    raise ValueError(
        f"Unknown printer profile {name!r}; known templates are "
        f"{tuple(p.name for p in WORKSHOP_PRINTER_PROFILES)}. "
        "Unknown / replacement printers cannot run production until "
        f"{BLK_PRINTER_QUALIFICATION} signs off."
    )


# ---------------------------------------------------------------------------
# Qualification record — procurement plan §5 row "Required qualification
# artifacts"
# ---------------------------------------------------------------------------
def _validate_qualification_profile(profile_name: str) -> None:
    if profile_name not in {p.name for p in WORKSHOP_PRINTER_PROFILES}:
        raise ValueError(
            f"PrinterQualification.profile_name={profile_name!r} is not a known "
            "workshop template; refusing to qualify an unknown / replacement printer."
        )


def _validate_cube_dimensions(*dimensions: float) -> None:
    for axis_name, value in zip(("cube_x_mm", "cube_y_mm", "cube_z_mm"), dimensions, strict=True):
        if not CUBE_MIN_MM <= value <= CUBE_MAX_MM:
            raise ValueError(
                f"{axis_name}={value} is outside the cube gate "
                f"[{CUBE_MIN_MM}, {CUBE_MAX_MM}] mm; receiving must record an actual measured dimension."
            )


def _validate_qualification_material(nozzle_diameter_mm: float, filament: str) -> None:
    if nozzle_diameter_mm <= 0:
        raise ValueError("nozzle_diameter_mm must be positive.")
    if filament not in APPROVED_FILAMENT_IDS:
        raise ValueError(
            f"filament={filament!r} is not in the approved workshop set "
            f"{sorted(APPROVED_FILAMENT_IDS)}; the strict ID is required "
            "(procurement plan §2.12 / §2.13)."
        )


def _validate_coupon_flags(qualification: PrinterQualification) -> None:
    for field_name in (
        "material_extrusion_passed",
        "pbs33b_directional_coupon_passed",
        "guuzi_action_coupon_passed",
        "snap_coupon_passed",
        "electronics_backbone_coupon_passed",
        "support_free_slice_passed",
    ):
        if not getattr(qualification, field_name):
            raise ValueError(
                f"{field_name} must be True; a False flag is a missing coupon and blocks production."
            )
    if not 0.0 <= qualification.snap_clearance_mm <= 2.0:
        raise ValueError(
            f"snap_clearance_mm={qualification.snap_clearance_mm} is outside the plausible coupon range (0.0–2.0 mm)."
        )
    if qualification.cube_repeat_count < 1:
        raise ValueError("cube_repeat_count must be ≥ 1.")


def _validate_qualification_signoff(qualification: PrinterQualification) -> None:
    if (
        not qualification.signed_by
        or not qualification.signed_utc
        or not qualification.receiving_row_id
    ):
        raise ValueError("signed_by / signed_utc / receiving_row_id are required.")


@dataclass(frozen=True)
class PrinterQualification:
    """Receiving-signed record that a printer template is qualified.

    Every field is required; missing / out-of-range values fail the
    cube/material/coupon/time gates and the slicer / export paths
    refuse to start. Procurement plan §5 row "Required qualification
    artifacts" lists 1–6; this dataclass covers 1, 3, 4, 5, 6 (the
    material/extrusion gate is split into ``material_extrusion_passed``
    plus the explicit ``filament`` string).

    The 20 mm cube gate requires X/Y/Z measured dimensions in
    [19.8, 20.2] mm. The coupon gate records separate PBS-33B directional
    and GUUZI action coupons, the future receiving-bound electronics-backbone
    coupon, and snap coupons. The chosen snap
    clearance is part of the qualification (procurement plan §5 row
    "Snap clearance").

    ``galaxy_black_clog_passed`` is the explicit evidence-bound
    datum for the XL 0.2 mm / Galaxy Black combination. The default
    is ``False``; a real receiving checklist sets it to ``True``
    only after the BLK-GALAXY-BLACK-XL gate signs. Vanilla White
    does not need this gate.

    Production-bound qualification records must come from a
    real provenance file (gate ``"printer"``) via
    :meth:`from_frozen_evidence`. Direct dataclass construction
    via ``PrinterQualification(...)`` is allowed for tests but
    leaves ``bound_evidence=None``; the validator / export pipeline
    refuses records that are not bound to a frozen evidence ref.
    """

    profile_name: str
    printer_id: str
    cube_x_mm: float
    cube_y_mm: float
    cube_z_mm: float
    material_extrusion_passed: bool
    pbs33b_directional_coupon_passed: bool
    guuzi_action_coupon_passed: bool
    snap_coupon_passed: bool
    electronics_backbone_coupon_passed: bool
    support_free_slice_passed: bool
    nozzle_diameter_mm: float
    filament: str
    snap_clearance_mm: float
    signed_by: str
    signed_utc: str
    receiving_row_id: str
    slicer_time_minutes: float = 0.0  # recorded slicer estimate at sign-off
    cube_repeat_count: int = 1  # ≥1 prints of the cube, recorded for the audit
    galaxy_black_clog_passed: bool = False  # BLK-GALAXY-BLACK-XL gate
    bound_evidence: _FrozenEvidenceRef | None = None
    profile_sha256: str = ""

    def __post_init__(self) -> None:
        _validate_qualification_profile(self.profile_name)
        printer_asset_for(self.printer_id, self.profile_name)
        _validate_cube_dimensions(self.cube_x_mm, self.cube_y_mm, self.cube_z_mm)
        _validate_qualification_material(self.nozzle_diameter_mm, self.filament)
        _validate_coupon_flags(self)
        _validate_qualification_signoff(self)
        if self.profile_sha256 and not _is_digest(self.profile_sha256):
            raise ValueError("profile_sha256 must be a lowercase 64-hex digest when supplied.")

    @classmethod
    def from_frozen_evidence(
        cls, evidence: _FrozenEvidenceRef | Path | str
    ) -> PrinterQualification:
        """Bind a :class:`PrinterQualification` to a real provenance file.

        The provenance JSON must be FROZEN with gate ``"printer"``
        passed=True; the ``computed.printer_qualification`` block
        carries every dataclass field. Cross-checks: the profile
        name / nozzle / filament in the JSON match the dataclass
        fields exactly. A fabrication / mismatch raises
        :class:`ProvenanceShapeInvalid`.
        """
        from .evidence import FrozenEvidenceRef  # local import (cycle)

        ref = (
            evidence
            if isinstance(evidence, FrozenEvidenceRef)
            else FrozenEvidenceRef.load(evidence, gate_name="printer")
        )
        computed = cast(Mapping[str, Any], ref.content.get("computed") or {})
        block = cast(Mapping[str, Any] | None, computed.get("printer_qualification"))
        if block is None:
            from .evidence import ProvenanceShapeInvalid

            raise ProvenanceShapeInvalid(
                f"provenance file {ref.path}: missing required computed.printer_qualification block"
            )
        profile_name = block.get("profile_name")
        if profile_name not in {p.name for p in WORKSHOP_PRINTER_PROFILES}:
            raise ValueError(f"unknown printer profile {profile_name!r}")
        # Required fields.
        required = (
            "profile_name",
            "printer_id",
            "cube_x_mm",
            "cube_y_mm",
            "cube_z_mm",
            "nozzle_diameter_mm",
            "filament",
            "snap_clearance_mm",
            "receiving_row_id",
            "profile_sha256",
        )
        for key in required:
            if key not in block:
                from .evidence import ProvenanceShapeInvalid

                raise ProvenanceShapeInvalid(
                    f"provenance file {ref.path}: computed.printer_qualification "
                    f"is missing required field {key!r}"
                )
        # All coupon flags must be True (gate already enforces the
        # gate-level passed=True — this enforces per-coupon).
        for flag in (
            "material_extrusion_passed",
            "pbs33b_directional_coupon_passed",
            "guuzi_action_coupon_passed",
            "snap_coupon_passed",
            "electronics_backbone_coupon_passed",
            "support_free_slice_passed",
        ):
            if flag not in block:
                from .evidence import ProvenanceShapeInvalid

                raise ProvenanceShapeInvalid(
                    f"provenance file {ref.path}: coupon flag {flag!r} is missing"
                )
            if type(block[flag]) is not bool or block[flag] is not True:
                from .evidence import ProvenanceShapeInvalid

                raise ProvenanceShapeInvalid(
                    f"provenance file {ref.path}: coupon flag {flag!r} is not boolean True"
                )
        # The sign-off (signed_by / signed_utc) is recorded on the
        # gate itself (the receiving checklist signs the row, not
        # the dataclass sub-block). The FrozenEvidenceRef revalidates
        # the gate on every re-read.
        return cls(
            profile_name=block["profile_name"],
            printer_id=block["printer_id"],
            cube_x_mm=float(block["cube_x_mm"]),
            cube_y_mm=float(block["cube_y_mm"]),
            cube_z_mm=float(block["cube_z_mm"]),
            material_extrusion_passed=bool(block["material_extrusion_passed"]),
            pbs33b_directional_coupon_passed=bool(block["pbs33b_directional_coupon_passed"]),
            guuzi_action_coupon_passed=bool(block["guuzi_action_coupon_passed"]),
            snap_coupon_passed=bool(block["snap_coupon_passed"]),
            electronics_backbone_coupon_passed=bool(block["electronics_backbone_coupon_passed"]),
            support_free_slice_passed=bool(block["support_free_slice_passed"]),
            nozzle_diameter_mm=float(block["nozzle_diameter_mm"]),
            filament=block["filament"],
            snap_clearance_mm=float(block["snap_clearance_mm"]),
            signed_by=ref.signed_by,
            signed_utc=ref.signed_utc,
            receiving_row_id=block["receiving_row_id"],
            slicer_time_minutes=float(block.get("slicer_time_minutes", 0.0)),
            cube_repeat_count=int(block.get("cube_repeat_count", 1)),
            galaxy_black_clog_passed=bool(block.get("galaxy_black_clog_passed", False)),
            bound_evidence=ref,
            profile_sha256=block["profile_sha256"],
        )

    def revalidate(self) -> None:
        """Re-read the bound provenance file (delegated)."""
        if self.bound_evidence is None:
            from .evidence import ProvenanceError

            raise ProvenanceError(
                "PrinterQualification.bound_evidence is None; this record "
                "was not loaded from a real provenance file and cannot "
                "be revalidated. Use from_frozen_evidence(...) for "
                "production-bound records."
            )
        self.bound_evidence.revalidate()


# ---------------------------------------------------------------------------
# Qualified printer wrapper
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class QualifiedPrinter:
    """A :class:`PrinterProfile` template bound to a signed
    :class:`PrinterQualification`.

    This is the type the slicer / export paths accept. There is no
    way to construct it without a qualification record, so there is no
    way to bypass the gate by passing only a template.

    Production callers consume ``.profile`` and ``.qualification``
    separately when they need to thread them through individual
    functions (e.g. the slicer wrapper takes the template's profile
    path; the constraint validator takes the qualification's snap
    clearance).
    """

    profile: PrinterProfile
    qualification: PrinterQualification

    def __post_init__(self) -> None:
        if self.profile.name != self.qualification.profile_name:
            raise ValueError(
                f"QualifiedPrinter: profile.name={self.profile.name!r} does "
                f"not match qualification.profile_name="
                f"{self.qualification.profile_name!r}."
            )
        if abs(self.profile.nozzle_mm - self.qualification.nozzle_diameter_mm) > 1e-9:
            raise ValueError(
                f"QualifiedPrinter: profile.nozzle_mm={self.profile.nozzle_mm} "
                f"does not match qualification.nozzle_diameter_mm="
                f"{self.qualification.nozzle_diameter_mm}."
            )


def qualify(profile: PrinterProfile, qualification: PrinterQualification) -> QualifiedPrinter:
    """Wrap a profile template + signed qualification record.

    The :class:`PrinterQualification` constructor already enforces
    every numeric gate; this helper just binds the two. Production
    callers invoke this exactly once per printer and cache the result
    in the orchestrator's slice evidence path.
    """
    return QualifiedPrinter(profile=profile, qualification=qualification)


# ---------------------------------------------------------------------------
# Profile-specific rules — VS-04 fix (no universal 0.30/4h gate).
# ---------------------------------------------------------------------------
def layer_height_mm_for(profile_name: str) -> float:
    """The required layer height for ``profile_name`` (mm).

    Procurement plan §5 row "Print duration" ties the layer height to
    the nozzle: Ender-3 Pro 0.4 mm → 0.28 mm; Prusa XL 0.2 mm →
    0.15 mm. A universal ``0.30`` is **not** the procurement-plan
    rule. Unknown / replacement printers raise ``ValueError`` (the
    caller cannot bypass the registry; production callers use
    :data:`WORKSHOP_PRINTER_PROFILES`).
    """
    for profile in WORKSHOP_PRINTER_PROFILES:
        if profile.name == profile_name:
            return profile.layer_height_mm
    raise ValueError(
        f"Unknown printer profile {profile_name!r}; known templates are "
        f"{tuple(p.name for p in WORKSHOP_PRINTER_PROFILES)}. "
        f"Unknown / replacement printers cannot run production until "
        f"{BLK_PRINTER_QUALIFICATION} signs off."
    )


def max_print_minutes_for(profile_name: str) -> float:
    """The maximum print duration for ``profile_name`` (minutes).

    Procurement plan §5 row "Print duration": Ender-3 Pro 0.4 mm
    → 4 h = 240 min; Prusa XL 0.2 mm → 12 h = 720 min. The
    universal ``4 * 60`` rule is **not** the procurement-plan
    rule.
    """
    for profile in WORKSHOP_PRINTER_PROFILES:
        if profile.name == profile_name:
            return profile.max_print_minutes
    raise ValueError(
        f"Unknown printer profile {profile_name!r}; known templates are "
        f"{tuple(p.name for p in WORKSHOP_PRINTER_PROFILES)}."
    )


def filament_approved_for(profile_name: str, filament: str, galaxy_black_clog_passed: bool) -> bool:
    """True iff ``filament`` may be sliced on ``profile_name``.

    * Galaxy Black requires the clog-test gate on the XL 0.2 mm
      nozzle. Vanilla White is approved without that gate.
    * Ender-3 Pro 0.4 mm accepts both without the clog-test gate.
    """
    if filament not in APPROVED_FILAMENT_IDS:
        return False
    if (
        profile_name not in {profile.name for profile in WORKSHOP_PRINTER_PROFILES}
        and profile_name != PRUSA_XL_0_2.name
    ):
        return False
    return not (
        profile_name == "prusa_xl_0.2"
        and filament == FILAMENT_ID_GALAXY_BLACK
        and not galaxy_black_clog_passed
    )


def qualification_matches_evidence(
    qualification: PrinterQualification,
    evidence: _FrozenEvidenceRef | Path | str,
) -> bool:
    """True iff the qualification is bound to ``evidence`` and current.

    A qualification that is **not** bound to a frozen evidence
    ref cannot qualify production. The wrapper revalidates the
    evidence (file SHA-256 unchanged since load) and cross-checks
    every documented field against the parsed JSON content.

    Returns ``True`` only when the qualification is bound AND
    every field matches AND the evidence file is unchanged.

    Mismatches raise :class:`EvidenceTamper` (or a sibling
    :class:`ProvenanceError`). Production callers invoke this at
    the gate and react to the exception via
    :class:`cadkit.constraints.ConstraintViolation` (never ``ValueError``).
    """
    from .evidence import EvidenceTamper, FrozenEvidenceRef  # local import

    if isinstance(evidence, FrozenEvidenceRef):
        ref = evidence
    else:
        ref = FrozenEvidenceRef.load(evidence, gate_name="printer")
    if qualification.bound_evidence is None:
        return False
    # Tamper check (raises EvidenceTamper on byte change).
    ref.revalidate()
    if qualification.bound_evidence.sha256 != ref.sha256:
        raise EvidenceTamper(
            f"PrinterQualification.bound_evidence sha256 "
            f"{qualification.bound_evidence.sha256} does not match the "
            f"currently-supplied evidence sha256 {ref.sha256}"
        )
    # Cross-check every documented field.
    computed = cast(Mapping[str, Any], ref.content.get("computed") or {})
    block = cast(Mapping[str, Any], computed.get("printer_qualification") or {})
    checks = {
        "profile_name": qualification.profile_name,
        "printer_id": qualification.printer_id,
        "cube_x_mm": qualification.cube_x_mm,
        "cube_y_mm": qualification.cube_y_mm,
        "cube_z_mm": qualification.cube_z_mm,
        "material_extrusion_passed": qualification.material_extrusion_passed,
        "pbs33b_directional_coupon_passed": qualification.pbs33b_directional_coupon_passed,
        "guuzi_action_coupon_passed": qualification.guuzi_action_coupon_passed,
        "snap_coupon_passed": qualification.snap_coupon_passed,
        "electronics_backbone_coupon_passed": qualification.electronics_backbone_coupon_passed,
        "support_free_slice_passed": qualification.support_free_slice_passed,
        "nozzle_diameter_mm": qualification.nozzle_diameter_mm,
        "filament": qualification.filament,
        "snap_clearance_mm": qualification.snap_clearance_mm,
        "receiving_row_id": qualification.receiving_row_id,
        "galaxy_black_clog_passed": qualification.galaxy_black_clog_passed,
        "profile_sha256": qualification.profile_sha256,
    }
    for field_name, expected in checks.items():
        actual = block.get(field_name)
        if isinstance(expected, float):
            if actual is None or abs(float(actual) - expected) > 1e-9:
                raise EvidenceTamper(
                    f"qualification.{field_name}={expected} does not match "
                    f"evidence computed.printer_qualification.{field_name}="
                    f"{actual!r}"
                )
        elif actual != expected:
            raise EvidenceTamper(
                f"qualification.{field_name}={expected!r} does not match "
                f"evidence computed.printer_qualification.{field_name}="
                f"{actual!r}"
            )
    return True


# ---------------------------------------------------------------------------
# Slice evidence — immutable record of what the slicer actually emitted
# ---------------------------------------------------------------------------
def _load_slice_evidence_ref(
    evidence: _FrozenEvidenceRef | Path | str,
) -> _FrozenEvidenceRef:
    """Reload slice provenance and reject caller-constructed references."""
    from .evidence import EvidenceTamper

    if isinstance(evidence, _FrozenEvidenceRef):
        supplied = evidence
        if supplied.is_snapshot:
            return supplied
        path = evidence.path
    else:
        supplied = None
        path = evidence
    validated = _FrozenEvidenceRef.load(path, gate_name="slice")
    if supplied is not None and (supplied != validated or supplied.content != validated.content):
        raise EvidenceTamper(
            f"provenance reference {supplied.path} does not match the validated slice record"
        )
    return validated


@dataclass(frozen=True)
class SliceEvidence:
    """An immutable record of one slicer invocation.

    The slicer wrapper constructs this from the process exit code,
    stdout / stderr, the parsed ``[print] support_material = 0`` line,
    and the parsed ``estimated printing time``. Production export
    refuses 3MF / G-code unless it sees ``SliceEvidence(success=True,
    supports_used=False)`` whose ``profile_name`` matches the chosen
    qualified printer's profile name.

    A *failed* slice (success=False) is itself a record — the
    validator surfaces it as a ``severity=error`` violation with rule
    ``slice_failed`` so the agent loop can iterate.
    """

    profile_name: str
    profile_path: str
    gcode_path: str
    success: bool
    supports_used: bool
    layer_height_mm: float
    print_time_seconds: int
    nozzle_diameter_mm: float
    filament: str
    printer_id: str
    returncode: int = 0
    error_message: str = ""
    timestamp_utc: str = ""
    source_sha: str = ""
    profile_sha256: str = ""
    gcode_sha256: str = ""
    top_stl_sha256: str = ""
    bottom_stl_sha256: str = ""
    raw_output_excerpt: str = ""
    bound_evidence: _FrozenEvidenceRef | None = None

    def __post_init__(self) -> None:
        if not self.profile_name:
            raise ValueError("SliceEvidence.profile_name is required.")
        if not self.profile_path:
            raise ValueError("SliceEvidence.profile_path is required.")
        if not self.gcode_path:
            raise ValueError("SliceEvidence.gcode_path is required.")
        if self.layer_height_mm <= 0:
            raise ValueError("SliceEvidence.layer_height_mm must be > 0.")
        if self.nozzle_diameter_mm <= 0:
            raise ValueError("SliceEvidence.nozzle_diameter_mm must be > 0.")
        if not self.filament:
            raise ValueError("SliceEvidence.filament is required.")
        if not self.printer_id:
            raise ValueError("SliceEvidence.printer_id is required.")
        if self.print_time_seconds < 0:
            raise ValueError("SliceEvidence.print_time_seconds must be ≥ 0.")
        if self.success and self.supports_used:
            raise ValueError(
                "SliceEvidence: success=True but supports_used=True; both "
                "halves must slice support-free (procurement plan §5)."
            )
        for field_name in (
            "profile_sha256",
            "gcode_sha256",
            "top_stl_sha256",
            "bottom_stl_sha256",
        ):
            value = getattr(self, field_name)
            if value and not _is_digest(value):
                raise ValueError(f"{field_name} must be a lowercase 64-hex digest when supplied.")

    @classmethod
    def from_frozen_evidence(cls, evidence: _FrozenEvidenceRef | Path | str) -> SliceEvidence:
        """Load and bind a slicer invocation from a signed frozen record."""
        from .evidence import ProvenanceShapeInvalid

        ref = _load_slice_evidence_ref(evidence)
        computed = ref.content.get("computed")
        computed_map = cast(Mapping[str, Any], computed) if isinstance(computed, Mapping) else None
        block = computed_map.get("slice_evidence") if computed_map is not None else None
        block = cast(Mapping[str, Any] | None, block)
        if not isinstance(block, Mapping):
            raise ProvenanceShapeInvalid(
                f"provenance file {ref.path}: missing required computed.slice_evidence block"
            )
        values = _slice_block_values(block, ref.path)
        result = cls(bound_evidence=ref, **values)
        _cross_check_slice_block(block, result, ref.path)
        return result

    def revalidate(self) -> None:
        """Revalidate the frozen slice record and every stored production field."""
        from .evidence import ProvenanceError

        if self.bound_evidence is None:
            raise ProvenanceError(
                "SliceEvidence.bound_evidence is None; this candidate was not loaded "
                "from a frozen slice invocation record"
            )
        if not slice_evidence_matches_evidence(self, self.bound_evidence):
            raise ProvenanceError("SliceEvidence does not match its frozen slice record")


# ---------------------------------------------------------------------------
# Consistency helpers
# ---------------------------------------------------------------------------
def slice_matches_qualification(slice_evidence: SliceEvidence, qualified: QualifiedPrinter) -> bool:
    """True iff the slice evidence agrees with the qualified printer.

    Production 3MF/G-code export refuses unless this returns ``True``.
    The check covers: profile name match, filament match, nozzle
    diameter match, layer-height match (within 1 µm), and support
    disabled.
    """
    if slice_evidence.profile_name != qualified.profile.name:
        return False
    if slice_evidence.printer_id != qualified.qualification.printer_id:
        return False
    if slice_evidence.filament != qualified.qualification.filament:
        return False
    if abs(slice_evidence.nozzle_diameter_mm - qualified.profile.nozzle_mm) > 1e-9:
        return False
    if abs(slice_evidence.layer_height_mm - qualified.profile.layer_height_mm) > 1e-6:
        return False
    if slice_evidence.success is not True:
        return False
    return slice_evidence.supports_used is False


_SLICE_PRODUCTION_FIELDS: tuple[str, ...] = (
    "profile_name",
    "printer_id",
    "profile_path",
    "gcode_path",
    "success",
    "supports_used",
    "layer_height_mm",
    "print_time_seconds",
    "nozzle_diameter_mm",
    "filament",
    "returncode",
    "source_sha",
    "profile_sha256",
    "gcode_sha256",
    "top_stl_sha256",
    "bottom_stl_sha256",
)
_SLICE_OPTIONAL_FIELDS: tuple[str, ...] = (
    "error_message",
    "timestamp_utc",
    "raw_output_excerpt",
)


def slice_evidence_matches_evidence(
    slice_evidence: SliceEvidence,
    evidence: _FrozenEvidenceRef | Path | str,
) -> bool:
    """Revalidate a frozen slice record and cross-check all stored fields.

    This helper may compare an unbound slicer candidate to an explicit
    provenance path (the agent's two-phase flow uses that capability). The
    export/upload gates separately require the candidate itself to carry the
    resulting ``bound_evidence`` reference.
    """
    from .evidence import EvidenceTamper

    ref = _load_slice_evidence_ref(evidence)
    if slice_evidence.bound_evidence is not None and (
        slice_evidence.bound_evidence.path != ref.path
        or slice_evidence.bound_evidence.sha256 != ref.sha256
    ):
        raise EvidenceTamper("SliceEvidence.bound_evidence does not match supplied evidence")
    computed = ref.content.get("computed")
    computed_map = cast(Mapping[str, Any], computed) if isinstance(computed, Mapping) else None
    block = computed_map.get("slice_evidence") if computed_map is not None else None
    block = cast(Mapping[str, Any] | None, block)
    if not isinstance(block, Mapping):
        from .evidence import ProvenanceShapeInvalid

        raise ProvenanceShapeInvalid(
            f"provenance file {ref.path}: missing required computed.slice_evidence block"
        )
    values = _slice_block_values(block, ref.path)
    for field_name in _SLICE_PRODUCTION_FIELDS + _SLICE_OPTIONAL_FIELDS:
        if field_name not in block:
            continue
        if getattr(slice_evidence, field_name) != values[field_name]:
            raise EvidenceTamper(
                f"slice evidence {field_name} does not match frozen computed.slice_evidence"
            )
    return True


def _slice_block_values(block: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """Validate a frozen block and return constructor-ready values."""
    _require_slice_fields(block, path)
    _validate_slice_text(block, path)
    _validate_slice_hashes(block, path)
    _validate_slice_scalars(block, path)
    values = {field_name: block[field_name] for field_name in _SLICE_PRODUCTION_FIELDS}
    values["layer_height_mm"] = float(values["layer_height_mm"])
    values["nozzle_diameter_mm"] = float(values["nozzle_diameter_mm"])
    values.update(_slice_optional_values(block, path))
    return values


def _require_slice_fields(block: Mapping[str, Any], path: Path) -> None:
    from .evidence import ProvenanceShapeInvalid

    missing = [field for field in _SLICE_PRODUCTION_FIELDS if field not in block]
    if missing:
        raise ProvenanceShapeInvalid(
            f"provenance file {path}: computed.slice_evidence is missing {missing[0]!r}"
        )


def _validate_slice_text(block: Mapping[str, Any], path: Path) -> None:
    from .evidence import ProvenanceShapeInvalid

    for field_name in (
        "profile_name",
        "printer_id",
        "profile_path",
        "gcode_path",
        "filament",
        "source_sha",
    ):
        value = block[field_name]
        if not isinstance(value, str) or not value:
            raise ProvenanceShapeInvalid(
                f"provenance file {path}: slice {field_name} must be a non-empty string"
            )
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", block["source_sha"]):
        raise ProvenanceShapeInvalid(
            f"provenance file {path}: slice source_sha is not a Git object ID"
        )


def _validate_slice_hashes(block: Mapping[str, Any], path: Path) -> None:
    from .evidence import ProvenanceShapeInvalid

    for field_name in ("profile_sha256", "gcode_sha256", "top_stl_sha256", "bottom_stl_sha256"):
        value = block[field_name]
        if not isinstance(value, str) or not _is_digest(value):
            raise ProvenanceShapeInvalid(
                f"provenance file {path}: slice {field_name} must be a lowercase 64-hex digest"
            )


def _validate_slice_scalars(block: Mapping[str, Any], path: Path) -> None:
    from .evidence import ProvenanceShapeInvalid

    for field_name in ("success", "supports_used"):
        if type(block[field_name]) is not bool:
            raise ProvenanceShapeInvalid(
                f"provenance file {path}: slice {field_name} must be a boolean"
            )
    for field_name in ("layer_height_mm", "nozzle_diameter_mm"):
        value = block[field_name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ProvenanceShapeInvalid(
                f"provenance file {path}: slice {field_name} must be a finite number"
            )
    if (
        isinstance(block["print_time_seconds"], bool)
        or not isinstance(block["print_time_seconds"], int)
        or block["print_time_seconds"] < 0
    ):
        raise ProvenanceShapeInvalid(
            f"provenance file {path}: slice print_time_seconds must be a non-negative integer"
        )
    if isinstance(block["returncode"], bool) or not isinstance(block["returncode"], int):
        raise ProvenanceShapeInvalid(f"provenance file {path}: slice returncode must be an integer")


def _slice_optional_values(block: Mapping[str, Any], path: Path) -> dict[str, str]:
    from .evidence import ProvenanceShapeInvalid

    values: dict[str, str] = {}
    for field_name in _SLICE_OPTIONAL_FIELDS:
        value = block.get(field_name, "")
        if value is None:
            values[field_name] = ""
            continue
        if not isinstance(value, str):
            raise ProvenanceShapeInvalid(
                f"provenance file {path}: slice {field_name} must be a string when supplied"
            )
        values[field_name] = value
    return values


def _cross_check_slice_block(block: Mapping[str, Any], evidence: SliceEvidence, path: Path) -> None:
    from .evidence import EvidenceTamper

    for field_name in _SLICE_PRODUCTION_FIELDS + _SLICE_OPTIONAL_FIELDS:
        if field_name in block and getattr(evidence, field_name) != _slice_value_for_compare(
            field_name, block[field_name]
        ):
            raise EvidenceTamper(f"slice evidence {field_name} does not match frozen record {path}")


def _slice_value_for_compare(field_name: str, value: Any) -> Any:
    if field_name in _SLICE_OPTIONAL_FIELDS and value is None:
        return ""
    if field_name in {"layer_height_mm", "nozzle_diameter_mm"}:
        return float(value)
    return value


def _is_digest(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))


def profile_sha256(profile: PrinterProfile) -> str:
    """Hash the exact regular canonical profile bytes used by the slicer."""
    path = Path(profile.profile_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"printer profile is not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def printer_profile_for(qualified: QualifiedPrinter) -> Mapping[str, object]:
    """Render a :class:`QualifiedPrinter` as a dict for log / metadata."""
    return {
        "profile_name": qualified.profile.name,
        "printer_id": qualified.qualification.printer_id,
        "nozzle_mm": qualified.profile.nozzle_mm,
        "build_volume_mm": qualified.profile.build_volume_mm,
        "layer_height_mm": qualified.profile.layer_height_mm,
        "supports_off": qualified.profile.supports_off,
        "max_print_minutes": qualified.profile.max_print_minutes,
        "profile_path": qualified.profile.profile_path,
        "printer_model": qualified.profile.printer_model,
        "filament": qualified.qualification.filament,
        "snap_clearance_mm": qualified.qualification.snap_clearance_mm,
        "galaxy_black_clog_passed": qualified.qualification.galaxy_black_clog_passed,
        "signed_by": qualified.qualification.signed_by,
        "signed_utc": qualified.qualification.signed_utc,
        "receiving_row_id": qualified.qualification.receiving_row_id,
    }


__all__ = [
    "APPROVED_FILAMENT_IDS",
    "BLK_GALAXY_BLACK_XL",
    "BLK_PRINTER_NOZZLE",
    # Constants
    "BLK_PRINTER_QUALIFICATION",
    "COMPATIBILITY_PRINTER_PROFILES",
    "CUBE_MAX_MM",
    "CUBE_MIN_MM",
    "CUBE_NOMINAL_MM",
    "ENDER_3_0_4",
    "ENDER_3_PRO_0_4",
    "ENDER_3_S1_PRO_0_4",
    # Strict filament registry
    "FILAMENT_ID_GALAXY_BLACK",
    "FILAMENT_ID_VANILLA_WHITE",
    "PRINTER_ASSETS",
    "PRUSA_XL_0_2",
    "WORKSHOP_PRINTER_PROFILES",
    "PrinterAsset",
    # Templates
    "PrinterProfile",
    # Qualification + wrapper
    "PrinterQualification",
    "QualifiedPrinter",
    # Slice evidence
    "SliceEvidence",
    "filament_approved_for",
    "get_profile",
    "layer_height_mm_for",
    "max_print_minutes_for",
    "printer_asset_for",
    "printer_profile_for",
    "profile_sha256",
    "qualification_matches_evidence",
    "qualify",
    "slice_evidence_matches_evidence",
    "slice_matches_qualification",
]
