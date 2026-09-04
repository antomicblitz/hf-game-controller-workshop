"""Focused runtime tests for the foreground editor and snapshot fallbacks."""

from __future__ import annotations

import socket
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from _pytest.monkeypatch import MonkeyPatch
from flask.testing import FlaskClient
from werkzeug.serving import BaseWSGIServer

from tools.editor import server, snapshot


def test_snapshot_fallback_rasterizer_draws_supported_primitives(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "cairosvg", None)

    def no_binary(_name: str) -> str | None:
        return None

    monkeypatch.setattr(snapshot.shutil, "which", no_binary)

    image = snapshot._render_svg(  # pyright: ignore[reportPrivateUsage]
        "<svg xmlns='http://www.w3.org/2000/svg'><rect x='2' y='2' width='4' height='4' fill='red'/></svg>",
        10,
        10,
    )

    assert image.getpixel((4, 4)) == (255, 0, 0, 255)
    assert image.getpixel((0, 0)) == (0, 0, 0, 0)


def test_snapshot_uses_rsvg_when_python_renderer_is_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "cairosvg", None)

    def rsvg_binary(_name: str) -> str:
        return "/usr/bin/rsvg-convert"

    monkeypatch.setattr(snapshot.shutil, "which", rsvg_binary)
    expected_png = snapshot.make_test_base_png(3, 2)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(stdout=expected_png)

    monkeypatch.setattr(snapshot.subprocess, "run", fake_run)

    image = snapshot._render_svg("<svg/>", 3, 2)  # pyright: ignore[reportPrivateUsage]

    assert image.size == (3, 2)
    assert commands == [["/usr/bin/rsvg-convert", "-w", "3", "-h", "2", "-"]]


def test_composite_snapshot_resizes_a_client_capture_before_overlay() -> None:
    output = snapshot.composite_snapshot(
        snapshot.make_test_base_png(2, 2),
        "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'>"
        "<rect width='4' height='4' fill='red'/></svg>",
        4,
        4,
    )

    assert snapshot.overlay_pixel_in_png(output, 2, 2) == (255, 0, 0)


def test_server_default_host_is_unconditionally_loopback() -> None:
    assert server._default_host() == "127.0.0.1"  # pyright: ignore[reportPrivateUsage]


def _unused_loopback_port(*, excluding: frozenset[int] = frozenset()) -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
        if port not in excluding:
            return port


def _return_without_serving(_server: BaseWSGIServer, poll_interval: float = 0.5) -> None:
    del poll_interval


