"""Fail-closed STL endpoint contract tests."""

from __future__ import annotations

import sys
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch
from flask.testing import FlaskClient

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

from tests._cad_helpers import exact_six_case_part  # noqa: E402

_CASE_PATH = "examples/6-button-gamepad/case.py"


class _Solid:
    def __init__(self, volume: float) -> None:
        self.volume = volume


class _Half:
    def __init__(self, volumes: tuple[float, ...]) -> None:
        self._solids = [_Solid(volume) for volume in volumes]

    def solids(self) -> list[_Solid]:
        return self._solids


@pytest.fixture
def prototype_client(
    monkeypatch: MonkeyPatch,
) -> Generator[tuple[FlaskClient, Any, Any], None, None]:
    from cadkit.assembly import build_demo_assembly, placements_from_assembly
    from cadkit.case_execution import CaseArtifacts

    from tools.editor import server

    scene = build_demo_assembly()
    artifacts = CaseArtifacts(
        part=exact_six_case_part(scene),
        assembly=scene.to_dict(),
        placements=placements_from_assembly(scene),
    )

    def case_artifacts(_path: Path) -> Any:
        return artifacts

    monkeypatch.setattr(server, "_case_artifacts", case_artifacts)

    app = server.create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client, artifacts, server


def _revision(client: FlaskClient) -> str:
    response = client.get("/manifest", query_string={"case_path": _CASE_PATH})
    assert response.status_code == 200
    return response.get_json()["editor_revision"]


def _install_split_and_export(
    monkeypatch: MonkeyPatch,
    top: tuple[float, ...] = (1.0,),
    bottom: tuple[float, ...] = (1.0,),
    data: bytes = b"solid prototype\nendsolid prototype\n",
) -> list[Any]:
    import cadkit

    selected: list[Any] = []

    def split(_part: Any) -> tuple[_Half, _Half]:
        return _Half(top), _Half(bottom)

    def orient(top_half: Any, bottom_half: Any) -> tuple[Any, Any]:
        return top_half, bottom_half

    monkeypatch.setattr(cadkit, "snap_fit_pair", split)
    monkeypatch.setattr(cadkit, "orient_case_halves_for_print", orient)

    def export(part: Any, path: Path) -> Path:
        selected.append(part)
        path.write_bytes(data)
        return path

    monkeypatch.setattr(cadkit.render, "export_stl_part", export)
    return selected


def test_prototype_stl_orients_selected_half_before_export(
    prototype_client: tuple[FlaskClient, Any, Any], monkeypatch: MonkeyPatch
) -> None:
    client, _artifacts, _server = prototype_client
    revision = _revision(client)
    import cadkit

    assembly_top = _Half((1.0,))
    assembly_bottom = _Half((1.0,))
    print_top = _Half((2.0,))
    print_bottom = _Half((2.0,))
    exported: list[Any] = []

    def split(_part: Any) -> tuple[_Half, _Half]:
        return assembly_top, assembly_bottom

    monkeypatch.setattr(cadkit, "snap_fit_pair", split)

    def orient(top: Any, bottom: Any) -> tuple[Any, Any]:
        assert top is assembly_top
        assert bottom is assembly_bottom
        return print_top, print_bottom

    monkeypatch.setattr(cadkit, "orient_case_halves_for_print", orient)

    def export(part: Any, path: Path) -> Path:
        exported.append(part)
        path.write_bytes(b"solid print-oriented\nendsolid print-oriented\n")
        return path

    monkeypatch.setattr(cadkit.render, "export_stl_part", export)

    response = client.get(
        "/stl/top",
        query_string={"case_path": _CASE_PATH, "revision": revision},
    )

    assert response.status_code == 200
    assert exported == [print_top]


def test_stl_downloads_both_halves_with_validation_headers(
    prototype_client: tuple[FlaskClient, Any, Any], monkeypatch: MonkeyPatch
) -> None:
    client, artifacts, _server = prototype_client
    selected = _install_split_and_export(monkeypatch)
    from cadkit import constraints
    from cadkit.constraints import ConstraintViolation

    warning = ConstraintViolation("print", "prototype_advisory", "warning", "advisory")
    calls: dict[str, Any] = {}

    def validate(part: Any, **kwargs: Any) -> list[ConstraintViolation]:
        calls["part"] = part
        calls.update(kwargs)
        return [warning]

    monkeypatch.setattr(constraints, "validate", validate)
    revision = _revision(client)

    for part_name in ("top", "bottom"):
        response = client.get(
            f"/stl/{part_name}",
            query_string={"case_path": _CASE_PATH, "revision": revision},
        )

        assert response.status_code == 200
        assert response.data == b"solid prototype\nendsolid prototype\n"
        assert response.mimetype == "model/stl"
        assert f"filename=case-{part_name}.stl" in response.headers["Content-Disposition"]
        assert response.headers["Cache-Control"] == "no-store, max-age=0"
        assert response.headers["Pragma"] == "no-cache"
        assert response.headers["X-HF-Editor-Revision"] == revision
        assert response.headers["X-HF-Artifact-Status"] == "validated"

    assert len(selected) == 2
    assert calls["part"] is artifacts.part
    assert calls["placements"] is artifacts.placements
    assert calls["require_export_verification"] is False
    assert calls["whole_case_geometry"] is True


def test_prototype_stl_rejects_missing_structured_placements(
    prototype_client: tuple[FlaskClient, Any, Any], monkeypatch: MonkeyPatch
) -> None:
    client, artifacts, server = prototype_client
    revision = _revision(client)

    def missing_placements(_path: Path) -> Any:
        return replace(artifacts, placements=None)

    monkeypatch.setattr(server, "_case_artifacts", missing_placements)

    response = client.get(
        "/stl/top",
        query_string={"case_path": _CASE_PATH, "revision": revision},
    )

    assert response.status_code == 400
    assert "structured placements artifact" in response.get_json()["error"]


