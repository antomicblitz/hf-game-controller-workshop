"""Security tests for the editor server's loopback-only bind address."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Make tools/ importable.
_here = Path(__file__).resolve().parent
_session = _here.parent
sys.path.insert(0, str(_session / "tools"))


def test_default_host_is_loopback_only():
    """The unauthenticated development server must never expose a LAN port."""
    from tools.editor.server import _default_host  # pyright: ignore[reportPrivateUsage]

    with mock.patch.dict(os.environ, {}, clear=True):
        assert _default_host() == "127.0.0.1"


@pytest.mark.parametrize("endpoint", ["manifest", "snapshot"])
def test_case_file_endpoints_refuse_paths_outside_session(
    endpoint: str,
    tmp_path: Path,
) -> None:
    from tools.editor.server import create_app

    marker = tmp_path / "executed"
    outside_case = tmp_path / "outside_case.py"
    outside_case.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )

    client = create_app().test_client()
    if endpoint == "manifest":
        response = client.get("/manifest", query_string={"case_path": outside_case})
        assert response.status_code == 404
    else:
        response = client.post(
            "/snapshot",
            json={"part_path": str(outside_case), "overlay": "<svg></svg>"},
        )
        assert response.status_code == 400

    assert not marker.exists()


def test_editor_has_no_credential_endpoint_or_form() -> None:
    from tools.editor.server import create_app

    client = create_app().test_client()
    assert client.post("/credentials", json={"api_key": "not-a-real-key"}).status_code == 404
    page = client.get("/").get_data(as_text=True).lower()
    assert "api_key" not in page
    assert 'type="password"' not in page


def test_project_config_pins_model_without_storing_credentials() -> None:
    config_path = _session / "opencode.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["model"] == "opencode/muse-spark-1.3-contributor-free"
    assert config["default_agent"] == "plan"
    assert config["share"] == "disabled"
    assert config["subagent_depth"] == 0
    assert "apiKey" not in config_path.read_text(encoding="utf-8")
