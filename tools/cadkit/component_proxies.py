"""Deterministic Build123d geometry for every standard assembly proxy.

These shapes are deliberately envelope-first.  They are safe offline fallbacks
for editor selection and collision preview, not manufacturing models.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from build123d import Align, Box, Cylinder, Location, Part

from .assembly import AssemblyNode, AssemblyScene, Dimensions, case_local_to_build123d

STANDARD_PROXY_REFS = (
    "case-shell-3d-proxy",
    "case-bottom-3d-proxy",
    "case-top-3d-proxy",
    "pbs33b-generated-proxy",
    "guuzi-b09bmrdptn-proxy",
    "breadboard-preview-3d",
    "feather-pid4516-3d",
    "pid2830-12-pin-proxy",
    "pid2830-16-pin-proxy",
    "pid2830-12-pin-connector-proxy",
    "pid2830-16-pin-connector-proxy",
    "micro-usb-proxy",
    "rear-usb-opening-proxy",
    "m2-5-fastener-proxy",
    "heat-shrink-proxy",
    "strain-relief-proxy",
    "harness-no-pinch-proxy",
    "wire-spares-proxy",
)

DEFAULT_PROXY_DIMENSIONS: dict[str, tuple[float, float, float]] = {
    "case-shell-3d-proxy": (130.0, 90.0, 65.0),
    "case-bottom-3d-proxy": (130.0, 90.0, 17.75),
    "case-top-3d-proxy": (130.0, 90.0, 47.25),
    "pbs33b-generated-proxy": (12.0, 12.0, 20.0),
    "guuzi-b09bmrdptn-proxy": (12.0, 12.0, 20.0),
    "breadboard-preview-3d": (85.0, 55.0, 20.0),
    "feather-pid4516-3d": (50.8, 22.86, 7.2),
    "pid2830-12-pin-proxy": (27.94, 2.54, 8.5),
    "pid2830-16-pin-proxy": (38.10, 2.54, 8.5),
    "pid2830-12-pin-connector-proxy": (27.94, 4.0, 8.5),
    "pid2830-16-pin-connector-proxy": (38.10, 4.0, 8.5),
    "micro-usb-proxy": (6.0, 14.0, 8.0),
    "rear-usb-opening-proxy": (6.0, 14.0, 8.0),
    "m2-5-fastener-proxy": (5.3, 5.3, 3.0),
    "m3-fastener-proxy": (5.8, 5.8, 3.0),
    "micro-usb-plug-proxy": (12.0, 20.0, 8.0),
    "heat-shrink-proxy": (6.0, 6.0, 8.0),
    "strain-relief-proxy": (8.0, 10.0, 8.0),
    "harness-no-pinch-proxy": (44.0, 10.0, 18.0),
    "wire-spares-proxy": (2.0, 19.0, 2.0),
}


class ProxyGeometryError(ValueError):
    """Raised for an unknown proxy reference or invalid dimensions."""


def _dimensions(value: Sequence[float]) -> tuple[float, float, float]:
    if len(value) != 3:
        raise ProxyGeometryError("proxy dimensions require three values")
    result = tuple(float(part) for part in value)
    if any(part <= 0 for part in result):
        raise ProxyGeometryError("proxy dimensions must be positive")
    return result  # type: ignore[return-value]


def _box(dimensions: tuple[float, float, float]) -> Part:
    return cast(
        Part,
        Box(*dimensions, align=(Align.CENTER, Align.CENTER, Align.CENTER)),
    )


def _button(dimensions: tuple[float, float, float]) -> Part:
    width, depth, height = dimensions
    body = Cylinder(
        min(width, depth, 12.0) / 2.0,
        height * 0.8,
        align=(Align.CENTER, Align.CENTER, Align.CENTER),
    ).located(Location((0.0, 0.0, -height * 0.1)))
    bezel = Cylinder(
        min(width, depth) / 2.0, height * 0.1, align=(Align.CENTER, Align.CENTER, Align.CENTER)
    ).located(Location((0.0, 0.0, height * 0.3)))
    cap = Cylinder(
        min(width, depth, 10.0) / 2.0,
        height * 0.2,
        align=(Align.CENTER, Align.CENTER, Align.CENTER),
    ).located(Location((0.0, 0.0, height * 0.4)))
    return cast(Part, body + bezel + cap)


def _fastener(dimensions: tuple[float, float, float]) -> Part:
    return cast(
        Part,
        Cylinder(
            min(dimensions[0], dimensions[1]) / 2.0,
            dimensions[2],
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        ),
    )


def proxy_for_ref(ref: str, dimensions: Sequence[float] | None = None) -> Part:
    """Build one local-origin proxy without reading files or using the network."""

    if ref not in DEFAULT_PROXY_DIMENSIONS:
        raise ProxyGeometryError(f"no deterministic proxy registered for {ref!r}")
    size = _dimensions(dimensions or DEFAULT_PROXY_DIMENSIONS[ref])
    if ref in {"pbs33b-generated-proxy", "guuzi-b09bmrdptn-proxy"}:
        return _button(size)
    if ref in {"m2-5-fastener-proxy", "m3-fastener-proxy", "heat-shrink-proxy"}:
        return _fastener(size)
    return _box(size)


def proxy_for_node(
    node: AssemblyNode, *, case_dimensions: Dimensions | Sequence[float] | None = None
) -> Part:
    """Build and place a node proxy in either its local or case-local frame."""

    part = proxy_for_ref(
        node.proxy_ref,
        (
            node.physical_dimensions.x,
            node.physical_dimensions.y,
            node.physical_dimensions.z,
        ),
    )
    position = node.transform.position
    if case_dimensions is not None:
        position = case_local_to_build123d(position, case_dimensions)
    return part.located(Location(position, node.transform.rotation_deg))


def shared_usb_opening(dimensions: Sequence[float]) -> Part:
    """Build the local-origin cutter for the canonical shared USB opening."""

    return _box(_dimensions(dimensions))


def build_scene_proxies(scene: AssemblyScene) -> dict[str, Part]:
    """Return deterministic, placed geometry for every scene node."""

    return {
        node.id: proxy_for_node(node, case_dimensions=scene.case_dimensions)
        for node in scene.nodes
        if node.proxy_ref in DEFAULT_PROXY_DIMENSIONS
    }


def available_proxy_refs() -> tuple[str, ...]:
    """Return stable proxy references used by the standard assembly."""

    return STANDARD_PROXY_REFS


proxy_for = proxy_for_ref


__all__ = [
    "DEFAULT_PROXY_DIMENSIONS",
    "STANDARD_PROXY_REFS",
    "ProxyGeometryError",
    "available_proxy_refs",
    "build_scene_proxies",
    "proxy_for",
    "proxy_for_node",
    "proxy_for_ref",
    "shared_usb_opening",
]
