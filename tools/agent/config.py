"""Small, non-secret defaults for the visual case editor."""

import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = json.loads((_PROJECT_ROOT / "opencode.json").read_text(encoding="utf-8"))
DEFAULT_MODEL = str(_CONFIG["model"])
DEFAULT_MAX_ITERATIONS = 2

__all__ = ["DEFAULT_MAX_ITERATIONS", "DEFAULT_MODEL"]
