"""Behavioral tests for deterministic Build123d component proxies."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from build123d import Part

_SESSION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SESSION / "tools"))

from cadkit.assembly import build_demo_assembly  # noqa: E402
from cadkit.component_proxies import (  # noqa: E402
    STANDARD_PROXY_REFS,
    ProxyGeometryError,
    build_scene_proxies,
    proxy_for_ref,
)

from tests._pytest_helpers import approx  # noqa: E402


def _size(part: Part) -> tuple[float, float, float]:
    box = part.bounding_box()
    return (box.max.X - box.min.X, box.max.Y - box.min.Y, box.max.Z - box.min.Z)


def test_every_standard_proxy_has_deterministic_nonzero_geometry():
    first = {ref: _size(proxy_for_ref(ref)) for ref in STANDARD_PROXY_REFS}
    second = {ref: _size(proxy_for_ref(ref)) for ref in STANDARD_PROXY_REFS}

    assert first == second
    assert all(all(value > 0 for value in size) for size in first.values())
    assert _size(proxy_for_ref("feather-pid4516-3d")) == approx((50.8, 22.86, 7.2))


def test_scene_proxy_builder_places_all_standard_nodes_in_case_frame():
    scene = build_demo_assembly()
    proxies = build_scene_proxies(scene)

    assert {node.id for node in scene.nodes} <= proxies.keys()
    assert _size(proxies["electronics.breadboard"]) == approx((85.0, 55.0, 20.0))
    assert _size(proxies["feather"]) == approx((50.8, 22.86, 7.2))
    assert proxies["usb.connector"].bounding_box().max.Z > 17.0


def test_unknown_proxy_reference_is_not_silently_invented():
    with pytest.raises(ProxyGeometryError, match="no deterministic proxy"):
        proxy_for_ref("unregistered-model")
