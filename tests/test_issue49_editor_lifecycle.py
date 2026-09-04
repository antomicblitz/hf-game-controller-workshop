"""Issue #49 contracts for local editor lifecycle and cable projections."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, cast

import pytest
from _pytest.monkeypatch import MonkeyPatch


@pytest.mark.parametrize("raises_interrupt", [True, False], ids=["ctrl-c", "normal-shutdown"])
def test_foreground_main_restores_case_after_any_server_shutdown(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    raises_interrupt: bool,
) -> None:
    from tools.editor import server

    case_path = tmp_path / "examples" / "6-button-gamepad" / "case.py"
    case_path.parent.mkdir(parents=True)
    original = b"case = 'pre-existing'\n"
    case_path.write_bytes(original)
    case_path.chmod(0o754)
    monkeypatch.setattr(server, "_SESSION_DIR", tmp_path)

    calls: dict[str, Any] = {}

    class ForegroundServer:
        port = 5000

        def serve_forever(self) -> None:
            calls["served"] = True
            case_path.write_bytes(b"case = 'interrupted'\n")
            if raises_interrupt:
                raise KeyboardInterrupt

        def server_close(self) -> None:
            calls["closed"] = True

    def make_editor_server(_app: Any, host: str) -> ForegroundServer:
        calls["host"] = host
        return ForegroundServer()

    monkeypatch.setattr(server, "create_app", object)
    monkeypatch.setattr(server, "_make_editor_server", make_editor_server)
    monkeypatch.delenv("HF_EDITOR_PORT", raising=False)

    server.main()

    assert calls == {"host": "127.0.0.1", "served": True, "closed": True}
    assert case_path.read_bytes() == original
    assert case_path.stat().st_mode & 0o777 == 0o754
    output = capsys.readouterr().out
    assert "Editor running at http://127.0.0.1:5000" in output


def test_keyboard_interrupt_during_regeneration_restores_source(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    from tools.editor import server

    case_path = tmp_path / "case.py"
    original = b"case = 'pre-existing'\n"
    case_path.write_bytes(original)

    def resolve_case_path(_value: str) -> Path:
        return case_path

    def work_dir_for(_path: str | Path, submit_id: str) -> Path:
        return tmp_path / submit_id

    monkeypatch.setattr(server, "_resolve_case_path", resolve_case_path)
    monkeypatch.setattr(server, "_work_dir_for", work_dir_for)

    import agent.loop
    import cadkit.case_execution

    monkeypatch.setattr(cadkit.case_execution, "require_sandbox", lambda: None)

    def interrupt(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        case_path.write_bytes(b"case = 'partial'\n")
        raise KeyboardInterrupt

    monkeypatch.setattr(agent.loop, "iterate_with_visual_feedback", interrupt)
    app = server.create_app()
    app.config["TESTING"] = True

    with pytest.raises(KeyboardInterrupt), app.test_client() as client:
        client.post("/submit", json={"case_path": "case.py", "feedback": {}})

    assert case_path.read_bytes() == original


def test_glb_marks_the_six_button_signal_routes_as_dupont_alligator_cables() -> None:
    from tools.cadkit.assembly import build_demo_assembly
    from tools.editor.gltf_scene import build_assembled_glb

    scene = build_demo_assembly()
    glb = build_assembled_glb(scene)
    json_length, json_type = struct.unpack_from("<II", glb, 12)
    assert json_type == 0x4E4F534A
    document = cast(dict[str, Any], json.loads(glb[20 : 20 + json_length].decode("utf-8")))

    signal_routes = {
        node["name"]: node["extras"]
        for node in document["nodes"]
        if node["name"].startswith("wire.control_")
        and not node["name"].startswith("wire.control_ground_")
    }

    assert len(signal_routes) == 6
    assert {
        (extras["cable_kind"], extras["termination_kind"]) for extras in signal_routes.values()
    } == {("Dupont", "alligator clips")}
