"""Snap-fit coupons and selection records (VS-04).

The snap-fit tolerance is **not** a software constant. Procurement plan
§5 row "Snap clearance" requires:

* Default ``0.30 mm`` per mating side.
* Candidates ``(0.25, 0.30, 0.35) mm``.
* Selection by coupon: each candidate receiver is a distinct
  geometry; the receiving checklist picks the one that survives
  **≥ 5 open-close cycles** on the qualified printer + filament.
* Both halves of one controller print on the **same** qualified
  printer with the **same** profile and the **same** filament spool
  (procurement plan §6 "Printer plan").
* The chosen clearance is part of the printer qualification
  (:class:`cadkit.printers.PrinterQualification.snap_clearance_mm`)
  and is **also** embedded in production metadata.

This module exposes:

* :func:`snap_coupon_header` — one coupon Part. The receiver pocket
  width is ``clip_width + 2 * clearance_mm``, so each candidate gives
  a *different* geometry. **Non-production** (labelled by name).
* :func:`snap_coupon_set` — the three candidate coupons as a tuple.
* :class:`SnapSelectionRecord` — the receiving-signed selection
  (clearance + printer + filament + ≥ 5 cycles + sign-off).
* :func:`snap_pair_is_consistent` — enforce that two selection
  records (top / bottom of one controller) use the same printer,
  filament, and clearance. Used by the validator and by the export
  pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from build123d import (
    Align,
    Box,
    BuildPart,
    BuildSketch,
    Locations,
    Mode,
    Part,
    Plane,
    Rectangle,
    extrude,
)

from . import parametric as _p
from . import printers as _printers
from .evidence import FrozenEvidenceRef as _FrozenEvidenceRef
from .snap_geometry import AUTHORITATIVE_SNAP_GEOMETRY as _SNAP_GEOMETRY

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
#: Default per-side clearance (procurement plan §5 row "Snap clearance").
SNAP_DEFAULT_CLEARANCE_MM: float = 0.30
#: Candidate clearances the receiving checklist selects between. The
#: coupon geometry changes with each value (the receiver pocket grows
#: with ``2 * clearance``), so the choice is observable.
SNAP_CLEARANCE_CANDIDATES_MM: tuple[float, ...] = _p.SNAP_CLEARANCE_CANDIDATES_MM
#: Open-close cycles the snap must survive (procurement plan §5 row
#: "Snap clearance" / §6 "Snap cycling").
SNAP_MIN_OPEN_CLOSE_CYCLES: int = 5

#: Coupon dimensions. The header is a small box (40 × 25 × 8 mm) with
#: one clip on the long axis and a matching receiver slot on the
#: opposite side. Changing the clearance widens the receiver pocket
#: by ``2 * clearance``.
SNAP_COUPON_LENGTH_MM: float = 40.0
SNAP_COUPON_WIDTH_MM: float = 25.0
SNAP_COUPON_HEIGHT_MM: float = 8.0
#: The clip protrusion (always 6 × 3 mm; the receiver only widens).
SNAP_COUPON_CLIP_LENGTH_MM: float = _SNAP_GEOMETRY.clip_length_mm
SNAP_COUPON_CLIP_WIDTH_MM: float = _SNAP_GEOMETRY.clip_depth_mm
SNAP_COUPON_CLIP_HEIGHT_MM: float = _SNAP_GEOMETRY.clip_height_mm


# ---------------------------------------------------------------------------
# Coupon geometry
# ---------------------------------------------------------------------------
def snap_coupon_header(clearance_mm: float) -> Part:
    """A printable snap-fit coupon for ``clearance_mm`` per side.

    The coupon is a small box (40 × 25 × 8 mm) with:

    * A positive clip protrusion (``6 × 3 × 4`` mm) on one end,
      centred on the long axis.
    * A receiver pocket on the opposite end that is
      ``clip_length + 2 * clearance_mm`` long,
      ``clip_width  + 2 * clearance_mm`` wide, and
      ``clip_height + 1 * clearance_mm`` deep.

    The geometry is *real*: a different ``clearance_mm`` produces a
    visibly different receiver pocket. This is what the receiving
    checklist uses to observe the surviving candidate (the tightest
    tolerance the printer + filament can cycle five times).

    Args:
        clearance_mm: per-side clearance; must be one of
            :data:`SNAP_CLEARANCE_CANDIDATES_MM` so the receiving
            checklist picks from the documented set.

    Returns:
        Build123d ``Part``. **Non-production.**

    Raises:
        ValueError: if ``clearance_mm`` is not in the documented
            candidate set.
    """
    if clearance_mm not in SNAP_CLEARANCE_CANDIDATES_MM:
        raise ValueError(
            f"snap_coupon_header clearance_mm={clearance_mm} is not in the "
            f"documented candidates {SNAP_CLEARANCE_CANDIDATES_MM}; "
            "receiving must pick from the documented set."
        )
    if clearance_mm <= 0:
        raise ValueError("clearance_mm must be positive.")

    length = SNAP_COUPON_LENGTH_MM
    width = SNAP_COUPON_WIDTH_MM
    height = SNAP_COUPON_HEIGHT_MM
    clip_len = SNAP_COUPON_CLIP_LENGTH_MM
    clip_w = SNAP_COUPON_CLIP_WIDTH_MM
    clip_h = SNAP_COUPON_CLIP_HEIGHT_MM

    # The clip is centred at one end of the coupon (x = +length/2 -
    # clip_len/2). The receiver pocket is centred at the opposite end
    # (x = -length/2 + receiver_len/2).
    receiver_len = clip_len + 2 * clearance_mm
    receiver_w = clip_w + 2 * clearance_mm
    receiver_h = clip_h + clearance_mm  # one-sided depth margin

    with BuildPart() as bp:
        # Coupon body.
        Box(length, width, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
        # Positive clip.
        with Locations((length / 2 - clip_len / 2, 0, height / 2)):
            Box(
                clip_len,
                clip_w,
                clip_h,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        # Receiver pocket — subtract the cavity from the opposite end.
        # The pocket floor is at z = (height - receiver_h); the pocket
        # extends from the floor to the top face (z = height). We sketch
        # at z = height - receiver_h, then extrude upward by receiver_h.
        with Locations((-length / 2 + receiver_len / 2, 0)):
            with BuildSketch(Plane.XY.offset(height - receiver_h)):
                Rectangle(receiver_len, receiver_w)
            extrude(amount=receiver_h, mode=Mode.SUBTRACT)
    return cast(Part, bp.part)


def snap_coupon_set() -> tuple[Part, Part, Part]:
    """The three candidate coupons as a tuple (0.25, 0.30, 0.35 mm).

    Each is a distinct Build123d ``Part`` whose receiver pocket is
    wider / deeper by ``2 * clearance``. Receiving prints all three,
    cycles each five times on the qualified printer + filament, and
    picks the smallest clearance that survives.
    """
    return (
        snap_coupon_header(SNAP_CLEARANCE_CANDIDATES_MM[0]),
        snap_coupon_header(SNAP_CLEARANCE_CANDIDATES_MM[1]),
        snap_coupon_header(SNAP_CLEARANCE_CANDIDATES_MM[2]),
    )


def snap_coupon_receiver_width_mm(clearance_mm: float) -> float:
    """The receiver-pocket width for ``clearance_mm`` per side.

    Returns:
        ``clip_width + 2 * clearance_mm``. Useful for tests that
        assert the three candidates are *distinct* (the receiver
        pocket is geometrically different for each candidate).
    """
    if clearance_mm not in SNAP_CLEARANCE_CANDIDATES_MM:
        raise ValueError(
            f"clearance_mm={clearance_mm} is not in the documented "
            f"candidates {SNAP_CLEARANCE_CANDIDATES_MM}."
        )
    return SNAP_COUPON_CLIP_WIDTH_MM + 2 * clearance_mm


# ---------------------------------------------------------------------------
# Selection record
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SnapSelectionRecord:
    """The receiving-signed snap clearance selection.

    Carries the chosen per-side clearance, the printer profile name
    the coupon was printed on, the filament identity (manufacturer +
    SKU + spool batch), the number of open-close cycles survived, and
    the sign-off. Production callers pair one record per case half;
    the validator enforces that both records describe the same
    printer + filament + clearance.

    Production-bound records must come from a real provenance
    file (gate ``"coupon"``) via :meth:`from_frozen_evidence`.
    Direct dataclass construction leaves ``bound_evidence=None``;
    the validator / export pipeline refuses unbound records.
    """

    clearance_mm: float
    printer_id: str
    printer_profile_name: str
    filament: str
    open_close_cycles: int
    signed_by: str
    signed_utc: str
    receiving_row_id: str
    bound_evidence: _FrozenEvidenceRef | None = None

    def __post_init__(self) -> None:
        if self.clearance_mm not in SNAP_CLEARANCE_CANDIDATES_MM:
            raise ValueError(
                f"SnapSelectionRecord.clearance_mm={self.clearance_mm} is "
                f"not in the documented candidates "
                f"{SNAP_CLEARANCE_CANDIDATES_MM}; receiving must pick from "
                "the coupon set."
            )
        if self.printer_profile_name not in {p.name for p in _printers.WORKSHOP_PRINTER_PROFILES}:
            raise ValueError(
                f"SnapSelectionRecord.printer_profile_name="
                f"{self.printer_profile_name!r} is not a known workshop "
                "template; the coupon must be printed on a qualified "
                "printer."
            )
        _printers.printer_asset_for(self.printer_id, self.printer_profile_name)
        if not self.filament:
            raise ValueError("filament is required.")
        if self.open_close_cycles < SNAP_MIN_OPEN_CLOSE_CYCLES:
            raise ValueError(
                f"SnapSelectionRecord.open_close_cycles="
                f"{self.open_close_cycles} is below the procurement-plan "
                f"minimum {SNAP_MIN_OPEN_CLOSE_CYCLES}; the snap must "
                "survive at least five cycles."
            )
        if not self.signed_by or not self.signed_utc or not self.receiving_row_id:
            raise ValueError("signed_by / signed_utc / receiving_row_id are required.")

    @classmethod
    def from_frozen_evidence(
        cls,
        evidence: _FrozenEvidenceRef | Path | str,
        *,
        side: str,
    ) -> SnapSelectionRecord:
        """Bind a :class:`SnapSelectionRecord` to a real provenance file.

        ``side`` selects the top or bottom sub-record from the
        JSON's ``computed.snap_selection`` block. The dataclass
        cross-checks every documented field against the JSON
        content. Mismatches raise :class:`ProvenanceShapeInvalid`.
        """
        from .evidence import FrozenEvidenceRef  # local import

        ref = (
            evidence
            if isinstance(evidence, FrozenEvidenceRef)
            else FrozenEvidenceRef.load(evidence, gate_name="coupon")
        )
        if side not in ("top", "bottom"):
            from .evidence import ProvenanceShapeInvalid

            raise ProvenanceShapeInvalid(
                f"snap_record.from_frozen_evidence: side must be 'top' or 'bottom', got {side!r}"
            )
        computed = cast(Mapping[str, Any], ref.content.get("computed") or {})
        block = computed.get("snap_selection")
        if not isinstance(block, dict):
            from .evidence import ProvenanceShapeInvalid

            raise ProvenanceShapeInvalid(
                f"provenance file {ref.path}: missing computed.snap_selection"
            )
        block = cast(dict[str, Any], block)
        sub = block.get(side)
        if not isinstance(sub, dict):
            from .evidence import ProvenanceShapeInvalid

            raise ProvenanceShapeInvalid(
                f"provenance file {ref.path}: computed.snap_selection.{side} is missing"
            )
        sub = cast(dict[str, Any], sub)
        required = (
            "clearance_mm",
            "printer_id",
            "printer_profile_name",
            "filament",
            "open_close_cycles",
            "receiving_row_id",
        )
        for key in required:
            if key not in sub:
                from .evidence import ProvenanceShapeInvalid

                raise ProvenanceShapeInvalid(
                    f"provenance file {ref.path}: computed.snap_selection.{side}.{key} is missing"
                )
        # The sign-off (signed_by / signed_utc) is recorded on the
        # gate itself (the receiving checklist signs the row, not
        # the dataclass sub-block).
        return cls(
            clearance_mm=float(sub["clearance_mm"]),
            printer_id=sub["printer_id"],
            printer_profile_name=sub["printer_profile_name"],
            filament=sub["filament"],
            open_close_cycles=int(sub["open_close_cycles"]),
            signed_by=ref.signed_by,
            signed_utc=ref.signed_utc,
            receiving_row_id=sub["receiving_row_id"],
            bound_evidence=ref,
        )

    def revalidate(self) -> None:
        """Re-read the bound provenance file (delegated)."""
        if self.bound_evidence is None:
            from .evidence import ProvenanceError

            raise ProvenanceError(
                "SnapSelectionRecord.bound_evidence is None; this record "
                "was not loaded from a real provenance file and cannot "
                "be revalidated. Use from_frozen_evidence(...) for "
                "production-bound records."
            )
        self.bound_evidence.revalidate()


def snap_pair_is_consistent(top: SnapSelectionRecord, bottom: SnapSelectionRecord) -> bool:
    """True iff the top and bottom selection records describe the same
    printer + filament + clearance.

    Procurement plan §6 row "Printer plan": "Print both halves of a
    controller on the same printer, using the same profile and
    filament spool/type." Mixed printer / filament / clearance
    silently degrades the snap; the validator surfaces this as a
    hard error before any export.
    """
    return (
        top.clearance_mm == bottom.clearance_mm
        and top.printer_id == bottom.printer_id
        and top.printer_profile_name == bottom.printer_profile_name
        and top.filament == bottom.filament
    )


def snap_record_from_qualification(
    qualification: _printers.PrinterQualification,
    open_close_cycles: int,
    receiving_row_id: str,
    signed_utc: str,
    signed_by: str,
) -> SnapSelectionRecord:
    """Build a :class:`SnapSelectionRecord` from a
    :class:`PrinterQualification` plus explicit snap coupon evidence.

    The qualification record already carries the chosen
    ``snap_clearance_mm`` and the filament identity. The
    **explicit** open-close cycles / receiving row id / sign-off /
    timestamp come from the snap coupon's signed provenance record
    (gate ``coupon``); they are **never** derived from the cube
    repeat count — that would conflate two unrelated coupons.
    """

    return SnapSelectionRecord(
        clearance_mm=qualification.snap_clearance_mm,
        printer_id=qualification.printer_id,
        printer_profile_name=qualification.profile_name,
        filament=qualification.filament,
        open_close_cycles=open_close_cycles,
        signed_by=signed_by,
        signed_utc=signed_utc,
        receiving_row_id=receiving_row_id,
    )


def snap_record_to_dict(record: SnapSelectionRecord) -> Mapping[str, object]:
    """Render a :class:`SnapSelectionRecord` as a dict for log / metadata."""
    return {
        "clearance_mm": record.clearance_mm,
        "printer_id": record.printer_id,
        "printer_profile_name": record.printer_profile_name,
        "filament": record.filament,
        "open_close_cycles": record.open_close_cycles,
        "signed_by": record.signed_by,
        "signed_utc": record.signed_utc,
        "receiving_row_id": record.receiving_row_id,
    }


def snap_record_matches_evidence(
    record: SnapSelectionRecord,
    evidence: _FrozenEvidenceRef | Path | str,
    *,
    side: str,
) -> bool:
    """True iff the snap record is bound to ``evidence`` and current.

    Re-reads the file, re-hashes, cross-checks every documented
    field against the parsed JSON. Mismatches raise
    :class:`EvidenceTamper`. Production callers invoke this at
    the gate and react to the exception via
    :class:`cadkit.constraints.ConstraintViolation` (never
    :class:`ValueError`).
    """
    from .evidence import EvidenceTamper, FrozenEvidenceRef  # local import

    if isinstance(evidence, FrozenEvidenceRef):
        ref = evidence
    else:
        ref = FrozenEvidenceRef.load(evidence, gate_name="coupon")
    if record.bound_evidence is None:
        return False
    ref.revalidate()
    if record.bound_evidence.sha256 != ref.sha256:
        raise EvidenceTamper(
            f"SnapSelectionRecord.bound_evidence sha256 "
            f"{record.bound_evidence.sha256} does not match the "
            f"currently-supplied evidence sha256 {ref.sha256}"
        )
    computed = cast(Mapping[str, Any], ref.content.get("computed") or {})
    block = cast(Mapping[str, Any], computed.get("snap_selection") or {})
    sub = cast(Mapping[str, Any], block.get(side) or {})
    checks = {
        "clearance_mm": record.clearance_mm,
        "printer_id": record.printer_id,
        "printer_profile_name": record.printer_profile_name,
        "filament": record.filament,
        "open_close_cycles": record.open_close_cycles,
        "receiving_row_id": record.receiving_row_id,
    }
    for field_name, expected in checks.items():
        actual = sub.get(field_name)
        if isinstance(expected, (int, float)):
            if actual is None or abs(float(actual) - expected) > 1e-9:
                raise EvidenceTamper(
                    f"snap_record.{field_name}={expected!r} does not match "
                    f"evidence computed.snap_selection.{side}.{field_name}="
                    f"{actual!r}"
                )
        elif actual != expected:
            raise EvidenceTamper(
                f"snap_record.{field_name}={expected!r} does not match "
                f"evidence computed.snap_selection.{side}.{field_name}="
                f"{actual!r}"
            )
    return True


__all__ = [
    "SNAP_CLEARANCE_CANDIDATES_MM",
    "SNAP_COUPON_CLIP_HEIGHT_MM",
    "SNAP_COUPON_CLIP_LENGTH_MM",
    "SNAP_COUPON_CLIP_WIDTH_MM",
    "SNAP_COUPON_HEIGHT_MM",
    "SNAP_COUPON_LENGTH_MM",
    "SNAP_COUPON_WIDTH_MM",
    # Constants
    "SNAP_DEFAULT_CLEARANCE_MM",
    "SNAP_MIN_OPEN_CLOSE_CYCLES",
    # Selection record
    "SnapSelectionRecord",
    # Coupon geometry
    "snap_coupon_header",
    "snap_coupon_receiver_width_mm",
    "snap_coupon_set",
    "snap_pair_is_consistent",
    "snap_record_from_qualification",
    "snap_record_matches_evidence",
    "snap_record_to_dict",
]
