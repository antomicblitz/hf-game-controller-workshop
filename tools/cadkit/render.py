"""STL export used by the local case editor."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import build123d as _build123d
from build123d import Part

PathLike = str | Path

_build123d_api: Any = cast(Any, _build123d)
_export_stl = cast(Callable[[Part, str], object], _build123d_api.export_stl)


def export_stl_part(part: Part, path: PathLike) -> Path:
    """Export one part to STL and return the written path."""
    output = Path(path)
    _export_stl(part, str(output))
    return output


__all__ = ["export_stl_part"]
