"""Fast structural checks for the no-build student browser project."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "student-project"


def _require(path: str, *needles: str) -> None:
    source = (WEB / path).read_text(encoding="utf-8")
    for needle in needles:
        if needle not in source:
            raise RuntimeError(f"{path} is missing required workshop contract: {needle}")


def main() -> int:
    _require("index.html", "game/", "viewer/")
    _require(
        "controller-input.js",
        "gamepad?.axes[0]",
        "gamepad?.axes[1]",
        "gamepad?.buttons[0]",
        "gamepad?.buttons[1]",
        'pressedKeys.has("ArrowUp")',
        'pressedKeys.has("KeyZ")',
        'pressedKeys.has("KeyX")',
    )
    _require("game/index.html", "game.js", "Arrow keys", "Action A", "Action B")
    _require("game/game.js", 'from "../controller-input.js"', "requestAnimationFrame")
    _require("viewer/index.html", "STL viewer", "viewer.js", "case-top.stl", "case-bottom.stl")
    _require("viewer/viewer.js", "STLLoader", "OrbitControls", "FileReader")
    for name in ("case-top.stl", "case-bottom.stl"):
        path = WEB / "assets" / name
        if not path.is_file() or path.stat().st_size < 84:
            raise RuntimeError(f"missing or empty default STL: {path}")
        if path.read_bytes() != (ROOT / "controller" / "default-stl" / name).read_bytes():
            raise RuntimeError(f"viewer STL does not match approved fallback: {name}")
    print("Student game, controller adapter, viewer, and default STLs are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
