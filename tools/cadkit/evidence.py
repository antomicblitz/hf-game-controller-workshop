"""Frozen evidence reference — repository artifact integrity + gate binding.

VS-04 review-blocker fix.

The constraint library / slicer wrapper / export pipeline cannot trust
arbitrary non-empty ``signed_by`` / ``signed_utc`` / row-id strings as
proof of evidence. Production-side checks instead **bind** every
qualification / snap selection / geometry-analysis claim to a real
provenance JSON file on disk:

* The file's SHA-256 is computed at load time and recorded on the
  :class:`FrozenEvidenceRef`.
* The file's ``status`` must be ``"FROZEN"`` (procurement-plan
  provenance-schema §"Status lifecycle"). ``DRAFT`` is rejected.
* The relevant gate (``gates.<gate_name>``) must have
  ``passed == true`` with ``signed_by`` and ``signed_utc`` populated
  as non-empty strings.
* The :class:`FrozenEvidenceRef` records the file path, schema
  version, record id, the parsed JSON content, and the expected
  SHA-256.
* A subsequent :meth:`FrozenEvidenceRef.revalidate` re-reads the file
  and recomputes the SHA-256; any byte change is rejected with
  ``EvidenceTamper``.

What this does and does **not** guarantee
---------------------------------------

This module guarantees:

* Repository artifact integrity — the JSON file the validator
  consumed is the same file the production export pipeline
  consumes.
* Gate binding — the gate's ``passed=true`` + signer + timestamp
  pair is exactly the pair the record carries.

This module does **not** guarantee:

* That the human signer is who they claim to be. There is no
  cryptographic identity check; the gate binding is **trust on
  first use** — the receiving checklist carries the human name in
  plain text. The orchestrator's review is the social check; this
  module is the repository check.

Synthetic test records
----------------------

Tests write **explicitly labeled** synthetic records
(``"synthetic-test-only-not-a-real-signoff"`` in the ``signed_by``
field). Production callers always see ``"Antonio Lamb"`` or a
similarly named human in the receiving checklist; the test fixtures
make this clear so a reviewer cannot mistake the synthetic
records for production evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

# Schema identifier for the workshop provenance records. Lives in
# ``docs/provenance-schema.md`` and is referenced by every
# :class:`FrozenEvidenceRef` we accept.
PROVENANCE_SCHEMA_VERSION: str = "1.0"
PROVENANCE_SCHEMA_NAME: str = "hf-il3-2026/provenance/v1"

#: Status values the reference accepts.
STATUS_FROZEN: str = "FROZEN"
STATUS_DRAFT: str = "DRAFT"

#: Default test signer used by the synthetic FROZEN records in
# ``tests/``. Production records must carry a real human name from
# the receiving checklist.
TEST_SIGNER: str = "synthetic-test-only-not-a-real-signoff"

# Every production provenance input is snapshotted before it is parsed.  The
# limit is deliberately shared by printer, coupon, geometry, slice, and
# first-article records so a caller cannot turn a parse into an unbounded read.
MAX_PROVENANCE_BYTES: int = 1 * 1024 * 1024


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ProvenanceError(Exception):
    """Base class for evidence-ref failures."""


class ProvenanceNotFrozen(ProvenanceError):
    """The provenance record's status is not FROZEN."""


class ProvenanceGateNotPassed(ProvenanceError):
    """The relevant gate has not been signed off as passed."""


class ProvenanceSignoffIncomplete(ProvenanceError):
    """The gate's ``signed_by`` or ``signed_utc`` is missing."""


class ProvenanceSchemaMismatch(ProvenanceError):
    """The record's ``schema_version`` does not match the expected version."""


class ProvenanceShapeInvalid(ProvenanceError, ValueError):
    """The record is missing a top-level required field."""


class EvidenceTamper(ProvenanceError):
    """The provenance file's SHA-256 has changed since load time."""


class EvidencePathMissing(ProvenanceError):
    """The provenance file does not exist."""


