"""Exact-six manifest contracts for the editor boundary."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

from cadkit.assembly import build_demo_assembly  # noqa: E402
from cadkit.manifest import (  # noqa: E402
    ManifestSourceError,
    _manifest_from_assembly,  # pyright: ignore[reportPrivateUsage]
)


def _append_rogue_control(payload: dict[str, Any]) -> None:
    payload["nodes"].append(
        {
            "id": "rogue",
            "mobility": "constrained_xy",
            "kind": "pbs33b_button",
        }
    )


def _forge_action_kind(payload: dict[str, Any]) -> None:
    next(node for node in payload["nodes"] if node["id"] == "control.action_a").update(
        {"kind": "pbs33b_button"}
    )


def _forge_up_role(payload: dict[str, Any]) -> None:
    next(node for node in payload["nodes"] if node["id"] == "control.up")["metadata"].update(
        {"control_role": "ACTION_A"}
    )


def test_manifest_preserves_control_identity_and_mobility() -> None:
    manifest = _manifest_from_assembly(build_demo_assembly().to_dict())

    controls = [
        element
        for element in manifest["elements"]
        if element["canonical_mobility"] == "constrained_xy"
    ]
    assert [element["id"] for element in controls] == [
        "control.up",
        "control.down",
        "control.right",
        "control.left",
        "control.action_a",
        "control.action_b",
    ]
    assert [element["control_role"] for element in controls] == [
        "UP",
        "DOWN",
        "RIGHT",
        "LEFT",
        "ACTION_A",
        "ACTION_B",
    ]
    assert controls[-1]["component_id"] == "guuzi_b09bmrdptn_action"
    assert controls[-1]["kind"] == "guuzi_action_button"
    assert all(element["mobility"] == "movable" for element in controls)


@pytest.mark.parametrize(
    "mutation",
    [
        _append_rogue_control,
        _forge_action_kind,
        _forge_up_role,
    ],
)
def test_manifest_rejects_unknown_or_forged_control_identity(
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    payload = build_demo_assembly().to_dict()
    mutation(payload)

    with pytest.raises(ManifestSourceError):
        _manifest_from_assembly(payload)
