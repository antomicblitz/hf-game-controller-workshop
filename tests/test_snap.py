"""Tests for snap-fit coupons and selection records (VS-04).

Run from the session root::

    pytest tests/test_snap.py

Each test exercises one observable property of the snap coupon
module:

* The three candidate coupons are **distinct** Build123d Parts.
* Each candidate's receiver pocket width matches the analytical
  formula (``clip_width + 2 * clearance``).
* SnapSelectionRecord enforces every required gate (cube-receipt,
  ≥5 open-close cycles, known printer).
* snap_pair_is_consistent surfaces mismatched records as
  ``False`` (the validator converts this into a hard error at
  export time).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

_here = Path(__file__).resolve().parent
_session = _here.parent
sys.path.insert(0, str(_session / "tools"))

import pytest  # noqa: E402
from cadkit.printers import (  # noqa: E402
    ENDER_3_PRO_0_4,
    PrinterQualification,
)
from cadkit.snap import (  # noqa: E402
    SNAP_CLEARANCE_CANDIDATES_MM,
    SNAP_COUPON_CLIP_WIDTH_MM,
    SNAP_MIN_OPEN_CLOSE_CYCLES,
    SnapSelectionRecord,
    snap_coupon_header,
    snap_coupon_receiver_width_mm,
    snap_coupon_set,
    snap_pair_is_consistent,
    snap_record_from_qualification,
    snap_record_to_dict,
)


# ---------------------------------------------------------------------------
# Snap coupons (procurement plan §5 row "Snap clearance" / §2.12)
# ---------------------------------------------------------------------------
def test_snap_coupon_set_returns_three_distinct_parts():
    """The three candidate coupons are three distinct Parts."""
    coupons = snap_coupon_set()
    assert len(coupons) == 3
    # Each candidate must have a distinct volume: the receiver pocket
    # grows wider / deeper with each clearance step.
    vols = [p.volume for p in coupons]
    assert vols[0] > vols[1] > vols[2], (
        f"expected strictly decreasing volume, got {vols}; the coupons are not actually distinct"
    )


def test_snap_coupon_receiver_width_matches_analytical():
    """The receiver-pocket width matches the analytical formula.

    Procurement plan §5 row "Snap clearance" requires that each
    candidate gives a *different* mating receiver width — the
    tightest tolerance the printer + filament can cycle five times
    is the one that survives.
    """
    for clearance in SNAP_CLEARANCE_CANDIDATES_MM:
        analytical = snap_coupon_receiver_width_mm(clearance)
        assert analytical == SNAP_COUPON_CLIP_WIDTH_MM + 2 * clearance


def test_snap_coupon_set_coupons_have_distinct_receiver_widths():
    """The three candidate coupons have pairwise-distinct receiver widths."""
    # The receiver width is the inner dimension of the coupon body's
    # subtracted cavity. Confirm via the analytical receiver-width
    # formula (the bounding box stays the same width — the
    # difference is visible in volume, tested above).
    widths = [snap_coupon_receiver_width_mm(c) for c in SNAP_CLEARANCE_CANDIDATES_MM]
    assert widths[0] != widths[1] != widths[2]
    assert len(set(widths)) == 3


def test_snap_coupon_rejects_undocumented_clearance():
    """The coupon builder rejects clearances outside the documented set."""
    with pytest.raises(ValueError, match="not in the documented"):
        snap_coupon_header(0.20)  # too tight — not in the candidate set
    with pytest.raises(ValueError, match="not in the documented"):
        snap_coupon_header(0.40)  # too loose — not in the candidate set
    with pytest.raises(ValueError, match="not in the documented"):
        snap_coupon_header(0.0)  # zero — not in the candidate set


# ---------------------------------------------------------------------------
# SnapSelectionRecord
# ---------------------------------------------------------------------------
def _signed_receipt_kwargs(clearance: float = 0.30) -> dict[str, Any]:
    return dict(
        clearance_mm=clearance,
        printer_id="ender3-pro-01",
        printer_profile_name=ENDER_3_PRO_0_4.name,
        filament="Prusament_Galaxy_Black_ASIN_B0BJXPNG4M",
        open_close_cycles=SNAP_MIN_OPEN_CLOSE_CYCLES,
        signed_by="Antonio Lamb",
        signed_utc="2026-08-26T12:00:00Z",
        receiving_row_id="SNAP-ROW-04",
    )


def _receipt_with(**overrides: Any) -> dict[str, Any]:
    return {**_signed_receipt_kwargs(), **overrides}


def test_snap_selection_record_accepts_candidate_clearance():
    rec = SnapSelectionRecord(**_signed_receipt_kwargs(clearance=0.25))
    assert rec.clearance_mm == 0.25
    assert rec.open_close_cycles == SNAP_MIN_OPEN_CLOSE_CYCLES


def test_snap_selection_record_rejects_undocumented_clearance():
    with pytest.raises(ValueError, match="not in the documented"):
        SnapSelectionRecord(**_signed_receipt_kwargs(clearance=0.20))


def test_snap_selection_record_requires_five_open_close_cycles():
    with pytest.raises(ValueError, match="below the procurement-plan"):
        SnapSelectionRecord(**cast(Any, _receipt_with(open_close_cycles=4)))


def test_snap_selection_record_requires_known_printer_profile():
    with pytest.raises(ValueError, match="not a known workshop template"):
        SnapSelectionRecord(
            **cast(Any, _receipt_with(printer_profile_name="stratasys_polyjet_xyz"))
        )


def test_snap_selection_record_requires_sign_off():
    with pytest.raises(ValueError, match="signed_by"):
        SnapSelectionRecord(**cast(Any, _receipt_with(signed_by="")))
    with pytest.raises(ValueError, match="signed_utc"):
        SnapSelectionRecord(**cast(Any, _receipt_with(signed_utc="")))
    with pytest.raises(ValueError, match="receiving_row_id"):
        SnapSelectionRecord(**cast(Any, _receipt_with(receiving_row_id="")))


# ---------------------------------------------------------------------------
# snap_pair_is_consistent
# ---------------------------------------------------------------------------
def test_snap_pair_is_consistent_when_both_records_match():
    a = SnapSelectionRecord(**_signed_receipt_kwargs())
    b = SnapSelectionRecord(**_signed_receipt_kwargs())
    assert snap_pair_is_consistent(a, b)


def test_snap_pair_is_inconsistent_when_clearance_differs():
    a = SnapSelectionRecord(**_signed_receipt_kwargs(clearance=0.25))
    b = SnapSelectionRecord(**_signed_receipt_kwargs(clearance=0.30))
    assert not snap_pair_is_consistent(a, b)


def test_snap_pair_is_inconsistent_when_filament_differs():
    a = SnapSelectionRecord(**_signed_receipt_kwargs())
    b = SnapSelectionRecord(
        **cast(Any, _receipt_with(filament="Prusament_Vanilla_White_ASIN_B0CJ22RCXS"))
    )
    assert not snap_pair_is_consistent(a, b)


def test_snap_pair_is_inconsistent_when_printer_differs():
    a = SnapSelectionRecord(**_signed_receipt_kwargs())
    b = SnapSelectionRecord(
        **cast(
            Any,
            _receipt_with(printer_id="ender3-s1-pro-01", printer_profile_name="ender3_s1_pro_0.4"),
        )
    )
    assert not snap_pair_is_consistent(a, b)


# ---------------------------------------------------------------------------
# snap_record_from_qualification — derive a snap record from a
# PrinterQualification (same filament + clearance is implicit).
# ---------------------------------------------------------------------------
def test_snap_record_from_qualification_uses_qualification_clearance():
    qualification = PrinterQualification(
        profile_name=ENDER_3_PRO_0_4.name,
        printer_id="ender3-pro-01",
        cube_x_mm=20.0,
        cube_y_mm=20.0,
        cube_z_mm=20.0,
        material_extrusion_passed=True,
        pbs33b_directional_coupon_passed=True,
        guuzi_action_coupon_passed=True,
        snap_coupon_passed=True,
        electronics_backbone_coupon_passed=True,
        support_free_slice_passed=True,
        nozzle_diameter_mm=ENDER_3_PRO_0_4.nozzle_mm,
        filament="Prusament_Galaxy_Black_ASIN_B0BJXPNG4M",
        snap_clearance_mm=0.30,
        signed_by="Antonio Lamb",
        signed_utc="2026-08-26T12:00:00Z",
        receiving_row_id="PRINTER-ROW-04",
        slicer_time_minutes=180.0,
        cube_repeat_count=3,
    )
    rec = snap_record_from_qualification(
        qualification,
        open_close_cycles=7,  # explicit cycles from the snap coupon record
        receiving_row_id="SNAP-ROW-04",
        signed_utc="2026-08-26T12:30:00Z",
        signed_by="Antonio Lamb",
    )
    assert rec.clearance_mm == 0.30
    assert rec.printer_profile_name == ENDER_3_PRO_0_4.name
    assert rec.printer_id == "ender3-pro-01"
    assert rec.filament == "Prusament_Galaxy_Black_ASIN_B0BJXPNG4M"
    # Explicit cycles from the snap coupon record, **not** derived
    # from the cube-repeat count.
    assert rec.open_close_cycles == 7


def test_snap_record_to_dict_round_trip():
    """The dict renderer emits every documented field (used in metadata)."""
    rec = SnapSelectionRecord(**_signed_receipt_kwargs())
    d = snap_record_to_dict(rec)
    assert set(d) == {
        "clearance_mm",
        "printer_id",
        "printer_profile_name",
        "filament",
        "open_close_cycles",
        "signed_by",
        "signed_utc",
        "receiving_row_id",
    }