# ---------------------------------------------------------------------------
# Frozen evidence reference
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FrozenEvidenceRef:
    """Immutable reference to a real provenance JSON file on disk.

    Construction is via the :meth:`load` class method, which reads the
    file, parses the JSON, validates the schema version + status +
    gate + sign-off, computes the SHA-256, and stores everything.
    Direct construction is not supported — the only way to get a
    reference is :meth:`load`.

    Attributes:
        path: Path to the provenance JSON file on disk.
        schema_version: Record's ``schema_version`` (e.g. ``"1.0"``).
        schema_name: Workshop schema name (e.g.
            ``"hf-il3-2026/provenance/v1"``).
        record_id: Record's ``id`` field (must match the filename
            stem for canonical records).
        status: Record's ``status`` (always ``"FROZEN"``).
        gate_name: The gate this reference is bound to (e.g.
            ``"printer"``, ``"coupon"``, ``"geometry"``).
        signed_by: Signer name from the gate record.
        signed_utc: ISO 8601 UTC timestamp from the gate record.
        sha256: SHA-256 of the JSON file at load time.
        content: Parsed JSON content (read-only; do not mutate).
    """

    path: Path
    schema_version: str
    schema_name: str
    record_id: str
    status: str
    gate_name: str
    signed_by: str
    signed_utc: str
    sha256: str
    content: Mapping[str, Any] = field(compare=False)
    _immutable_snapshot: bool = field(default=False, compare=False, repr=False)

    @classmethod
    def load(
        cls,
        path: Path | str,
        *,
        gate_name: str,
        schema_name: str = PROVENANCE_SCHEMA_NAME,
        schema_version: str = PROVENANCE_SCHEMA_VERSION,
    ) -> FrozenEvidenceRef:
        """Load a real provenance JSON file and bind it to ``gate_name``.

        Args:
            path: filesystem path to the provenance JSON file.
            gate_name: name of the gate in ``gates.<gate_name>`` that
                must be ``passed=true`` with signer + timestamp.
            schema_name: expected workshop schema name.
            schema_version: expected schema version.

        Returns:
            Immutable :class:`FrozenEvidenceRef`.

        Raises:
            EvidencePathMissing: when ``path`` does not exist.
            ProvenanceShapeInvalid: when the JSON is missing a
                top-level required field.
            ProvenanceSchemaMismatch: when ``schema_version`` does
                not match.
            ProvenanceNotFrozen: when ``status != "FROZEN"``.
            ProvenanceGateNotPassed: when ``gates.<gate_name>.passed``
                is not ``True``.
            ProvenanceSignoffIncomplete: when the gate's
                ``signed_by`` or ``signed_utc`` is missing / empty.
            json.JSONDecodeError: when the file is not valid JSON.
        """
        path = Path(path)
        raw_bytes = _read_provenance_bytes(path)
        return cls._from_bytes(
            path,
            raw_bytes,
            gate_name=gate_name,
            schema_name=schema_name,
            schema_version=schema_version,
            immutable_snapshot=False,
        )

    @classmethod
    def load_bytes(
        cls,
        path: Path | str,
        raw_bytes: bytes,
        *,
        gate_name: str,
        schema_name: str = PROVENANCE_SCHEMA_NAME,
        schema_version: str = PROVENANCE_SCHEMA_VERSION,
    ) -> FrozenEvidenceRef:
        """Parse one already-snapshotted provenance file exactly once.

        ``raw_bytes`` must come from :func:`_read_provenance_bytes` (or an
        equivalent bounded, no-follow read).  Snapshot references retain the
        parsed content and never re-read the mutable source path during
        ``revalidate``; this is the production finalization TOCTOU boundary.
        """
        return cls._from_bytes(
            path,
            raw_bytes,
            gate_name=gate_name,
            schema_name=schema_name,
            schema_version=schema_version,
            immutable_snapshot=True,
        )

    @classmethod
    def _from_bytes(
        cls,
        path: Path | str,
        raw_bytes: bytes,
        *,
        gate_name: str,
        schema_name: str,
        schema_version: str,
        immutable_snapshot: bool,
    ) -> FrozenEvidenceRef:
        """Validate parsed bytes, optionally retaining them as immutable."""
        path = Path(path)
        if len(raw_bytes) > MAX_PROVENANCE_BYTES:
            raise ProvenanceShapeInvalid(
                f"provenance file {path} exceeds {MAX_PROVENANCE_BYTES} bytes"
            )
        sha = hashlib.sha256(raw_bytes).hexdigest()

        content = _parse_provenance_content(raw_bytes, path)

        # Shape validation: every required top-level field must
        # exist (the value may be null/empty — that is the gate's
        # job to enforce, not the reference loader's).
        for required in (
            "schema_version",
            "status",
            "id",
            "part",
            "receipt",
            "dimensional_observations",
            "evidence",
            "computed",
            "gates",
            "supersedes",
            "license",
        ):
            if required not in content:
                raise ProvenanceShapeInvalid(
                    f"provenance file {path} is missing top-level field {required!r}"
                )

        # Schema version.
        actual_version = content.get("schema_version")
        if actual_version != schema_version:
            raise ProvenanceSchemaMismatch(
                f"provenance file {path}: schema_version={actual_version!r} "
                f"does not match expected {schema_version!r}"
            )

        # Status must be FROZEN.
        actual_status = content.get("status")
        if actual_status != STATUS_FROZEN:
            raise ProvenanceNotFrozen(
                f"provenance file {path}: status={actual_status!r}; "
                f"only {STATUS_FROZEN!r} is acceptable for production"
            )

        signed_by, signed_utc = _validate_gate(content, path, gate_name)

        return cls(
            path=path,
            schema_version=cast(str, actual_version),
            schema_name=schema_name,
            record_id=str(content.get("id") or ""),
            status=cast(str, actual_status),
            gate_name=gate_name,
            signed_by=signed_by,
            signed_utc=signed_utc,
            sha256=sha,
            content=content,
            _immutable_snapshot=immutable_snapshot,
        )

    def revalidate(self) -> None:
        """Re-read the file and re-compute the SHA-256.

        Raises:
            EvidencePathMissing: when ``self.path`` no longer exists.
            EvidenceTamper: when the SHA-256 has changed since load.
        """
        if self._immutable_snapshot:
            return
        try:
            raw_bytes = _read_provenance_bytes(self.path)
        except EvidencePathMissing as exc:
            raise EvidencePathMissing(f"provenance file {self.path} no longer exists") from exc
        sha = hashlib.sha256(raw_bytes).hexdigest()
        if sha != self.sha256:
            raise EvidenceTamper(
                f"provenance file {self.path}: SHA-256 changed from "
                f"{self.sha256} to {sha} since load; refusing to use "
                "tampered evidence"
            )

    def summary(self) -> dict[str, str]:
        """Render a small summary dict for log / metadata use."""
        return {
            "path": str(self.path),
            "record_id": self.record_id,
            "gate_name": self.gate_name,
            "schema_version": self.schema_version,
            "status": self.status,
            "signed_by": self.signed_by,
            "signed_utc": self.signed_utc,
            "sha256": self.sha256,
        }

    @property
    def is_snapshot(self) -> bool:
        """Whether this reference was parsed from an immutable byte snapshot."""
        return self._immutable_snapshot

    # Make the dataclass instances hashable + comparable.
    def __hash__(self) -> int:  # pragma: no cover — trivial
        return hash((str(self.path), self.sha256, self.gate_name))


