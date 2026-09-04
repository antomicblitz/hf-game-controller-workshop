"""Printer profile registry — single source of truth for known templates.

VS-04 review-blocker fix.

The workshop accepts only the **enumerated** printer profiles in
:data:`KNOWN_PRINTER_PROFILE_TEMPLATES`. The retained XL and legacy MK4
profiles are kept under :data:`DEPRECATED_PROFILE_TEMPLATES` for backwards
compatibility with old worked examples — the slicer wrapper
**refuses** deprecated profiles for production slicing / export
and surfaces the error message documented in
:func:`is_production_profile_template`.

Adding a new printer is a code change (deliberate; no silent adoption).
Each profile is a software **template** that is flagged
``qualified=False`` until a real :class:`cadkit.printers.PrinterQualification`
record is signed (see procurement plan §5 row "Printer volume" / §7
risks 6 + 7).

Block named ``BLK-PRINTER-QUALIFICATION`` tracks the physical gate
that lifts ``qualified`` for each template.
"""

from __future__ import annotations

from pathlib import Path

# Layout: sessions/2026-09-04-05/tools/agent/<this>.py
#          sessions/2026-09-04-05/tools/slice/  ← profiles
_HERE = Path(__file__).resolve().parent
_SLICE_DIR = _HERE.parent / "slice"

# ---------------------------------------------------------------------------
# Production profile templates (procurement plan §5)
# ---------------------------------------------------------------------------
#: Creality Ender-3 with a 0.4 mm nozzle. Template is unqualified until
#: ``BLK-PRINTER-NOZZLE`` and ``BLK-PRINTER-QUALIFICATION`` sign off.
ENDER_3_0_4_TEMPLATE: str = str(_SLICE_DIR / "ender3_0.4.cfg")

#: Creality Ender-3 Pro with a 0.4 mm nozzle. Template is unqualified until
#: ``BLK-PRINTER-NOZZLE`` and ``BLK-PRINTER-QUALIFICATION`` sign off.
ENDER_3_PRO_0_4_TEMPLATE: str = str(_SLICE_DIR / "ender3_pro_0.4.cfg")

#: Creality Ender-3 S1 Pro with a 0.4 mm nozzle. Template is unqualified
#: until ``BLK-PRINTER-NOZZLE`` and ``BLK-PRINTER-QUALIFICATION`` sign off.
ENDER_3_S1_PRO_0_4_TEMPLATE: str = str(_SLICE_DIR / "ender3_s1_pro_0.4.cfg")

#: Retained Prusa XL with a 0.2 mm nozzle — compatibility-only historical
#: profile. It is not an active workshop production profile.
PRUSA_XL_0_2_TEMPLATE: str = str(_SLICE_DIR / "prusa_xl_0.2.cfg")

#: Legacy MK4 placeholder profile. **Deprecated for production.** New
#: callers must select one of the production templates via
#: :func:`is_production_profile_template`. The wrapper refuses
#: deprecated profiles with :class:`agent.slicer.DeprecatedProfile`
#: so worked examples that pre-date VS-04 cannot accidentally
#: export production geometry through the legacy placeholder.
PRUSA_MK4_LEGACY_TEMPLATE: str = str(_SLICE_DIR / "prusa.mk4.cfg")


# ---------------------------------------------------------------------------
# Registry split — production vs. deprecated
# ---------------------------------------------------------------------------
#: Profiles that the slicer wrapper / export pipeline **may slice
#: or export**. ``is_known_profile_template`` accepts paths in this
#: set; the slicer wrapper accepts profiles in this set as
#: production candidates; the export pipeline emits production
#: metadata for profiles in this set.
KNOWN_PRINTER_PROFILE_TEMPLATES: tuple[str, ...] = (
    ENDER_3_0_4_TEMPLATE,
    ENDER_3_PRO_0_4_TEMPLATE,
    ENDER_3_S1_PRO_0_4_TEMPLATE,
)

#: Deprecated XL and MK4 profiles kept for **compatibility only**. The slicer
#: wrapper refuses them with :class:`agent.slicer.DeprecatedProfile`
#: (``BLK-PRINTER-PROFILE-DEPRECATED``). Worked examples that
#: pre-date VS-04 may keep importing the path; the wrapper
#: surfaces a clear refusal rather than silently producing
#: production geometry through the legacy placeholder.
DEPRECATED_PROFILE_TEMPLATES: tuple[str, ...] = (
    PRUSA_XL_0_2_TEMPLATE,
    PRUSA_MK4_LEGACY_TEMPLATE,
)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def is_known_profile_template(path: str | Path) -> bool:
    """True iff ``path`` is one of the production workshop templates.

    Uses absolute resolution so callers cannot trick the wrapper
    with a relative path that points at a different file. The
    wrapper refuses any unknown / replacement profile — see
    procurement plan §5 row "Printer volume" and §7 risk 6.
    """
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        return False
    for known in KNOWN_PRINTER_PROFILE_TEMPLATES:
        try:
            if Path(known).resolve() == Path(resolved):
                return True
        except OSError:
            continue
    return False


def is_deprecated_profile_template(path: str | Path) -> bool:
    """True iff ``path`` is a documented deprecated template.

    The slicer wrapper uses this to emit
    :class:`agent.slicer.DeprecatedProfile` with a refusal message
    naming ``BLK-PRINTER-PROFILE-DEPRECATED``.
    """
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        return False
    for dep in DEPRECATED_PROFILE_TEMPLATES:
        try:
            if Path(dep).resolve() == Path(resolved):
                return True
        except OSError:
            continue
    return False


def is_production_profile_template(path: str | Path) -> bool:
    """True iff ``path`` is a production template (not deprecated).

    Production slicing / export accept only profiles in
    :data:`KNOWN_PRINTER_PROFILE_TEMPLATES` that are **not** in
    :data:`DEPRECATED_PROFILE_TEMPLATES`. The retained XL and MK4
    placeholders are excluded from production.
    """
    return is_known_profile_template(path) and not is_deprecated_profile_template(path)


__all__ = [
    "DEPRECATED_PROFILE_TEMPLATES",
    "ENDER_3_0_4_TEMPLATE",
    "ENDER_3_PRO_0_4_TEMPLATE",
    "ENDER_3_S1_PRO_0_4_TEMPLATE",
    "KNOWN_PRINTER_PROFILE_TEMPLATES",
    "PRUSA_MK4_LEGACY_TEMPLATE",
    "PRUSA_XL_0_2_TEMPLATE",
    "is_deprecated_profile_template",
    "is_known_profile_template",
    "is_production_profile_template",
]
