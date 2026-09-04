"""Snapshot pipeline — Slice 8.

The critical-path proof that **WebGL canvas + SVG overlay → composited PNG**
works end-to-end. This is the entire reason Slice 8 exists first.

Two halves:

1. **Server side** (`server.py`): Flask app that wraps `yacv_server`. Exposes
   `/snapshot` to take a Build123d Part (or a path to a `case.py` script),
   tessellate it via `yacv_server.tessellate`, and return the data the
   browser needs to render a snapshot.

2. **Client side** (HTML + JS in `frontend/`): renders the GLTF via
   `model-viewer`, overlays an SVG annotation layer, and composites both
   into a single PNG that gets sent back to the agent.

For Slice 8, the **server side** is what we're shipping now. The client side
ships in Slices 9-10. This file implements the server side and a
**deterministic test that the snapshot pipeline produces a non-empty image**
without needing a browser.

If the snapshot pipeline silently breaks (WebGL canvas doesn't include the
SVG overlay, cross-origin tainting prevents `toDataURL`, etc.), Slice 8's
test catches it before Slice 10's UI work is wasted.
"""

from __future__ import annotations

import io
import math
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Generator
from contextlib import contextmanager
from enum import Enum
from typing import Any, cast

# Block the yacv-server auto-start at import time. We instantiate YACV
# ourselves in server.py when /snapshot is called.
os.environ.setdefault("YACV_DISABLE_SERVER", "1")

from PIL import Image, ImageDraw, ImageFont

MAX_SVG_BYTES = 256 * 1024
MAX_SVG_ELEMENTS = 2048
MAX_SVG_DEPTH = 32
MAX_SVG_DIMENSION = 4096.0
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_777_216
MAX_IMAGE_DIMENSION = 4096

_SVG_EXTERNAL_REFERENCE = re.compile(
    r"(?:@import\b|url\s*\(|(?:https?|file|data|javascript):|//)", re.IGNORECASE
)
_SVG_DIMENSION_ATTRIBUTES = {
    "width",
    "height",
    "x",
    "y",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "stroke-width",
}