def _parse_provenance_content(raw_bytes: bytes, path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProvenanceShapeInvalid(f"provenance file {path} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProvenanceShapeInvalid(f"provenance file {path} must contain a JSON object")
    return cast(dict[str, Any], parsed)


def _validate_gate(content: Mapping[str, Any], path: Path, gate_name: str) -> tuple[str, str]:
    gates_value = content.get("gates")
    if not isinstance(gates_value, dict):
        raise ProvenanceShapeInvalid(f"provenance file {path} has malformed gates")
    gates = cast(dict[str, Any], gates_value)
    if gate_name not in gates:
        raise ProvenanceGateNotPassed(
            f"provenance file {path}: gate {gate_name!r} not present in record"
        )
    gate_value = gates[gate_name]
    if not isinstance(gate_value, dict):
        raise ProvenanceShapeInvalid(f"provenance file {path} has malformed gate {gate_name!r}")
    gate = cast(dict[str, Any], gate_value)
    if gate.get("passed") is not True:
        raise ProvenanceGateNotPassed(
            f"provenance file {path}: gate {gate_name!r} has "
            f"passed={gate.get('passed')!r}; production requires passed=True"
        )
    signed_by = _signoff_text(gate.get("signed_by"))
    signed_utc = _signoff_text(gate.get("signed_utc"))
    if not signed_by:
        raise ProvenanceSignoffIncomplete(
            f"provenance file {path}: gate {gate_name!r}.signed_by is empty"
        )
    if not signed_utc:
        raise ProvenanceSignoffIncomplete(
            f"provenance file {path}: gate {gate_name!r}.signed_utc is empty"
        )
    return signed_by, signed_utc


def _signoff_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _read_provenance_bytes(path: Path) -> bytes:
    """Read a bounded regular JSON file once, refusing symlink traversal."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ProvenanceShapeInvalid(f"provenance file {path} is not a regular file")
            raw_bytes = handle.read(MAX_PROVENANCE_BYTES + 1)
    except FileNotFoundError as exc:
        raise EvidencePathMissing(f"provenance file missing: {path}") from exc
    except OSError as exc:
        raise ProvenanceShapeInvalid(f"unable to read provenance file {path}: {exc}") from exc
    if len(raw_bytes) > MAX_PROVENANCE_BYTES:
        raise ProvenanceShapeInvalid(f"provenance file {path} exceeds {MAX_PROVENANCE_BYTES} bytes")
    return raw_bytes


def load_snapshot(
    path: Path | str,
    *,
    gate_name: str,
    schema_name: str = PROVENANCE_SCHEMA_NAME,
    schema_version: str = PROVENANCE_SCHEMA_VERSION,
) -> FrozenEvidenceRef:
    """Read and parse one production evidence input from one immutable read."""
    path = Path(path)
    return FrozenEvidenceRef.load_bytes(
        path,
        _read_provenance_bytes(path),
        gate_name=gate_name,
        schema_name=schema_name,
        schema_version=schema_version,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------
def write_test_frozen_record(
    path: Path | str,
    *,
    id_slug: str,
    gate_name: str,
    signed_by: str = TEST_SIGNER,
    signed_utc: str = "2026-08-26T12:00:00Z",
    schema_version: str = PROVENANCE_SCHEMA_VERSION,
    extra_content: Mapping[str, Any] | None = None,
) -> Path:
    """Write a synthetic FROZEN provenance JSON for tests.

    The signer defaults to :data:`TEST_SIGNER` so a reviewer can tell
    the record is synthetic at a glance. Tests pass
    ``signed_by="Antonio Lamb"`` only when they want to mimic a
    production record.

    Args:
        path: filesystem path to write the JSON to.
        id_slug: the record's ``id`` (matches the filename stem for
            canonical records; tests may diverge).
        gate_name: which gate to bind the FROZEN status to.
        signed_by: signer name (default ``TEST_SIGNER``).
        signed_utc: ISO 8601 UTC timestamp.
        schema_version: workshop schema version (default ``"1.0"``).
        extra_content: optional top-level keys to merge into the
            record (e.g. ``computed.frozen_*`` values for a
            specific feature).

    Returns:
        The path written.
    """
    path = Path(path)
    record: dict[str, Any] = {
        "schema_version": schema_version,
        "status": STATUS_FROZEN,
        "id": id_slug,
        "part": {
            "name": f"test-{id_slug}",
            "sku": f"test-sku-{id_slug}",
            "manufacturer": None,
            "supplier": None,
            "purchase_order": None,
            "listing_url": None,
            "lot_or_revision": None,
            "procurement_section_ref": None,
        },
        "receipt": {
            "received_utc": signed_utc,
            "received_by": signed_by,
            "carrier_tracking": None,
            "carton_condition": None,
            "received_count": None,
            "expected_count": None,
            "accessory_check": None,
        },
        "dimensional_observations": [],
        "vendor_dimensions": {"note": "synthetic-test-only"},
        "evidence": [],
        "computed": {
            "freeze_utc": signed_utc,
        },
        "gates": {
            "button": {"passed": False, "signed_by": None, "signed_utc": None},
            "dpad": {"passed": False, "signed_by": None, "signed_utc": None},
            "electronics_backbone": {"passed": False, "signed_by": None, "signed_utc": None},
            "fastener": {"passed": False, "signed_by": None, "signed_utc": None},
            "printer": {"passed": False, "signed_by": None, "signed_utc": None},
            "coupon": {"passed": False, "signed_by": None, "signed_utc": None},
            "first_article": {"passed": False, "signed_by": None, "signed_utc": None},
            "geometry": {"passed": False, "signed_by": None, "signed_utc": None},
            "slice": {"passed": False, "signed_by": None, "signed_utc": None},
        },
        "supersedes": [],
        "license": {
            "vendor_provided_step": None,
            "license_id": None,
            "attribution": None,
        },
    }
    if gate_name not in record["gates"]:
        raise ValueError(f"gate_name={gate_name!r} is not one of the documented gates")
    record["gates"][gate_name] = {
        "passed": True,
        "signed_by": signed_by,
        "signed_utc": signed_utc,
    }
    if extra_content:
        # Shallow merge at the top level only — tests can override
        # ``computed`` keys by passing them in extra_content.
        for key, value in extra_content.items():
            if isinstance(value, dict) and isinstance(record.get(key), dict):
                record[key].update(value)
            else:
                record[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True))
    return path


def write_test_printer_record(
    path: Path | str,
    *,
    id_slug: str,
    signed_by: str = TEST_SIGNER,
    signed_utc: str = "2026-08-26T12:00:00Z",
    printer_qualification: Mapping[str, Any],
) -> Path:
    """Write a synthetic FROZEN ``"printer"``-gated provenance record.

    The ``computed.printer_qualification`` block carries every
    field :class:`cadkit.printers.PrinterQualification` reads, including the
    required ``electronics_backbone_coupon_passed`` flag. Tests pass a
    mapping; missing keys fail :meth:`PrinterQualification.from_frozen_evidence`.
    """
    block = dict(printer_qualification)
    if "printer_id" not in block and block.get("profile_name"):
        default_assets = {
            "ender3_pro_0.4": "ender3-pro-01",
            "ender3_0.4": "ender3-01",
            "ender3_s1_pro_0.4": "ender3-s1-pro-01",
        }
        if block["profile_name"] in default_assets:
            block["printer_id"] = default_assets[block["profile_name"]]
    # This helper writes explicitly synthetic records; deriving the hash here
    # keeps legacy fixtures complete without permitting production backfill.
    if not block.get("profile_sha256") and block.get("profile_name"):
        from .printers import get_profile, profile_sha256

        with suppress(ValueError):
            block["profile_sha256"] = profile_sha256(get_profile(str(block["profile_name"])))
    return write_test_frozen_record(
        path,
        id_slug=id_slug,
        gate_name="printer",
        signed_by=signed_by,
        signed_utc=signed_utc,
        extra_content={
            "computed": {
                "printer_qualification": block,
            },
        },
    )


def write_test_electronics_backbone_record(
    path: Path | str,
    *,
    id_slug: str,
    signed_by: str = TEST_SIGNER,
    signed_utc: str = "2026-08-26T12:00:00Z",
    computed: Mapping[str, Any] | None = None,
) -> Path:
    """Write a complete synthetic MIG-03 backbone record for tests only.

    Every physical value is deliberately synthetic and the signer defaults to
    :data:`TEST_SIGNER`; no repository production record is created by this
    helper.
    """
    from .electronics_backbone import (
        ELECTRONICS_BACKBONE_BREADBOARD_PRODUCT_URL,
        ELECTRONICS_BACKBONE_HEADER_PRODUCT_URL,
    )

    block: dict[str, Any] = {
        "intended_identity": {
            "breadboard_name": "The Pi Hut Half-Size Breadboard - White",
            "breadboard_supplier_sku": "100058",
            "breadboard_product_url": ELECTRONICS_BACKBONE_BREADBOARD_PRODUCT_URL,
            "feather_pid": "4516",
            "header_id": "ADA2830",
            "header_product_url": ELECTRONICS_BACKBONE_HEADER_PRODUCT_URL,
            "header_pin_counts": [12, 16],
        },
        "seller_topology": {
            "points": 400,
            "rail_point_counts": [50, 50],
            "grid": "30x10",
            "pitch_mm": 2.54,
            "self_adhesive_rear": True,
        },
        "received_identity": {
            "breadboard_name": "The Pi Hut Half-Size Breadboard - White",
            "breadboard_supplier_sku": "100058",
            "feather_pid": "4516",
            "header_id": "ADA2830",
            "header_pin_counts": [12, 16],
        },
        "received_count": 7,
        "expected_count": 7,
        "outside_dimensions_mm": [80.0, 50.0, 10.0],
        "underside_base_condition": "synthetic test base intact",
        "adhesive_condition": "synthetic test adhesive bond intact",
        "assembled_stack_height_mm": 22.0,
        "feather_grid_placement": {
            "coordinate_frame": "breadboard_local_grid",
            "column": 2,
            "row": 1,
        },
        "feather_orientation": "long_axis_-X_usb_facing_left",
        "coordinate_frame": "breadboard_local_xyz",
        "placement_offset_mm": [10.0, 8.0, 2.0],
        "usb_offset_mm": [38.0, 8.0, 22.0],
        "header_insertion_mm": 4.0,
        "header_protrusion_mm": 8.0,
        "usb_plug_clearance_mm": 3.0,
        "retention_method": "factory_adhesive_direct_to_inside_bottom_shell",
        "adhesive_bond_interface": {
            "surface": "inside_bottom_shell",
        },
        "final_placement_result": True,
        "adhesive_bond_result": True,
        "usb_clearance_result": True,
        "interference_result": True,
        "rail_continuity": {"rail_1": True, "rail_2": True},
        "six_signal_continuity": {
            signal: True for signal in ("up", "down", "right", "left", "action_a", "action_b")
        },
        "termination_result": True,
        "coupon_binding": "synthetic-backbone-coupon-2026-08-26",
        "signed_receiving_row": "synthetic-receiving-row-backbone-2026-08-26",
        "evidence_ids": [
            "synthetic-backbone-coupon-2026-08-26",
            "synthetic-backbone-photo-1",
            "synthetic-backbone-measurement-1",
        ],
    }
    if computed:
        block.update(dict(computed))
    return write_test_frozen_record(
        path,
        id_slug=id_slug,
        gate_name="electronics_backbone",
        signed_by=signed_by,
        signed_utc=signed_utc,
        extra_content={"computed": {"electronics_backbone": block}},
    )


def write_test_slice_record(
    path: Path | str,
    *,
    id_slug: str,
    slice_evidence: Mapping[str, Any] | object,
    signed_by: str = TEST_SIGNER,
    signed_utc: str = "2026-08-26T12:00:00Z",
) -> Path:
    """Write a synthetic FROZEN ``"slice"``-gated invocation record.

    The mapping is deliberately copied without filling production fields.
    This keeps test records honest: callers must provide the exact values
    emitted by the slicer, including all four artifact hashes.
    """
    if isinstance(slice_evidence, Mapping):
        block = dict(cast(Mapping[str, Any], slice_evidence))
    else:
        block = {
            field_name: getattr(slice_evidence, field_name)
            for field_name in (
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
        }
    if "printer_id" not in block:
        block["printer_id"] = {
            "ender3_pro_0.4": "ender3-pro-01",
            "ender3_0.4": "ender3-01",
            "ender3_s1_pro_0.4": "ender3-s1-pro-01",
        }.get(str(block.get("profile_name")), "")
    return write_test_frozen_record(
        path,
        id_slug=id_slug,
        gate_name="slice",
        signed_by=signed_by,
        signed_utc=signed_utc,
        extra_content={"computed": {"slice_evidence": block}},
    )


def write_test_coupon_record(
    path: Path | str,
    *,
    id_slug: str,
    signed_by: str = TEST_SIGNER,
    signed_utc: str = "2026-08-26T12:00:00Z",
    snap_selection_top: Mapping[str, Any] | None = None,
    snap_selection_bottom: Mapping[str, Any] | None = None,
    selected_clearance_mm: float | None = None,
) -> Path:
    """Write a synthetic FROZEN ``"coupon"``-gated provenance record.

    The ``computed.snap_selection`` block carries the snap
    selection records. Tests pass the top + bottom mappings
    (each must include every field
    :class:`cadkit.snap.SnapSelectionRecord` reads).
    """
    block: dict[str, Any] = {}
    if snap_selection_top is not None:
        block["top"] = dict(snap_selection_top)
    if snap_selection_bottom is not None:
        block["bottom"] = dict(snap_selection_bottom)
    for side in ("top", "bottom"):
        if side in block and "printer_id" not in block[side]:
            block[side]["printer_id"] = "ender3-pro-01"
    if selected_clearance_mm is not None:
        block["selected_clearance_mm"] = float(selected_clearance_mm)
    return write_test_frozen_record(
        path,
        id_slug=id_slug,
        gate_name="coupon",
        signed_by=signed_by,
        signed_utc=signed_utc,
        extra_content={
            "computed": {
                "snap_selection": block,
            },
        },
    )


__all__ = [
    "MAX_PROVENANCE_BYTES",
    "PROVENANCE_SCHEMA_NAME",
    "PROVENANCE_SCHEMA_VERSION",
    "STATUS_DRAFT",
    "STATUS_FROZEN",
    "TEST_SIGNER",
    "EvidencePathMissing",
    "EvidenceTamper",
    "FrozenEvidenceRef",
    "ProvenanceError",
    "ProvenanceGateNotPassed",
    "ProvenanceNotFrozen",
    "ProvenanceSchemaMismatch",
    "ProvenanceShapeInvalid",
    "ProvenanceSignoffIncomplete",
    "load_snapshot",
    "write_test_coupon_record",
    "write_test_electronics_backbone_record",
    "write_test_frozen_record",
    "write_test_printer_record",
    "write_test_slice_record",
]