def test_server_uses_first_free_default_port_and_prints_its_url(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_port = _unused_loopback_port()
    second_port = _unused_loopback_port(excluding=frozenset({first_port}))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", first_port))
        occupied.listen()
        assert occupied.getsockname()[1] == first_port
        monkeypatch.delenv("HF_EDITOR_PORT", raising=False)
        monkeypatch.setattr(server, "_default_editor_ports", lambda: (first_port, second_port))
        monkeypatch.setattr(BaseWSGIServer, "serve_forever", _return_without_serving)

        server.main()

    assert f"Editor running at http://127.0.0.1:{second_port}" in capsys.readouterr().out


def test_server_keeps_free_default_port_and_prints_its_url(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    port = _unused_loopback_port()
    monkeypatch.delenv("HF_EDITOR_PORT", raising=False)
    monkeypatch.setattr(server, "_default_editor_ports", lambda: (port,))
    monkeypatch.setattr(BaseWSGIServer, "serve_forever", _return_without_serving)

    server.main()

    assert f"Editor running at http://127.0.0.1:{port}" in capsys.readouterr().out


def test_server_default_scan_starts_at_5000_and_explicit_port_disables_fallback(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("HF_EDITOR_PORT", raising=False)
    default_ports = server._editor_ports()  # pyright: ignore[reportPrivateUsage]
    assert default_ports[0] == 5000
    assert 1 < len(default_ports) < 100

    monkeypatch.setenv("HF_EDITOR_PORT", "6123")
    assert server._editor_ports() == (6123,)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("raw_port", ["0", "65536", "not-a-port"])
def test_server_rejects_invalid_port_overrides(monkeypatch: MonkeyPatch, raw_port: str) -> None:
    monkeypatch.setenv("HF_EDITOR_PORT", raw_port)

    with pytest.raises(SystemExit, match="HF_EDITOR_PORT"):
        server._editor_port()  # pyright: ignore[reportPrivateUsage]


def test_server_has_no_inline_source_execution_helper() -> None:
    assert not hasattr(server, "_exec_case_source")


def test_exec_case_path_uses_shared_sandbox_and_reports_missing_file(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    case_path = tmp_path / "case.py"
    case_path.write_text("case = object()\n")
    sentinel = object()

    import cadkit.case_execution

    execute = mock.Mock(return_value=sentinel)
    monkeypatch.setattr(cadkit.case_execution, "execute_case_path", execute)

    assert server._exec_case_path(case_path) is sentinel  # pyright: ignore[reportPrivateUsage]
    execute.assert_called_once_with(case_path)
    with pytest.raises(FileNotFoundError):
        server._exec_case_path(tmp_path / "missing.py")  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def client() -> Iterator[FlaskClient]:
    app = server.create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def _patch_submission_paths(monkeypatch: MonkeyPatch, tmp_path: Path) -> Path:
    case_path = tmp_path / "case.py"
    case_path.write_text("case = 'original'\n")

    def resolve_case_path(_path: str) -> Path:
        return case_path

    monkeypatch.setattr(server, "_resolve_case_path", resolve_case_path)

    def work_dir_for(_path: str, submit_id: str) -> Path:
        return tmp_path / "submits" / submit_id

    monkeypatch.setattr(
        server,
        "_work_dir_for",
        work_dir_for,
    )
    import cadkit.case_execution

    monkeypatch.setattr(cadkit.case_execution, "require_sandbox", lambda: None)
    return case_path


def test_submit_returns_vision_failed_and_restores_case_on_agent_exception(
    client: FlaskClient, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    case_path = _patch_submission_paths(monkeypatch, tmp_path)
    import agent.loop

    def fail_iteration(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("agent unavailable")

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", fail_iteration)

    response = client.post(
        "/submit",
        json={"case_path": "case.py", "feedback": {"notes": ["try again"]}},
    )

    assert response.status_code == 500
    assert response.get_json()["status"] == "vision_failed"
    assert "agent unavailable" in response.get_json()["error"]
    assert case_path.read_text() == "case = 'original'\n"


def test_submit_reports_manifest_failure_after_successful_agent_run(
    client: FlaskClient, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    case_path = _patch_submission_paths(monkeypatch, tmp_path)
    import agent.loop

    def iterate_ok(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "iterations": 2}

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", iterate_ok)

    def manifest_error(_path: Path) -> tuple[Any, Exception | None]:
        return None, ValueError("bad case")

    monkeypatch.setattr(server, "_refresh_manifest", manifest_error)

    response = client.post("/submit", json={"case_path": "case.py", "feedback": {}})

    assert response.status_code == 500
    assert response.get_json()["status"] == "vision_ok_but_manifest_failed"
    assert "bad case" in response.get_json()["error"]
    assert case_path.read_text() == "case = 'original'\n"


def test_submit_reports_render_failure_with_refreshed_manifest(
    client: FlaskClient, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    case_path = _patch_submission_paths(monkeypatch, tmp_path)
    import agent.loop

    def iterate_ok(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "iterations": 2}

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", iterate_ok)
    manifest: dict[str, Any] = {"elements": [], "coordinate_system": "test", "units": "mm"}

    def refresh_manifest(_path: Path) -> tuple[Any, Exception | None]:
        return manifest, None

    monkeypatch.setattr(server, "_refresh_manifest", refresh_manifest)

    def render_error(
        _path: Path, _manifest: Any
    ) -> tuple[tuple[str, str] | None, Exception | None]:
        return None, RuntimeError("renderer unavailable")

    monkeypatch.setattr(server, "_render_submission", render_error)

    response = client.post("/submit", json={"case_path": "case.py", "feedback": {}})

    assert response.status_code == 500
    body = response.get_json()
    assert body["status"] == "vision_ok_but_render_failed"
    assert body["manifest"] == manifest
    assert "renderer unavailable" in body["error"]
    assert case_path.read_text() == "case = 'original'\n"


def test_orchestrator_moves_legacy_source_changes_to_private_candidate_and_rolls_back(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    case_path = tmp_path / "case.py"
    case_path.write_text("case = 'original'\n")
    work_dir = tmp_path / "submission"
    work_dir.mkdir()
    import agent.loop

    def legacy_orchestrator(path: Path, _submit_dir: Path, **_kwargs: Any) -> dict[str, Any]:
        path.write_text("case = 'candidate'\n")
        return {"status": "ok", "iterations": 1}

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", legacy_orchestrator)

    result, error = server._run_orchestrator(  # pyright: ignore[reportPrivateUsage]
        case_path, work_dir
    )

    assert error is None
    assert result == {"status": "ok", "iterations": 1}
    assert case_path.read_text() == "case = 'original'\n"
    assert (work_dir / "case.py").read_text() == "case = 'candidate'\n"


def test_submit_serializes_case_mutation_and_preserves_newer_update(
    client: FlaskClient, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    case_path = _patch_submission_paths(monkeypatch, tmp_path)
    import agent.loop

    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    calls = 0
    saw_second_while_first_running = False

    def iterate(_path: Path, _submit_dir: Path, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls, saw_second_while_first_running
        calls += 1
        if calls == 1:
            case_path.write_text("case = 'first'\n")
            first_entered.set()
            saw_second_while_first_running = second_entered.wait(timeout=0.1)
            assert release_first.wait(timeout=2.0)
            return {"status": "fatal_after_iterations", "iterations": 1, "violations": []}
        second_entered.set()
        case_path.write_text("case = 'second'\n")
        return {"status": "ok", "iterations": 1, "violations": []}

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", iterate)

    def refresh_manifest(_path: Path) -> tuple[Any, Exception | None]:
        return {"elements": []}, None

    def render_submission(_path: Path, _manifest: Any) -> tuple[tuple[str, str], None]:
        return ("snapshot", "glb"), None

    monkeypatch.setattr(server, "_refresh_manifest", refresh_manifest)
    monkeypatch.setattr(
        server,
        "_render_submission",
        render_submission,
    )
    responses: list[Any] = []

    def submit() -> None:
        with client.application.test_client() as thread_client:
            responses.append(
                thread_client.post("/submit", json={"case_path": "case.py", "feedback": {}})
            )

    first = threading.Thread(target=submit)
    second = threading.Thread(target=submit)
    first.start()
    assert first_entered.wait(timeout=2.0)
    second.start()
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not first.is_alive() and not second.is_alive()
    assert saw_second_while_first_running is False
    assert sorted(response.status_code for response in responses) == [200, 500]
    assert case_path.read_text() == "case = 'second'\n"


def test_submit_rolls_back_oversized_agent_case_before_manifest(
    client: FlaskClient, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    case_path = _patch_submission_paths(monkeypatch, tmp_path)
    original = case_path.read_bytes()
    import agent.loop

    def iterate_oversized(path: Path, _submit_dir: Path, **_kwargs: Any) -> dict[str, Any]:
        path.write_bytes(b"# generated\n" + b"x" * server.MAX_CASE_BYTES)
        return {"status": "ok", "iterations": 1, "violations": []}

    def manifest_must_not_run(_path: Path) -> tuple[Any, Exception | None]:
        pytest.fail("oversized generated case reached manifest parsing")

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", iterate_oversized)
    monkeypatch.setattr(server, "_refresh_manifest", manifest_must_not_run)

    response = client.post("/submit", json={"case_path": "case.py", "feedback": {}})

    assert response.status_code == 500
    assert response.get_json()["status"] == "vision_failed"
    assert "execution size limit" in response.get_json()["error"]
    assert case_path.read_bytes() == original


def test_snapshot_and_submit_reject_oversized_requests(client: FlaskClient) -> None:
    huge_overlay = "<svg>" + ("x" * (256 * 1024)) + "</svg>"
    snapshot_response = client.post("/snapshot", json={"overlay": huge_overlay})
    assert snapshot_response.status_code == 400
    assert "payload limit" in snapshot_response.get_json()["error"]

    huge_feedback = {"notes": ["x" * (256 * 1024)]}
    submit_response = client.post(
        "/submit", json={"case_path": "case.py", "feedback": huge_feedback}
    )
    assert submit_response.status_code == 413


def test_snapshot_rejects_unknown_case_path_without_running_cad(
    client: FlaskClient,
) -> None:
    response = client.post(
        "/snapshot",
        json={"part_path": "examples/missing/case.py", "overlay": "<svg/>"},
    )

    assert response.status_code == 400
    assert "case.py not found" in response.get_json()["error"]