def tessellate_part(part: Any, *, tolerance: float = 0.5) -> bytes:
    """Tessellate a Build123d Part into a binary glTF payload.

    Args:
        part: a Build123d `Part` (or anything `yacv_server.tessellate` accepts —
            `TopLoc_Location` or `TopoDS_Shape`).
        tolerance: linear deflection in mm. Default 0.5 — coarse enough for
            workshop-scale controllers (max ~120mm).

    Returns:
        Bytes of a `.glb` file (binary glTF).
    """
    # yacv_server.tessellate() accepts TopoDS_Shape but not Build123d Part
    # directly. Unwrap the Part via `.wrapped`. If the caller passes a
    # raw OCP shape, leave it as-is.
    if hasattr(part, "wrapped") and not _is_ocp_shape(part):
        part = part.wrapped

    import inspect as _inspect

    _tess = _import_yacv_tessellate()

    # yacv-server API drift: >=0.11 uses color_faces/color_edges/color_vertices
    # while 0.9.x uses obj_color + boolean faces/edges/vertices. Inspect the
    # signature so the same call works against either lineage.
    tessellate = cast(
        Callable[..., Any],
        _tess.tessellate,  # pyright: ignore[reportUnknownMemberType]
    )
    _params = _inspect.signature(tessellate).parameters
    _kwargs: dict[str, Any] = {"tolerance": tolerance}
    _light_gray = (0.78, 0.78, 0.82, 1.0)
    if "color_faces" in _params:
        _kwargs["color_faces"] = _light_gray
    if "obj_color" in _params:
        _kwargs["obj_color"] = _light_gray
    # Edge / vertex colours are best-effort; missing kwargs simply take the
    # yacv-server default (gray edges, amber vertices when supported).
    if "color_edges" in _params:
        _kwargs["color_edges"] = (0.30, 0.30, 0.30, 1.0)
    if "color_vertices" in _params:
        _kwargs["color_vertices"] = (1.0, 0.65, 0.0, 1.0)
    gltf: Any = tessellate(part, **_kwargs)
    import pygltflib  # pyright: ignore[reportMissingTypeStubs]

    if not isinstance(gltf, pygltflib.GLTF2):
        raise TypeError(f"tessellate() returned {type(gltf).__name__}, expected GLTF2")
    # pygltflib API inconsistency: `save_to_bytes()` exists but returns the
    # internal chunk LIST (not bytes — it's a known buggy alias), while
    # `save_binary(filename)` writes a valid GLB file. We use the file path
    # via a temp file and read it back.
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as f:
        tmp_path = f.name
    try:
        save_binary = cast(
            Callable[[str], Any],
            gltf.save_binary,  # pyright: ignore[reportUnknownMemberType]
        )
        save_binary(tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        import os

        os.unlink(tmp_path)


def _is_ocp_shape(obj: Any) -> bool:
    """True if obj is an OCP TopoDS_Shape (not a build123d Part that *has* a wrapped attr)."""
    cls = type(obj).__module__ or ""
    return cls.startswith("OCP.")


@contextmanager
def _http_method_compat() -> Generator[None, None, None]:
    """Temporarily provide Python 3.11's HTTPMethod to older interpreters."""
    import http

    http_module: Any = http
    if hasattr(http_module, "HTTPMethod"):
        yield
        return

    class _HTTPMethod(str, Enum):
        CONNECT = "CONNECT"
        DELETE = "DELETE"
        GET = "GET"
        HEAD = "HEAD"
        OPTIONS = "OPTIONS"
        PATCH = "PATCH"
        POST = "POST"
        PUT = "PUT"
        TRACE = "TRACE"

    http_module.HTTPMethod = _HTTPMethod
    try:
        yield
    finally:
        delattr(http_module, "HTTPMethod")


def _import_yacv_tessellate() -> Any:
    """Import YACV's tessellator with a narrowly scoped HTTPMethod fallback."""
    with _http_method_compat():
        import yacv_server.tessellate as _tess

    return _tess


def _svg_dimension(value: str, name: str) -> float:
    match = re.fullmatch(
        r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
        r"\s*(px|pt|pc|mm|cm|in)?\s*",
        value,
        re.IGNORECASE,
    )
    if match is None:
        raise ValueError(f"SVG {name} has an unsupported dimension")
    number = float(match.group(1))
    unit = (match.group(2) or "px").lower()
    scale = {
        "px": 1.0,
        "pt": 96.0 / 72.0,
        "pc": 16.0,
        "mm": 96.0 / 25.4,
        "cm": 96.0 / 2.54,
        "in": 96.0,
    }[unit]
    pixels = number * scale
    if not math.isfinite(pixels) or abs(pixels) > MAX_SVG_DIMENSION:
        raise ValueError(f"SVG {name} exceeds the dimension limit")
    return pixels


def _validate_viewbox(value: str) -> None:
    values = value.replace(",", " ").split()
    if len(values) != 4:
        raise ValueError("SVG viewBox is invalid")
    numbers = [float(item) for item in values]
    if any(not math.isfinite(item) for item in numbers) or any(
        abs(item) > MAX_SVG_DIMENSION for item in numbers
    ):
        raise ValueError("SVG viewBox exceeds the dimension limit")
    if numbers[2] <= 0 or numbers[3] <= 0:
        raise ValueError("SVG viewBox must have positive dimensions")


def _validate_svg_attributes(element: Any) -> None:
    for raw_name, raw_value in element.attrib.items():
        name = str(raw_name).rsplit("}", 1)[-1].lower()
        value = str(raw_value)
        if name in {"href", "src", "xlink:href"} or name.startswith("on"):
            raise ValueError("SVG external references and event handlers are not allowed")
        if _SVG_EXTERNAL_REFERENCE.search(value):
            raise ValueError("SVG external references are not allowed")
        if name in _SVG_DIMENSION_ATTRIBUTES:
            _svg_dimension(value, name)
        if name == "viewbox":
            _validate_viewbox(value)


def _validate_svg_element(element: Any, depth: int) -> int:
    if depth > MAX_SVG_DEPTH:
        raise ValueError("SVG exceeds the complexity limit")
    tag = str(element.tag).rsplit("}", 1)[-1].lower()
    if tag == "style":
        css_text = "".join(str(text) for text in element.itertext())
        if _SVG_EXTERNAL_REFERENCE.search(css_text):
            raise ValueError("SVG CSS external references are not allowed")
        raise ValueError("SVG element style is not allowed")
    if tag in {"script", "foreignobject"}:
        raise ValueError(f"SVG element {tag} is not allowed")
    _validate_svg_attributes(element)
    count = 1 + sum(_validate_svg_element(child, depth + 1) for child in element)
    if count > MAX_SVG_ELEMENTS:
        raise ValueError("SVG exceeds the complexity limit")
    return count


def _validate_svg(svg_str: str) -> None:
    """Reject SVG features that can fetch resources or exhaust renderers."""
    encoded = svg_str.encode("utf-8")
    if len(encoded) > MAX_SVG_BYTES:
        raise ValueError("SVG overlay exceeds the payload limit")
    if re.search(r"<!DOCTYPE|<!ENTITY|<\?(?!xml(?:\s|\?|$))", svg_str, re.IGNORECASE):
        raise ValueError("SVG declarations and processing instructions are not allowed")
    try:
        from defusedxml import ElementTree as ET

        root = ET.fromstring(svg_str)
    except Exception as exc:
        raise ValueError("SVG is invalid or unsafe") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ValueError("SVG root element is required")
    _validate_svg_element(root, 0)


def composite_snapshot(
    base_png: bytes,
    overlay_svg: str,
    width: int,
    height: int,
) -> bytes:
    """Composite an SVG overlay on top of a base PNG and return the result.

    This is the function the client side calls after capturing the WebGL
    canvas and serializing the annotation overlay. In Slice 8 we test it
    server-side with a synthetic PNG; in Slices 9-10 the client wires
    `model-viewer` + the overlay SVG to this contract.

    Args:
        base_png: the PNG bytes from the WebGL canvas (or a placeholder).
        overlay_svg: a serialized `<svg>` element string. The SVG is
            rendered as an `<img>` and drawn on top of the base image.
        width: target canvas width (px).
        height: target canvas height (px).

    Returns:
        PNG bytes of the composited image.
    """
    _validate_svg(overlay_svg)
    if len(base_png) > MAX_IMAGE_BYTES:
        raise ValueError("base PNG exceeds the payload limit")
    if (
        width < 1
        or height < 1
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ValueError("snapshot dimensions exceed the render limits")
    base_image = Image.open(io.BytesIO(base_png))
    if (
        base_image.width > MAX_IMAGE_DIMENSION
        or base_image.height > MAX_IMAGE_DIMENSION
        or base_image.width * base_image.height > MAX_IMAGE_PIXELS
    ):
        raise ValueError("base PNG dimensions exceed the render limits")
    base = base_image.convert("RGBA")
    if base.size != (width, height):
        base = base.resize((width, height), Image.Resampling.LANCZOS)

    # SVG → image. cairosvg is preferred (crisp, deterministic); PIL's
    # built-in SVG support is absent in modern Pillow. We try cairosvg,
    # then rsvg-convert via subprocess, then a fallback raster path.
    overlay_img = _render_svg(overlay_svg, width, height)
    if overlay_img.mode != "RGBA":
        overlay_img = overlay_img.convert("RGBA")

    canvas = Image.alpha_composite(base, overlay_img)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _render_svg(svg_str: str, width: int, height: int) -> Image.Image:
    """Render an SVG string to a PIL Image at (width, height).

    Tries cairosvg first (Python, fast, no subprocess), then rsvg-convert
    (CLI, fallback), then a minimal handwritten SVG rasterizer for the
    common annotation primitives (rect, circle, line, text).
    """
    if width < 1 or height < 1 or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ValueError("snapshot dimensions exceed the render limits")
    _validate_svg(svg_str)
    try:
        import cairosvg  # pyright: ignore[reportMissingTypeStubs]

        svg2png = cast(
            Callable[..., Any],
            cairosvg.svg2png,  # pyright: ignore[reportUnknownMemberType]
        )
        png_bytes = cast(
            bytes,
            svg2png(
                bytestring=svg_str.encode("utf-8"),
                output_width=width,
                output_height=height,
            ),
        )
        if len(png_bytes) > MAX_IMAGE_BYTES:
            raise ValueError("rendered SVG exceeds the payload limit")
        return Image.open(io.BytesIO(png_bytes))
    except (ImportError, OSError):
        pass

    try:
        rsvg_binary = shutil.which("rsvg-convert")
        if rsvg_binary is None:
            raise FileNotFoundError("rsvg-convert is not on PATH")
        rsvg_binary = str(os.path.realpath(rsvg_binary))
        result = subprocess.run(  # noqa: S603  # executable is resolved from PATH above.
            [rsvg_binary, "-w", str(width), "-h", str(height), "-"],
            input=svg_str.encode("utf-8"),
            capture_output=True,
            check=True,
            timeout=5,
        )
        if len(result.stdout) > MAX_IMAGE_BYTES:
            raise ValueError("rendered SVG exceeds the payload limit")
        return Image.open(io.BytesIO(result.stdout))
    except (
        ImportError,
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        pass

    # Last-resort fallback: a tiny SVG rasterizer for the primitives we
    # use in the workshop. Not pretty, but enough for Slice 8's tests.
    return _fallback_svg_rasterize(svg_str, width, height)


def _fallback_svg_rasterize(svg_str: str, width: int, height: int) -> Image.Image:
    """A minimal SVG rasterizer for the workshop's annotation primitives.

    Supports: `<rect>`, `<circle>`, `<line>`, `<text>`. Anything else is
    ignored. This is a fallback — cairosvg is the primary renderer; we
    install it as a hard dep when we lock workshop deps in Slice 12.
    """
    from defusedxml import ElementTree as ET

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    NS = "{http://www.w3.org/2000/svg}"

    # Parse the SVG, tolerate namespaces.
    root = ET.fromstring(svg_str)

    for el in root.iter():
        tag = el.tag.replace(NS, "")
        if tag == "rect":
            x = float(el.get("x", "0"))
            y = float(el.get("y", "0"))
            w = float(el.get("width", "0"))
            h = float(el.get("height", "0"))
            fill = el.get("fill", "red")
            draw.rectangle([x, y, x + w, y + h], fill=fill)
        elif tag == "circle":
            cx = float(el.get("cx", "0"))
            cy = float(el.get("cy", "0"))
            r = float(el.get("r", "5"))
            fill = el.get("fill", "red")
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fill, width=2)
        elif tag == "line":
            x1 = float(el.get("x1", "0"))
            y1 = float(el.get("y1", "0"))
            x2 = float(el.get("x2", "0"))
            y2 = float(el.get("y2", "0"))
            stroke = el.get("stroke", "red")
            draw.line([x1, y1, x2, y2], fill=stroke, width=2)
        elif tag == "text":
            x = float(el.get("x", "0"))
            y = float(el.get("y", "0"))
            fill = el.get("fill", "black")
            draw.text(
                (x, y),
                "".join(el.itertext()),
                fill=fill,
                # Pillow 10.0 is the declared lower bound; ``size`` was added
                # to load_default() in 10.1. Keep the last-resort renderer
                # deterministic without requiring the newer API.
                font=ImageFont.load_default(),
                anchor="ls",
            )

    return img


def make_test_overlay(width: int = 800, height: int = 600) -> str:
    """A minimal SVG overlay with one red rect, one blue circle, one black line.

    Sized to avoid cairosvg sub-pixel anti-aliasing artifacts (rectangles
    < ~50px get fuzzy edges). 200×200 rect, centered horizontally.
    """
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        f'<rect x="100" y="100" width="200" height="200" fill="red"/>'
        f'<circle cx="500" cy="300" r="40" fill="blue" stroke="green" stroke-width="4"/>'
        f'<line x1="0" y1="0" x2="100" y2="100" stroke="black" stroke-width="4"/>'
        "</svg>"
    )


def make_test_base_png(width: int = 800, height: int = 600) -> bytes:
    """A plain white PNG — for the snapshot compositing test."""
    img = Image.new("RGB", (width, height), (255, 255, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def overlay_pixel_in_png(png_bytes: bytes, x: int, y: int) -> tuple[int, int, int]:
    """Read the RGB pixel at (x, y) in a PNG. Used by the test to verify
    the overlay was actually composited."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    px = img.getpixel((x, y))
    # PIL's getpixel() returns Any for typed images; narrow for the test helper.
    if not isinstance(px, tuple) or len(px) != 3:
        raise TypeError(f"unexpected pixel value: {px!r}")
    return (int(px[0]), int(px[1]), int(px[2]))
