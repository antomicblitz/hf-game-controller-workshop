"""Immutable geometry contract for the two-piece shell snap interface.

Generated case source and editor moves never supply this record. Authoritative
editor and production paths apply it after executing the editable unsplit case.
Only the separately qualified receiver clearance remains variable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthoritativeSnapGeometry:
    """Fixed clip, attachment, and support-gusset dimensions in millimetres."""

    clip_length_mm: float = 6.0
    clip_depth_mm: float = 3.0
    clip_height_mm: float = 4.0
    wall_thickness_mm: float = 2.0
    root_overlap_mm: float = 0.5
    x_fractions: tuple[float, float] = (0.25, 0.75)
    max_lower_shell_height_mm: float = 17.75

    @property
    def unsupported_projection_mm(self) -> float:
        """Projection that the permanent gusset must support."""
        return self.clip_depth_mm - self.root_overlap_mm

    @property
    def gusset_rise_mm(self) -> float:
        """Equal run/rise fixes the support face at 45 degrees."""
        return self.unsupported_projection_mm


AUTHORITATIVE_SNAP_GEOMETRY = AuthoritativeSnapGeometry()


__all__ = ["AUTHORITATIVE_SNAP_GEOMETRY", "AuthoritativeSnapGeometry"]
