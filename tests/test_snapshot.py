"""Snapshot pipeline tests — Slice 8.

Two things to verify:

1. **Server-side compositing works** — a base PNG + an SVG overlay produces
   a PNG with the overlay's pixels visible at the expected coordinates.
   This is the critical path: if the WebGL canvas capture doesn't include
   the SVG overlay, the agent sees a clean render with no annotations and
   'improves' nothing.

2. **Server-side tessellation works** — a Build123d Part round-trips
   through yacv-server's tessellate() and produces non-empty glTF bytes.

3. **The /snapshot endpoint round-trips** — POSTing an SVG overlay returns
   a PNG whose pixels match the overlay.
"""

from __future__ import annotations

import ast
import base64
import builtins
import json
import shutil
import stat
import struct
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest
from _pytest.monkeypatch import MonkeyPatch
from flask.testing import FlaskClient

# Add the session's tools/ dir so `cadkit` resolves when this module is
# executed from anywhere.
_here = Path(__file__).resolve().parent
_session = _here.parent
sys.path.insert(0, str(_session / "tools"))


from tests._cad_helpers import exact_six_case_part  # noqa: E402
from tests._pytest_helpers import approx  # noqa: E402
from tools.editor.snapshot import (  # noqa: E402
    composite_snapshot,
    make_test_base_png,
    make_test_overlay,
    overlay_pixel_in_png,
    tessellate_part,
)


def _temporary_example_case(tmp_path: Path) -> Path:
    source = _session / "examples" / "6-button-gamepad" / "case.py"
    target = tmp_path / "case.py"
    target.write_text(source.read_text())
    return target


def _work_dir_callback(root: Path) -> Callable[[Path, str], Path]:
    def resolve(_case_path: Path, submit_id: str) -> Path:
        return root / submit_id

    return resolve


def _fixed_work_dir(path: Path) -> Callable[[Path, str], Path]:
    def resolve(_case_path: Path, _submit_id: str) -> Path:
        return path

    return resolve


# ---------------------------------------------------------------------------
# Pure-Python compositing tests (no Flask, no YACV)
# ---------------------------------------------------------------------------
def test_composite_snapshot_is_non_empty() -> None:
    base = make_test_base_png(800, 600)
    overlay = make_test_overlay(800, 600)
    out = composite_snapshot(base, overlay, 800, 600)
    assert len(out) > 1000, "composited PNG is suspiciously small"
    # The PNG should be parseable.
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(out))
    assert img.size == (800, 600)
    assert img.mode == "RGB"


def test_composite_snapshot_preserves_overlay_pixels() -> None:
    """The red rect at (100..300, 100..300) and blue circle at (500, 300)
    must show up in the output."""
    base = make_test_base_png(800, 600)
    overlay = make_test_overlay(800, 600)
    out = composite_snapshot(base, overlay, 800, 600)

    # Inside the rect — should be red-dominant.
    r, g, b = overlay_pixel_in_png(out, 200, 200)
    assert r > 200, f"red channel too low at (200, 200): got {r}"
    assert g < 50, f"green channel too high at (200, 200): got {g}"

    # Background — should be white.
    r, g, b = overlay_pixel_in_png(out, 700, 50)
    assert r > 240 and g > 240 and b > 240, f"background not white at (700, 50): got {(r, g, b)}"

    # Inside the circle — should be blue-dominant.
    r, g, b = overlay_pixel_in_png(out, 500, 300)
    assert b > 100, f"blue channel too low at (500, 300): got {b}"


@pytest.mark.parametrize(
    "overlay",
    [
        "<svg><script>alert(1)</script></svg>",
        "<svg><foreignObject><div>bad</div></foreignObject></svg>",
        "<svg><style>@import url(https://example.invalid/a.css)</style></svg>",
        "<svg><style>.x { fill: url(file://etc/passwd) }</style></svg>",
        "<svg><image href='https://example.invalid/a.png'/></svg>",
        "<!DOCTYPE svg [<!ENTITY x 'bad'>]><svg>&x;</svg>",
        "<?evil><svg/>",
        "<svg width='999999px' height='1px'/>",
    ],
)
def test_composite_snapshot_rejects_unsafe_or_pathological_svg(overlay: str) -> None:
    with pytest.raises(ValueError):
        composite_snapshot(make_test_base_png(10, 10), overlay, 10, 10)


def test_composite_snapshot_rejects_oversized_overlay() -> None:
    overlay = f"<svg>{'x' * (256 * 1024)}</svg>"

    with pytest.raises(ValueError, match="payload limit"):
        composite_snapshot(make_test_base_png(10, 10), overlay, 10, 10)


def test_composite_snapshot_renders_benign_shape_and_text() -> None:
    overlay = """
    <svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">
      <rect x="10" y="10" width="40" height="40" fill="red"/>
      <text x="70" y="40" fill="blue" font-size="20">label</text>
    </svg>
    """

    out = composite_snapshot(make_test_base_png(200, 100), overlay, 200, 100)

    assert overlay_pixel_in_png(out, 20, 20)[0] > 200

    import io

    from PIL import Image

    text_region = Image.open(io.BytesIO(out)).convert("RGB").crop((65, 15, 140, 50))
    pixels = text_region.tobytes()
    assert any(
        blue > 150 and blue > red + 50 and blue > green + 50
        for red, green, blue in zip(pixels[0::3], pixels[1::3], pixels[2::3], strict=True)
    ), "expected blue text pixels in the bounded label region"