def test_prototype_stl_rejects_placements_from_a_different_scene(
    prototype_client: tuple[FlaskClient, Any, Any], monkeypatch: MonkeyPatch
) -> None:
    client, artifacts, server = prototype_client
    revision = _revision(client)
    assert artifacts.placements is not None
    mismatched = replace(
        artifacts,
        placements=replace(
            artifacts.placements,
            case_length_mm=artifacts.placements.case_length_mm - 1.0,
        ),
    )

    def mismatched_artifacts(_path: Path) -> Any:
        return mismatched

    monkeypatch.setattr(server, "_case_artifacts", mismatched_artifacts)

    response = client.get(
        "/stl/top",
        query_string={"case_path": _CASE_PATH, "revision": revision},
    )

    assert response.status_code == 400
    assert "derived exactly" in response.get_json()["error"]


@pytest.mark.parametrize("analysis", [None, [object()]])
def test_prototype_stl_rejects_missing_or_malformed_validation_analysis(
    prototype_client: tuple[FlaskClient, Any, Any],
    monkeypatch: MonkeyPatch,
    analysis: Any,
) -> None:
    client, _artifacts, _server = prototype_client
    revision = _revision(client)
    from cadkit import constraints

    def validate(*_args: Any, **_kwargs: Any) -> Any:
        return analysis

    monkeypatch.setattr(constraints, "validate", validate)
    response = client.get(
        "/stl/top",
        query_string={"case_path": _CASE_PATH, "revision": revision},
    )

    assert response.status_code == 400
    assert "validation analysis is missing or malformed" in response.get_json()["error"]


def test_prototype_stl_rejects_fatal_validation_before_split_or_export(
    prototype_client: tuple[FlaskClient, Any, Any], monkeypatch: MonkeyPatch
) -> None:
    client, _artifacts, _server = prototype_client
    revision = _revision(client)
    from cadkit import constraints
    from cadkit.constraints import ConstraintViolation

    fatal = ConstraintViolation("print", "unsafe", "error", "unsafe")

    def validate(*_args: Any, **_kwargs: Any) -> list[ConstraintViolation]:
        return [fatal]

    monkeypatch.setattr(constraints, "validate", validate)
    selected = _install_split_and_export(monkeypatch)

    response = client.get(
        "/stl/top",
        query_string={"case_path": _CASE_PATH, "revision": revision},
    )

    assert response.status_code == 400
    assert "fatal constraint violations" in response.get_json()["error"]
    assert selected == []


@pytest.mark.parametrize("rule", ["keepout_overlap", "route_intersects_volume"])
def test_prototype_stl_allows_only_known_unmeasured_placement_errors(
    prototype_client: tuple[FlaskClient, Any, Any], monkeypatch: MonkeyPatch, rule: str
) -> None:
    client, _artifacts, _server = prototype_client
    revision = _revision(client)
    from cadkit import constraints
    from cadkit.constraints import ConstraintViolation

    provisional_collision = ConstraintViolation("print", rule, "error", "provisional overlap")

    def validate(*_args: Any, **_kwargs: Any) -> list[ConstraintViolation]:
        return [provisional_collision]

    monkeypatch.setattr(constraints, "validate", validate)
    selected = _install_split_and_export(monkeypatch)

    response = client.get(
        "/stl/top",
        query_string={"case_path": _CASE_PATH, "revision": revision},
    )

    assert response.status_code == 200
    assert response.headers["X-HF-Artifact-Status"] == "validated"
    assert len(selected) == 1


@pytest.mark.parametrize(
    ("top", "message"),
    [
        ((), "exactly one solid"),
        ((1.0, 2.0), "exactly one solid"),
        ((0.0,), "positive finite volume"),
        ((-1.0,), "positive finite volume"),
        ((float("nan"),), "positive finite volume"),
        ((float("inf"),), "positive finite volume"),
    ],
)
def test_prototype_stl_rejects_invalid_snap_fit_halves(
    prototype_client: tuple[FlaskClient, Any, Any],
    monkeypatch: MonkeyPatch,
    top: tuple[float, ...],
    message: str,
) -> None:
    client, _artifacts, _server = prototype_client
    revision = _revision(client)
    _install_split_and_export(monkeypatch, top=top)

    response = client.get(
        "/stl/top",
        query_string={"case_path": _CASE_PATH, "revision": revision},
    )

    assert response.status_code == 400
    assert message in response.get_json()["error"]


def test_prototype_stl_rejects_empty_export_output(
    prototype_client: tuple[FlaskClient, Any, Any], monkeypatch: MonkeyPatch
) -> None:
    client, _artifacts, _server = prototype_client
    revision = _revision(client)
    _install_split_and_export(monkeypatch, data=b"")

    response = client.get(
        "/stl/bottom",
        query_string={"case_path": _CASE_PATH, "revision": revision},
    )

    assert response.status_code == 400
    assert "empty STL output" in response.get_json()["error"]


def test_prototype_stl_rejects_stale_revision_without_exporting(
    prototype_client: tuple[FlaskClient, Any, Any], monkeypatch: MonkeyPatch
) -> None:
    client, _artifacts, _server = prototype_client
    selected = _install_split_and_export(monkeypatch)

    response = client.get(
        "/stl/top",
        query_string={"case_path": _CASE_PATH, "revision": "0" * 64},
    )

    assert response.status_code == 409
    assert response.get_json()["status"] == "stale_revision"
    assert selected == []
