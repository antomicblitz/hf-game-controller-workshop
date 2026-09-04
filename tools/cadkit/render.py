"""Render pipeline — export STL, STEP, and PNG from a Build123d Part.

PNG rendering uses matplotlib + numpy-stl (already common Python
deps). VS-04 changes the policy: **PNG is now fail-closed** — if the
optional dependencies are missing, the helper re-raises the
``ImportError`` so the operator sees it rather than silently dropping
the preview. The convenience :func:`export_part` exposes a
``render_png=False`` default for callers that intentionally do not
want the preview.

Headless: this module never opens a display. Works on linux servers
without X11.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import build123d as _build123d
from build123d import Part

PathLike = str | Path

_build123d_api: Any = cast(Any, _build123d)
_export_stl = cast(Callable[[Part, str], object], _build123d_api.export_stl)
_export_step = cast(Callable[[Part, str], object], _build123d_api.export_step)


# ---------------------------------------------------------------------------
# STL / STEP export
# ---------------------------------------------------------------------------
def export_stl_part(part: Part, path: PathLike) -> Path:
    """Export a Part to STL. Returns the path written."""
    path = Path(path)
    _export_stl(part, str(path))
    return path


def export_step_part(part: Part, path: PathLike) -> Path:
    """Export a Part to STEP. Returns the path written."""
    path = Path(path)
    _export_step(part, str(path))
    return path


# ---------------------------------------------------------------------------
# PNG rendering from STL
# ---------------------------------------------------------------------------
def export_png(
    stl_path: PathLike,
    png_path: PathLike,
    *,
    azimuth_deg: float = 35.0,
    elevation_deg: float = 25.0,
    dpi: int = 120,
) -> Path:
    """Render a PNG preview from an STL file at a fixed isometric angle.

    Args:
        stl_path: input STL.
        png_path: output PNG.
        azimuth_deg: viewing azimuth (rotation about Z).
        elevation_deg: viewing elevation (tilt above the XY plane).
        dpi: image resolution.

    Returns:
        Path to the written PNG.

    Raises:
        ImportError: if matplotlib or numpy-stl are not installed.
            **Fail-closed** (VS-04): the missing-dependency case is no
            longer suppressed; the operator sees it.
        FileNotFoundError: if stl_path does not exist.
    """
    # Fail-closed (VS-04): missing optional dependencies surface as
    # ImportError rather than being silently absorbed.
    from matplotlib import pyplot as _plt
    from mpl_toolkits.mplot3d.art3d import (  # pyright: ignore[reportMissingTypeStubs]
        Poly3DCollection as _Poly3DCollection,
    )
    from stl import mesh as _stl_mesh

    # These optional render dependencies do not ship complete type stubs;
    # keep the untyped boundary local to the preview-only branch.
    plt: Any = cast(Any, _plt)
    Poly3DCollection: Any = cast(Any, _Poly3DCollection)
    stl_mesh: Any = cast(Any, _stl_mesh)

    stl_path = Path(stl_path)
    png_path = Path(png_path)
    if not stl_path.exists():
        raise FileNotFoundError(f"STL file not found: {stl_path}")

    your_mesh = stl_mesh.Mesh.from_file(str(stl_path))
    vectors = your_mesh.vectors  # shape (n_triangles, 3, 3)

    fig = plt.figure(figsize=(8, 6), dpi=dpi)
    ax = fig.add_subplot(111, projection="3d")
    mesh_collection = Poly3DCollection(
        vectors,
        facecolors="lightgray",
        edgecolors="dimgray",
        linewidths=0.1,
        alpha=1.0,
    )
    ax.add_collection3d(mesh_collection)

    # Scale axes to the mesh's actual bounds.
    scale = your_mesh.points.flatten()
    ax.auto_scale_xyz(scale, scale, scale)

    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_zlabel("Z (mm)")
    ax.view_init(elev=elevation_deg, azim=azimuth_deg)
    ax.set_box_aspect((1, 1, 1))

    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(png_path), dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return png_path


# ---------------------------------------------------------------------------
# Convenience: full export (STL + STEP + PNG)
# ---------------------------------------------------------------------------
def export_part(
    part: Part,
    base_path: PathLike,
    *,
    render_png: bool = False,
) -> dict[str, Path]:
    """Export a Part to STL + STEP (+ optional PNG) with a shared base path.

    Args:
        part: the Build123d Part.
        base_path: file path WITHOUT extension; ".stl", ".step", ".png"
            are appended automatically.
        render_png: when ``True``, also write a PNG preview. PNG
            rendering requires matplotlib + numpy-stl and raises
            ``ImportError`` if those dependencies are missing (VS-04
            fail-closed change — the silent suppression is gone).
            When ``False`` (the new default), only STL + STEP are
            written. Worked examples that pre-date VS-04 should pass
            ``render_png=False`` explicitly so they do not pick up
            the new dependency on the worked-example path.

    Returns:
        dict of {format: path} for each file written.
    """
    base_path = Path(base_path)
    written: dict[str, Path] = {}
    written["stl"] = export_stl_part(part, base_path.with_suffix(".stl"))
    written["step"] = export_step_part(part, base_path.with_suffix(".step"))
    if render_png:
        written["png"] = export_png(
            written["stl"],
            base_path.with_suffix(".png"),
        )
    return written
