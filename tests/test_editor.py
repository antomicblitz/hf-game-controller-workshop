"""Focused request-shape tests for the hardened editor boundary."""

from __future__ import annotations

from tools.editor.server import create_app


def test_snapshot_rejects_non_object_json() -> None:
    client = create_app().test_client()

    response = client.post("/snapshot", json=["not", "an", "object"])

    assert response.status_code == 400
    assert response.get_json()["error"] == "request body must be a JSON object"


def test_index_disables_browser_caching() -> None:
    client = create_app().test_client()

    response = client.get("/")

    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