# ---------------------------------------------------------------------------
# Tessellation (round-trip a Build123d Part through yacv-server)
# ---------------------------------------------------------------------------
def _make_box() -> Any:
    from build123d import Align, Box, BuildPart

    with BuildPart() as bp:
        Box(50, 50, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return bp.part


def test_tessellate_part_produces_nonempty_gltf() -> None:
    part = _make_box()
    glb = tessellate_part(part)
    assert isinstance(glb, bytes)
    assert len(glb) > 1000, "glTF binary too small — tessellator may have failed silently"
    # glTF binary starts with "glTF" magic (4 bytes), then a version, then length.
    assert glb[:4] == b"glTF", f"missing glTF magic; got {glb[:4]!r}"


def test_yacv_http_method_fallback_is_scoped_to_import(monkeypatch: MonkeyPatch) -> None:
    """Python 3.10 gets HTTPMethod only while YACV is being imported."""
    import http
    from enum import Enum

    from tools.editor import snapshot

    monkeypatch.delattr(http, "HTTPMethod", raising=False)
    real_import = builtins.__import__
    imported_method: dict[str, Any] = {}

    def observe_yacv_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yacv_server.tessellate":
            imported_method["value"] = getattr(http, "HTTPMethod", None)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", observe_yacv_import)
    snapshot._import_yacv_tessellate()  # pyright: ignore[reportPrivateUsage]

    method: Any = imported_method["value"]
    assert method is not None
    head = method.HEAD
    assert issubclass(method, str)
    assert issubclass(method, Enum)
    assert head.value == "HEAD"
    assert not hasattr(http, "HTTPMethod")


# ---------------------------------------------------------------------------
# Flask endpoint round-trip
# ---------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch: MonkeyPatch) -> Iterator[FlaskClient]:
    """A Flask test client for the editor server."""
    # Import lazily so the server module's yacv_server auto-start is contained.
    from cadkit.assembly import build_demo_assembly, placements_from_assembly
    from cadkit.case_execution import CaseArtifacts

    from tools.editor import server
    from tools.editor.server import create_app

    def offline_artifacts(path: Path) -> CaseArtifacts:
        source = path.read_text() if path.is_file() else ""
        scene = build_demo_assembly()
        if "ASSEMBLY_SPEC" in source:
            tree = ast.parse(source)
            controls = next(
                statement.value
                for statement in tree.body
                if (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id == "CONTROLS"
                )
                or (
                    isinstance(statement, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "CONTROLS"
                        for target in statement.targets
                    )
                )
            )
            assert controls is not None
            scene = build_demo_assembly(buttons=ast.literal_eval(controls))
        part = exact_six_case_part(scene)
        return CaseArtifacts(
            part=part,
            assembly=scene.to_dict() if "ASSEMBLY_SPEC" in source else None,
            placements=placements_from_assembly(scene),
        )

    monkeypatch.setattr(server, "_case_artifacts", offline_artifacts)
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def sandboxed(monkeypatch: MonkeyPatch) -> None:
    """Enable endpoint tests that exercise post-validation behavior offline."""
    from cadkit import case_execution
    from cadkit.case_execution import SandboxStatus

    monkeypatch.setattr(
        case_execution,
        "require_sandbox",
        lambda: SandboxStatus(True, "test-fixture", "offline canonical artifact fixture"),
    )


def test_health_endpoint(client: FlaskClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert set(body["sandbox"]) == {"available", "backend", "reason"}


def test_snapshot_endpoint_with_synthetic_box(client: FlaskClient) -> None:
    """No Part supplied — server uses a synthetic box. The composite pipeline
    still produces a non-empty PNG."""
    resp = client.post(
        "/snapshot",
        json={
            "overlay": make_test_overlay(800, 600),
            "width": 800,
            "height": 600,
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "glb_b64" in body
    assert "snapshot_b64" in body
    glb = base64.b64decode(body["glb_b64"])
    snapshot = base64.b64decode(body["snapshot_b64"])
    assert glb[:4] == b"glTF"
    # The composited snapshot must be larger than the empty base (~3 KB)
    # because the overlay added pixels.
    assert len(snapshot) > 2000


def test_snapshot_endpoint_with_inline_case_cannot_create_marker(
    client: FlaskClient, tmp_path: Path
) -> None:
    """Inline Build123d Python is rejected before any source is run."""
    marker = tmp_path / "inline-marker"
    case_source = f"""
from pathlib import Path
Path({str(marker)!r}).touch()
from build123d import Box, BuildPart, Align
with BuildPart() as bp:
    Box(60, 40, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
"""
    resp = client.post(
        "/snapshot",
        json={
            "case": case_source,
            "overlay": make_test_overlay(800, 600),
            "width": 800,
            "height": 600,
        },
    )
    assert resp.status_code == 400
    assert "inline case source" in resp.get_json()["error"]
    assert not marker.exists()


def test_snapshot_inline_case_with_assembly_spec_is_rejected(client: FlaskClient) -> None:
    case_source = "ASSEMBLY_SPEC = object()\n"
    resp = client.post(
        "/snapshot",
        json={
            "case": case_source,
            "overlay": "<svg xmlns='http://www.w3.org/2000/svg'/>",
            "width": 800,
            "height": 600,
        },
    )

    assert resp.status_code == 400
    assert "inline case source" in resp.get_json()["error"]


def test_snapshot_worked_case_returns_complete_assembled_scene(client: FlaskClient) -> None:
    """The canonical worked case must not regress to a shell-only GLB."""
    resp = client.post(
        "/snapshot",
        json={
            "part_path": "examples/6-button-gamepad/case.py",
            "overlay": "<svg xmlns='http://www.w3.org/2000/svg'/>",
            "width": 800,
            "height": 600,
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["render_mode"] == "assembled"
    assert body["manifest"]["editor_revision"]
    assert body["manifest"]["assembly"] == body["assembly"]
    assert any(node["id"] == "electronics.breadboard" for node in body["assembly"]["nodes"])
    glb = base64.b64decode(body["glb_b64"])
    assert glb[:4] == b"glTF"
    assert len(glb) < 8 * 1024 * 1024

    json_length, json_type = struct.unpack_from("<II", glb, 12)
    assert json_type == 0x4E4F534A
    document = cast(dict[str, Any], json.loads(glb[20 : 20 + json_length].decode("utf-8")))
    names = {node["name"] for node in document["nodes"]}
    expected = {
        "case.shell",
        "case.bottom",
        "case.top",
        "electronics.breadboard",
        "control.up",
        "control.down",
        "control.right",
        "control.left",
        "control.action_a",
        "control.action_b",
        "feather",
        "feather.header_12",
        "feather.header_16",
        "usb.connector",
        "usb.opening",
        *(
            f"wire.control_{control_id}"
            for control_id in ("up", "down", "right", "left", "action_a", "action_b")
        ),
        *(
            f"wire.control_ground_{control_id}"
            for control_id in ("up", "down", "right", "left", "action_a", "action_b")
        ),
    }
    assert expected <= names
    triangles = sum(
        accessor["count"] // 3
        for mesh in document["meshes"]
        for primitive in mesh["primitives"]
        for accessor in [document["accessors"][primitive["indices"]]]
    )
    assert triangles < 250_000


def test_snapshot_endpoint_falls_back_when_cairo_native_library_is_missing(
    client: FlaskClient, monkeypatch: MonkeyPatch
) -> None:
    """An installed cairosvg package may still fail to load native Cairo."""
    real_import = builtins.__import__

    def import_without_native_cairo(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "cairosvg":
            raise OSError("native Cairo library is unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_native_cairo)
    from cadkit.case_execution import SandboxUnavailable, sandbox_status

    from tools.editor import server

    if not sandbox_status().available:

        def unavailable(_path: Path) -> Any:
            raise SandboxUnavailable("sandbox_unavailable: test host has no sandbox")

        monkeypatch.setattr(server, "_case_artifacts", unavailable)

    resp = client.post(
        "/snapshot",
        json={
            "overlay": ("<svg xmlns='http://www.w3.org/2000/svg' width='800' height='600'></svg>"),
            "part_path": "examples/6-button-gamepad/case.py",
            "width": 800,
            "height": 600,
        },
    )

    if not sandbox_status().available:
        assert resp.status_code == 503
        assert resp.get_json()["status"] == "sandbox_unavailable"
    else:
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert base64.b64decode(resp.get_json()["glb_b64"])[:4] == b"glTF"


def test_manifest_and_snapshot_case_execution_is_serialized(
    client: FlaskClient, monkeypatch: MonkeyPatch
) -> None:
    """Manifest extraction must not expose its temporary CAD stubs to snapshot."""
    import build123d
    import cadkit
    import cadkit.manifest
    from cadkit.case_execution import sandbox_status

    if not sandbox_status().available:
        pytest.skip("OS sandbox backend unavailable")

    manifest_entered = threading.Event()
    snapshot_finished = threading.Event()
    manifest_responses: list[Any] = []
    real_gamepad_body = cadkit.gamepad_body
    real_add: Any = build123d.add  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]

    def manifest_with_exposed_stub(
        _path: Path, _assembly: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        def stub_gamepad_body(**_kwargs: Any) -> str:
            return "<stubbed-gamepad-body>"

        def stub_add(*_args: Any, **_kwargs: Any) -> str:
            return "<stubbed-add>"

        cadkit.gamepad_body = stub_gamepad_body
        build123d.add = stub_add  # temporary module stub for this race test.
        manifest_entered.set()
        try:
            snapshot_finished.wait(timeout=0.25)
            return {"elements": [], "coordinate_system": "test", "units": "mm"}
        finally:
            cadkit.gamepad_body = real_gamepad_body
            build123d.add = real_add  # restore the temporary test stub.

    monkeypatch.setattr(cadkit.manifest, "from_case_path", manifest_with_exposed_stub)

    def get_manifest() -> None:
        with client.application.test_client() as manifest_client:
            manifest_responses.append(
                manifest_client.get(
                    "/manifest",
                    query_string={"case_path": "examples/6-button-gamepad/case.py"},
                )
            )

    manifest_thread = threading.Thread(target=get_manifest)
    manifest_thread.start()
    assert manifest_entered.wait(timeout=2.0)
    try:
        resp = client.post(
            "/snapshot",
            json={
                "overlay": (
                    "<svg xmlns='http://www.w3.org/2000/svg' width='800' height='600'></svg>"
                ),
                "part_path": "examples/6-button-gamepad/case.py",
                "width": 800,
                "height": 600,
            },
        )
    finally:
        snapshot_finished.set()
        manifest_thread.join(timeout=2.0)

    assert not manifest_thread.is_alive()
    assert manifest_responses[0].status_code == 200
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert base64.b64decode(resp.get_json()["glb_b64"])[:4] == b"glTF"


def test_snapshot_endpoint_rejects_missing_overlay(client: FlaskClient) -> None:
    resp = client.post("/snapshot", json={"width": 100, "height": 100})
    assert resp.status_code == 400
    assert "overlay" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# /manifest endpoint (Slice 9)
# ---------------------------------------------------------------------------
def test_manifest_endpoint_returns_elements(client: FlaskClient) -> None:
    resp = client.get("/manifest", query_string={"case_path": "examples/6-button-gamepad/case.py"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "elements" in body
    elements = body["elements"]
    # Three shell nodes + six controls + breadboard/board/connectors.
    assert len(elements) == 21
    kinds = {e["kind"] for e in elements}
    assert kinds == {
        "case_shell",
        "case_bottom",
        "case_top",
        "pbs33b_button",
        "guuzi_action_button",
        "half_size_breadboard",
        "feather_board",
        "pid2830_header_row",
        "dupont_connector_row",
        "harness_strain_relief",
        "harness_no_pinch_envelope",
        "micro_usb_connector",
        "rear_usb_opening",
    }
    control_ids = sorted(e["id"] for e in elements if e["id"].startswith("control."))
    assert control_ids == [
        "control.action_a",
        "control.action_b",
        "control.down",
        "control.left",
        "control.right",
        "control.up",
    ]
    elements_by_id = {element["id"]: element for element in elements}
    assert elements_by_id["control.up"]["mobility"] == "movable"
    assert elements_by_id["control.action_a"]["component_id"] == "guuzi_b09bmrdptn_action"
    assert elements_by_id["control.action_b"]["component_id"] == "guuzi_b09bmrdptn_action"
    feather = elements_by_id["feather"]
    assert feather["position"][2] == approx(5.6)


def test_manifest_endpoint_loads_while_case_execution_is_initializing() -> None:
    """The public manifest route must not import the case runtime's partial state."""
    probe = """
import importlib.machinery
import sys
import types

from tools.editor import server
import cadkit
from cadkit.assembly import build_demo_assembly
from cadkit.parametric import gamepad_body

scene = build_demo_assembly()
server._case_artifacts = lambda _path: types.SimpleNamespace(
    assembly=scene.to_dict(),
    part=gamepad_body(),
)
sys.modules.pop("cadkit.manifest", None)
cadkit.__dict__.pop("manifest", None)
partial = types.ModuleType("cadkit.case_execution")
partial.__spec__ = importlib.machinery.ModuleSpec("cadkit.case_execution", loader=None)
partial.__spec__._initializing = True
assert not hasattr(partial, "MAX_SOURCE_BYTES")
sys.modules["cadkit.case_execution"] = partial

response = server.create_app().test_client().get(
    "/manifest?case_path=examples%2F6-button-gamepad%2Fcase.py"
)
assert response.status_code == 200, response.get_data(as_text=True)
body = response.get_json()
assert len(body["elements"]) == 21
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        cwd=_session,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_manifest_metadata_describes_mobility_and_case_local_visuals(
    client: FlaskClient,
) -> None:
    response = client.get(
        "/manifest", query_string={"case_path": "examples/6-button-gamepad/case.py"}
    )
    body = response.get_json()
    assert body["layout"] == {
        "coordinate_system": "case_local_xy",
        "origin": "lower_left",
        "scale": "uniform_mm",
        "dimensions_source": "assembly_spec",
    }
    elements = {element["id"]: element for element in body["elements"]}
    assert elements["case.shell"]["physical_dimensions"] == {
        "x_mm": 130.0,
        "y_mm": 90.0,
        "z_mm": 65.0,
    }
    assert elements["case.shell"]["mobility"] == "fixed"
    assert elements["case.shell"]["visual"]["shape"] == "rounded-rectangle"
    assert elements["case.shell"]["visual"]["width_mm"] == 130.0
    assert elements["case.shell"]["visual"]["height_mm"] == 90.0
    assert all(
        elements[f"control.{control_id}"]["mobility"] == "movable"
        for control_id in ("up", "down", "right", "left", "action_a", "action_b")
    )
    assert elements["control.up"]["visual"] == {"shape": "circle", "diameter_mm": 12.4}
    assert elements["feather"]["mobility"] == "fixed"
    assert elements["feather"]["visual"]["shape"] == "feather-board"
    assert elements["feather"]["visual"]["width_mm"] == 50.8
    assert elements["feather"]["visual"]["height_mm"] == 22.86
    assert len(elements["feather"]["visual"]["mounting_holes_mm"]) == 4
    assert {header["count"] for header in elements["feather"]["visual"]["headers"]} == {12, 16}
    assert elements["usb.connector"]["mobility"] == "fixed"
    assert elements["usb.opening"]["visual"]["shape"] == "usb-slot"
    assert body["assembly"]["schema_version"] == "1.0"
    assert body["assembly"]["coordinate_system"] == "case_local_lower_left_xyz"
    assert body["provisional"] is True


def test_manifest_endpoint_rejects_missing_path(client: FlaskClient) -> None:
    resp = client.get("/manifest")
    assert resp.status_code == 400
    assert "case_path" in resp.get_json()["error"]


def test_manifest_endpoint_404_on_missing_file(client: FlaskClient) -> None:
    resp = client.get("/manifest", query_string={"case_path": "examples/does-not-exist/case.py"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /submit endpoint (Slice 9)
# ---------------------------------------------------------------------------
def test_submit_endpoint_rejects_candidate_that_ignores_accepted_move(
    client: FlaskClient, sandboxed: None, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A candidate that ignores an accepted move is rejected transactionally.

    We mock the orchestrator so the test doesn't invoke opencode.

    NOTE: server.py imports the orchestrator as `from agent import loop
    as agent_loop` (bare path because tools/ is on sys.path), so the
    mock MUST use the same bare path — `agent.loop` and
    `tools.agent.loop` resolve to two DIFFERENT module objects in
    sys.modules.
    """
    from tools.editor import server

    real_case = (
        Path(server.__file__).resolve().parents[2] / "examples" / "6-button-gamepad" / "case.py"
    )
    original_source = real_case.read_text()
    monkeypatch.setattr(
        server,
        "_work_dir_for",
        _work_dir_callback(tmp_path),
    )
    import agent.loop

    def _successful(case_path: Path, submit_dir: Path, **kw: Any) -> dict[str, Any]:
        output_path = Path(kw["output_path"])
        # Deliberately ignore the private editor base to exercise the
        # transactional rejected-candidate path.
        output_path.write_text(original_source)
        return {"status": "ok", "iterations": 1, "case_path": str(output_path), "violations": []}

    monkeypatch.setattr(
        agent.loop,
        "iterate_with_visual_feedback",
        _successful,
    )

    resp = client.post(
        "/submit",
        json={
            "case_path": "examples/6-button-gamepad/case.py",
            "feedback": {
                "moves": [
                    {
                        "element_id": "control.up",
                        "expected_position": [35.0, 60.0],
                        "new_position": [34.9, 60],
                    }
                ],
                "annotations": [{"id": 1, "kind": "text", "text": "smaller"}],
                "notes": ["Round the corners more"],
            },
            "snapshot_b64": "",  # no snapshot
        },
    )
    body = resp.get_json()
    assert body["status"] == "vision_failed"
    assert "ignored accepted move" in body["error"]
    assert real_case.read_text() == original_source


def test_submit_endpoint_rejects_llm_control_move_without_validated_drag(
    client: FlaskClient, sandboxed: None, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from tools.editor import server

    real_case = (
        Path(server.__file__).resolve().parents[2] / "examples" / "6-button-gamepad" / "case.py"
    )
    original_source = real_case.read_text()
    work_dir = real_case.parent / "submits" / tmp_path.name
    monkeypatch.setattr(server, "_work_dir_for", _fixed_work_dir(work_dir))
    import agent.loop

    def _moves_without_drag(case_path: Path, submit_dir: Path, **kw: Any) -> dict[str, Any]:
        del submit_dir
        output_path = Path(kw["output_path"])
        output_path.write_text(Path(case_path).read_text().replace('"x": 35.0,', '"x": 34.9,', 1))
        return {"status": "ok", "iterations": 1, "case_path": str(output_path), "violations": []}

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", _moves_without_drag)
    try:
        response = client.post(
            "/submit",
            json={
                "case_path": "examples/6-button-gamepad/case.py",
                "feedback": {"prompt": "Round the outer case corners.", "moves": []},
            },
        )
        body = response.get_json()
        assert response.status_code == 500
        assert body["status"] == "vision_failed"
        assert "control.up changed without a validated 2D drag" in body["error"]
        assert real_case.read_text() == original_source
    finally:
        real_case.write_text(original_source)
        shutil.rmtree(work_dir, ignore_errors=True)


def test_candidate_placements_must_match_the_canonical_scene() -> None:
    from dataclasses import replace

    from cadkit.assembly import build_demo_assembly, placements_from_assembly

    from tools.editor import server

    scene = build_demo_assembly()
    placements = placements_from_assembly(scene)
    mismatched = replace(placements, case_length_mm=placements.case_length_mm - 1.0)
    placements_match_error = server.__dict__["_placements_match_error"]

    assert placements_match_error(scene, placements) is None
    assert (
        placements_match_error(scene, mismatched)
        == "candidate PLACEMENTS must be derived exactly from candidate ASSEMBLY_SPEC"
    )


def test_candidate_source_must_keep_case_fillet_scene_synchronized(tmp_path: Path) -> None:
    from tools.editor import server

    source_path = Path(server.__file__).resolve().parents[2] / "examples/6-button-gamepad/case.py"
    candidate = tmp_path / "case.py"
    source = source_path.read_text()
    candidate.write_text(source)
    source_contract_error = server.__dict__["_source_contract_error"]

    assert source_contract_error(candidate) is None

    candidate.write_text(source.replace("    case_fillet_radius_mm=FILLET_RADIUS_MM,\n", ""))
    assert source_contract_error(candidate) == (
        "candidate ASSEMBLY_SPEC must pass FILLET_RADIUS_MM as case_fillet_radius_mm"
    )

    candidate.write_text(
        source.replace("    case_fillet_radius_mm=FILLET_RADIUS_MM,\n", "")
        + "\ndef decoy_scene():\n"
        + "    return build_demo_assembly(case_fillet_radius_mm=FILLET_RADIUS_MM)\n"
    )
    assert source_contract_error(candidate) == (
        "candidate ASSEMBLY_SPEC must pass FILLET_RADIUS_MM as case_fillet_radius_mm"
    )

    candidate.write_text(source.replace("        fillet_radius_mm=FILLET_RADIUS_MM,\n", ""))
    assert source_contract_error(candidate) == (
        "candidate build_case must pass FILLET_RADIUS_MM as gamepad_body fillet_radius_mm"
    )


def test_candidate_source_cannot_parameterize_authoritative_snap_geometry(tmp_path: Path) -> None:
    from tools.editor import server

    source_path = Path(server.__file__).resolve().parents[2] / "examples/6-button-gamepad/case.py"
    candidate = tmp_path / "case.py"
    source = source_path.read_text()
    source_contract_error = server.__dict__["_source_contract_error"]

    for replacement in (
        "snap_fit_pair(case_for_main, clip_width_mm=8.0)",
        "snap_fit_pair(case_for_main, tolerance_mm=0.35)",
        "snap_fit_pair(case)",
    ):
        candidate.write_text(source.replace("snap_fit_pair(case_for_main)", replacement))
        assert source_contract_error(candidate) == (
            "candidate source may not parameterize authoritative snap-fit geometry"
        )

    candidate.write_text(
        source.replace("snap_fit_pair(case_for_main)", "(case_for_main, case_for_main)")
    )
    assert source_contract_error(candidate) == (
        "candidate source must retain exactly one authoritative snap_fit_pair call"
    )

    candidate.write_text(
        source.replace(
            "    BuildPart,\n",
            "    Box,\n    BuildPart,\n",
        )
    )
    assert source_contract_error(candidate) == (
        "candidate source may not recreate snap geometry with raw build123d primitives"
    )

    candidate.write_text(
        source.replace(
            "    body = gamepad_body(\n",
            "    from build123d import Box\n\n    body = gamepad_body(\n",
        )
    )
    assert source_contract_error(candidate) == (
        "candidate source may not recreate snap geometry with raw build123d primitives"
    )

    candidate.write_text(
        source.replace(
            "def main() -> int:\n",
            "def snap_fit_pair(part):\n    return part, part\n\n\ndef main() -> int:\n",
        )
    )
    assert source_contract_error(candidate) == (
        "candidate source must retain the unshadowed cadkit snap_fit_pair import"
    )

    candidate.write_text(source + "\nfrom replacement import snap_fit_pair\n")
    assert source_contract_error(candidate) == (
        "candidate source must retain the unshadowed cadkit snap_fit_pair import"
    )

    candidate.write_text(source + "\nfrom replacement import *\n")
    assert source_contract_error(candidate) == (
        "candidate source must retain the unshadowed cadkit gamepad_body import"
    )

    candidate.write_text(source + '\nglobals()["snap_fit_pair"] = replacement\n')
    assert source_contract_error(candidate) == (
        "candidate source may not dynamically replace protected geometry bindings"
    )

    candidate.write_text(source + "\nsnap_fit_pair.__code__ = replacement.__code__\n")
    assert source_contract_error(candidate) == (
        "candidate source must retain the unshadowed cadkit snap_fit_pair import"
    )

    candidate.write_text(
        source.replace(
            "snap_fit_pair(case_for_main)",
            "replacement.snap_fit_pair(case_for_main)",
        )
    )
    assert source_contract_error(candidate) == (
        "candidate source must retain exactly one authoritative snap_fit_pair call"
    )

    candidate.write_text(
        source.replace(
            "    assembly_top, assembly_bottom = snap_fit_pair(case_for_main)\n",
            "    assembly_top, assembly_bottom = case_for_main, case_for_main\n",
        )
        + "\n\ndef unused_snap():\n    return snap_fit_pair(case_for_main)\n"
    )
    assert source_contract_error(candidate) == (
        "candidate source must apply snap_fit_pair to the exported shell halves"
    )

    candidate.write_text(
        source.replace(
            "    top, bottom = orient_case_halves_for_print(assembly_top, assembly_bottom)\n",
            "    top, bottom = assembly_top, assembly_bottom\n",
        )
    )
    assert source_contract_error(candidate) == (
        "candidate source must retain authoritative broad-face-down print orientation"
    )

    candidate.write_text(
        source.replace(
            "        fillet_radius_mm=FILLET_RADIUS_MM,\n",
            "        fillet_radius_mm=FILLET_RADIUS_MM,\n        wall_thickness_mm=3.0,\n",
        )
    )
    assert source_contract_error(candidate) == (
        "candidate source may not override the authoritative shell wall thickness"
    )

    candidate.write_text(
        source.replace(
            "def build_case() -> Part:\n",
            "def gamepad_body(**kwargs):\n    return kwargs\n\n\ndef build_case() -> Part:\n",
        )
    )
    assert source_contract_error(candidate) == (
        "candidate source must retain the unshadowed cadkit gamepad_body import"
    )


def test_candidate_cannot_fabricate_frozen_backbone_evidence() -> None:
    from dataclasses import replace

    from cadkit.assembly import build_demo_assembly

    from tools.editor import server

    scene = build_demo_assembly()
    assert scene.electronics_backbone is not None
    fabricated_backbone = replace(
        scene.electronics_backbone,
        status="FROZEN",
        outside_dimensions_mm=(100.0, 50.0, 10.0),
        stack_height_mm=18.0,
        placement_offset_mm=(22.5, 20.0, 0.0),
        usb_offset_mm=(0.0, 27.0, 12.15),
        insertion_depth_mm=8.5,
        adhesive_bond_interface={
            "surface": "inside_bottom_shell",
        },
        adhesive_bond_verified=True,
        signed_record_binding="invented-record.json",
    )
    candidate = replace(scene, electronics_backbone=fabricated_backbone)
    accepted_move_error = server.__dict__["_accepted_move_error"]

    assert (
        accepted_move_error(scene, candidate, [])
        == "candidate changed protected ElectronicsBackbone evidence"
    )


def test_submit_endpoint_accepts_candidate_that_applies_move_and_returns_exact_scene(
    client: FlaskClient, sandboxed: None, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from tools.editor import server

    real_case = (
        Path(server.__file__).resolve().parents[2] / "examples" / "6-button-gamepad" / "case.py"
    )
    original_source = real_case.read_text()
    work_dir = real_case.parent / "submits" / tmp_path.name
    monkeypatch.setattr(
        server,
        "_work_dir_for",
        _fixed_work_dir(work_dir),
    )
    import agent.loop

    def _successful(case_path: Path, submit_dir: Path, **kw: Any) -> dict[str, Any]:
        output_path = Path(kw["output_path"])
        # The orchestrator receives a private base after the explicit drag.
        output_path.write_text(Path(case_path).read_text())
        return {"status": "ok", "iterations": 1, "case_path": str(output_path), "violations": []}

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", _successful)
    try:
        resp = client.post(
            "/submit",
            json={
                "case_path": "examples/6-button-gamepad/case.py",
                "feedback": {
                    "moves": [
                        {
                            "element_id": "control.up",
                            "expected_position": [35.0, 60.0],
                            "new_position": [34.9, 60],
                        }
                    ]
                },
                "snapshot_b64": base64.b64encode(make_test_base_png(40, 30)).decode("ascii"),
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        button = next(node for node in body["assembly"]["nodes"] if node["id"] == "control.up")
        assert button["transform"]["position_mm"][:2] == [34.9, 60.0]
        manifest_button = next(
            item for item in body["manifest"]["elements"] if item["id"] == "control.up"
        )
        assert manifest_button["position"][:2] == [34.9, 60.0]
        glb = base64.b64decode(body["glb_b64"])
        json_length, _json_type = struct.unpack_from("<II", glb, 12)
        document = cast(dict[str, Any], json.loads(glb[20 : 20 + json_length].decode("utf-8")))
        glb_button = next(node for node in document["nodes"] if node["name"] == "control.up")
        assert glb_button["translation"][:2] == [34.9, 60.0]
    finally:
        real_case.write_text(original_source)
        shutil.rmtree(work_dir, ignore_errors=True)


def test_submit_applies_explicit_editor_move_before_typed_llm_move(
    client: FlaskClient, sandboxed: None, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The typed orchestrator receives a private base after the editor drag."""
    from tools.editor import server

    real_case = (
        Path(server.__file__).resolve().parents[2] / "examples" / "6-button-gamepad" / "case.py"
    )
    original_source = real_case.read_text()
    work_dir = real_case.parent / "submits" / tmp_path.name
    monkeypatch.setattr(server, "_work_dir_for", _fixed_work_dir(work_dir))
    import agent.loop

    def typed_orchestrator(case_path: Path, _submit_dir: Path, **kwargs: Any) -> dict[str, Any]:
        private_base = Path(case_path)
        assert private_base != real_case
        source = private_base.read_text()
        assert '"x": 34.9' in source
        candidate = Path(kwargs["output_path"])
        candidate.write_text(source.replace('"x": 105.0,', '"x": 104.0,', 1))
        return {
            "status": "ok",
            "iterations": 1,
            "violations": [],
            "llm_accepted_moves": [
                {"element_id": "control.action_a", "new_position": [104.0, 30.0]}
            ],
        }

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", typed_orchestrator)
    try:
        response = client.post(
            "/submit",
            json={
                "case_path": "examples/6-button-gamepad/case.py",
                "feedback": {
                    "moves": [
                        {
                            "element_id": "control.up",
                            "expected_position": [35.0, 60.0],
                            "new_position": [34.9, 60.0],
                        }
                    ]
                },
                "snapshot_b64": base64.b64encode(make_test_base_png(40, 30)).decode("ascii"),
            },
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        nodes = {node["id"]: node for node in body["assembly"]["nodes"]}
        assert nodes["control.up"]["transform"]["position_mm"][:2] == [34.9, 60.0]
        assert nodes["control.action_a"]["transform"]["position_mm"][:2] == [104.0, 30.0]
    finally:
        real_case.write_text(original_source)
        shutil.rmtree(work_dir, ignore_errors=True)


def test_submit_endpoint_writes_snapshot_png(
    client: FlaskClient, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from cadkit.case_execution import sandbox_status

    from tools.editor import server

    if not sandbox_status().available:
        pytest.skip("OS sandbox backend unavailable")

    case_path = _temporary_example_case(tmp_path)

    def resolve_case_path(_value: str) -> Path:
        return case_path

    monkeypatch.setattr(server, "_resolve_case_path", resolve_case_path)

    def work_dir_for(_case_path: str | Path, submit_id: str) -> Path:
        return tmp_path / submit_id

    monkeypatch.setattr(server, "_work_dir_for", work_dir_for)
    import agent.loop

    def _successful(case_path: Path, submit_dir: Path, **kw: Any) -> dict[str, Any]:
        output_path = Path(kw["output_path"])
        output_path.write_text(Path(case_path).read_text())
        return {"status": "ok", "iterations": 1, "case_path": str(output_path), "violations": []}

    monkeypatch.setattr(
        agent.loop,
        "iterate_with_visual_feedback",
        _successful,
    )
    import base64

    tiny_png_b64 = base64.b64encode(make_test_base_png(40, 30)).decode("ascii")

    resp = client.post(
        "/submit",
        json={
            "case_path": "examples/6-button-gamepad/case.py",
            "feedback": {"moves": [], "annotations": [], "notes": []},
            "snapshot_b64": tiny_png_b64,
        },
    )
    assert resp.status_code == 200
    work_dir = Path(resp.get_json()["work_dir"])
    assert (work_dir / "snapshot.png").exists()
    assert (work_dir / "snapshot.png").stat().st_size > 0
    assert stat.S_IMODE(work_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((work_dir / "snapshot.png").stat().st_mode) == 0o600


def test_submit_endpoint_reverts_on_orchestrator_failure(
    client: FlaskClient, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """If the orchestrator reports a non-ok status, /submit restores the
    `case.py` from the pre-submit backup so the next submit starts from
    the user's last known-good code."""
    from cadkit.case_execution import sandbox_status

    from tools.editor import server

    if not sandbox_status().available:
        pytest.skip("OS sandbox backend unavailable")

    real_case = tmp_path / "case.py"
    original_source = "case = 'original'\n"
    real_case.write_text(original_source)

    def resolve_case_path(_value: str) -> Path:
        return real_case

    def work_dir_for(_path: str, submit_id: str) -> Path:
        return tmp_path / submit_id

    monkeypatch.setattr(server, "_resolve_case_path", resolve_case_path)
    monkeypatch.setattr(server, "_work_dir_for", work_dir_for)
    import agent.loop

    def _failing(case_path: Path, _submit_dir: Path, **_kw: Any) -> dict[str, Any]:
        # Simulate the orchestrator writing bad code into a temporary case
        # before reporting failure — never mutate a tracked example.
        Path(case_path).write_text("# BROKEN BY ORCHESTRATOR\n")
        return {
            "status": "fatal_after_iterations",
            "iterations": 3,
            "case_path": str(case_path),
            "violations": [{"severity": "fatal", "message": "min wall"}],
        }

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", _failing)

    resp = client.post(
        "/submit",
        json={
            "case_path": "temporary/case.py",
            "feedback": {"moves": [], "annotations": [], "prompt": "do something bad"},
            "snapshot_b64": "",
        },
    )
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["status"] == "vision_failed"
    assert real_case.read_text() == original_source


def test_orchestrator_always_restores_source_when_candidate_also_exists(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import agent.loop

    from tools.editor import server

    case_path = tmp_path / "case.py"
    case_path.write_text("case = 'original'\n")
    work_dir = tmp_path / "submit"
    work_dir.mkdir()

    def _writes_both(source: Path, _submit_dir: Path, **kw: Any) -> dict[str, Any]:
        source.write_text("case = 'illicit direct write'\n")
        Path(kw["output_path"]).write_text("case = 'candidate'\n")
        return {"status": "fatal_after_iterations", "iterations": 1, "violations": []}

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", _writes_both)

    result, error = server._run_orchestrator(  # pyright: ignore[reportPrivateUsage]
        case_path, work_dir
    )

    assert error is None
    assert result is not None and result["status"] == "fatal_after_iterations"
    assert case_path.read_text() == "case = 'original'\n"
    assert (work_dir / "case.py").read_text() == "case = 'candidate'\n"


def test_resolve_case_path_blocks_path_traversal() -> None:
    """The case_path field is user-controllable; the resolver must
    refuse paths that escape the session root."""
    from tools.editor.server import _resolve_case_path  # pyright: ignore[reportPrivateUsage]

    # Absolute path outside the session — refused.
    assert _resolve_case_path("/etc/passwd") is None
    # Relative traversal — refused.
    assert _resolve_case_path("../../../../etc/passwd") is None
    # A real file inside the session resolves.
    real = _resolve_case_path("examples/6-button-gamepad/case.py")
    assert real is not None
    assert real.is_file()
    assert _resolve_case_path("examples/6-button-gamepad/__init__.py") is None


def test_submit_endpoint_rejects_missing_case_path(client: FlaskClient) -> None:
    resp = client.post("/submit", json={"feedback": {}})
    assert resp.status_code == 400


def test_submit_rejects_invalid_direct_move_before_creating_submit_workdir(
    client: FlaskClient, sandboxed: None, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from tools.editor import server

    target = tmp_path / "submit-work"
    monkeypatch.setattr(
        server,
        "_work_dir_for",
        _fixed_work_dir(target),
    )
    resp = client.post(
        "/submit",
        json={
            "case_path": "examples/6-button-gamepad/case.py",
            "feedback": {
                "moves": [
                    {
                        "element_id": "case.shell",
                        "expected_position": [65.0, 45.0],
                        "new_position": [1, 1],
                    }
                ]
            },
        },
    )

    assert resp.status_code == 400
    assert not target.exists()


def test_submit_scene_less_candidate_never_replaces_canonical_case(
    client: FlaskClient, sandboxed: None, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import agent.loop

    from tools.editor import server

    real_case = (
        Path(server.__file__).resolve().parents[2] / "examples" / "6-button-gamepad" / "case.py"
    )
    original = real_case.read_text()
    monkeypatch.setattr(
        server,
        "_work_dir_for",
        _work_dir_callback(tmp_path),
    )

    def _scene_less(case_path: Path, submit_dir: Path, **kw: Any) -> dict[str, Any]:
        Path(kw["output_path"]).write_text("case = object()\n")
        return {"status": "ok", "iterations": 1, "violations": []}

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", _scene_less)
    resp = client.post(
        "/submit",
        json={
            "case_path": "examples/6-button-gamepad/case.py",
            "feedback": {"moves": []},
        },
    )

    assert resp.status_code == 500
    assert "ASSEMBLY_SPEC" in resp.get_json()["error"]
    assert real_case.read_text() == original


def test_submit_render_failure_restores_original_and_cleans_candidate(
    client: FlaskClient, sandboxed: None, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import agent.loop

    from tools.editor import server

    real_case = (
        Path(server.__file__).resolve().parents[2] / "examples" / "6-button-gamepad" / "case.py"
    )
    original = real_case.read_text()
    submit_root = tmp_path / "submits"
    monkeypatch.setattr(
        server,
        "_work_dir_for",
        _work_dir_callback(submit_root),
    )

    def _successful(case_path: Path, submit_dir: Path, **kw: Any) -> dict[str, Any]:
        output_path = Path(kw["output_path"])
        output_path.write_text(Path(case_path).read_text())
        return {"status": "ok", "iterations": 1, "violations": []}

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", _successful)

    def _render_failure(*args: Any, **kwargs: Any) -> NoReturn:
        raise RuntimeError("render")

    monkeypatch.setattr(server, "_render_case_glb", _render_failure)
    resp = client.post(
        "/submit",
        json={
            "case_path": "examples/6-button-gamepad/case.py",
            "feedback": {"moves": []},
        },
    )

    assert resp.status_code == 500
    assert real_case.read_text() == original
    assert not list(real_case.parent.glob(f".{real_case.stem}.candidate-*.py"))


# ---------------------------------------------------------------------------
# GET / — frontend HTML (Slice 10)
# ---------------------------------------------------------------------------
def test_index_serves_html_with_toolbar(client: FlaskClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<model-viewer" in resp.data
    # Toolbar markers from Slice 10 frontend.
    assert b'data-tool="arrow"' in resp.data
    assert b'data-tool="circle"' in resp.data
    assert b'data-tool="text"' in resp.data
    assert b'data-tool="clear"' in resp.data
    # Send feedback button.
    assert b'id="send"' in resp.data
    # Marker-num class for Set-of-Mark prompting.
    assert b"marker-num" in resp.data
