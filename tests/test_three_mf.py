"""Tests for the production-grade 3MF writer.

The 3MF format is OPC (Open Packaging Conventions): a ZIP
containing ``[Content_Types].xml``, ``_rels/.rels``, and
``3D/3dmodel.model``. The writer is stdlib-only and works on a
Build123d Part via a temporary STL round-trip (the same path
PrusaSlicer uses when ingesting an STL).
"""

from __future__ import annotations

import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from defusedxml import ElementTree as _DefusedET

_XML_FROMSTRING_NAME = "fromstring"
_parse_xml = cast(Callable[[str | bytes], Any], getattr(_DefusedET, _XML_FROMSTRING_NAME))

_here = Path(__file__).resolve().parent
_session = _here.parent
sys.path.insert(0, str(_session / "tools"))

import pytest  # noqa: E402
from build123d import Align, Box, BuildPart  # noqa: E402
from cadkit.three_mf import (  # noqa: E402
    Tessellation,
    build_model_xml,
    parse_model_xml_entries,
    read_3mf_package,
    tessellate_part_to_vertices_and_triangles,
    write_3mf_package,
)

_ET_3MF = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def _box_part(size: float):
    with BuildPart() as bp:
        Box(size, size, size, align=(Align.CENTER, Align.CENTER, Align.MIN))
    part = bp.part
    assert part is not None
    return part


# ---------------------------------------------------------------------------
# Tessellation
# ---------------------------------------------------------------------------
def test_tessellate_produces_vertices_and_triangles():
    part = _box_part(20.0)
    tess = tessellate_part_to_vertices_and_triangles(part)
    assert isinstance(tess, Tessellation)
    assert len(tess.vertices) > 0
    assert len(tess.triangles) > 0
    # Every triangle index must reference a real vertex.
    for tri in tess.triangles:
        for idx in tri:
            assert 0 <= idx < len(tess.vertices)


def test_tessellation_rejects_out_of_range_indices():
    with pytest.raises(ValueError, match="out of range"):
        Tessellation(
            vertices=((0.0, 0.0, 0.0),),
            triangles=((0, 1, 2),),  # 1 and 2 are out of range
        )


# ---------------------------------------------------------------------------
# Model XML
# ---------------------------------------------------------------------------
def test_build_model_xml_has_namespace_and_unit():
    parts = (
        (
            "top",
            1,
            Tessellation(
                vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                triangles=((0, 1, 2),),
            ),
        ),
    )
    xml = build_model_xml(parts)
    # The unit attribute is "millimeter".
    assert b'unit="millimeter"' in xml
    # 3MF namespace is declared.
    assert b"schemas.microsoft.com/3dmanufacturing/core/2015/02" in xml


def test_build_model_xml_metadata_round_trip():
    parts = (
        (
            "top",
            1,
            Tessellation(
                vertices=((0.0, 0.0, 0.0),),
                triangles=(),
            ),
        ),
    )
    xml = build_model_xml(
        parts,
        metadata={"SourceSHA": "deadbeef", "ComponentRevision": "v1"},
    )
    root = _parse_xml(xml)
    metas = {}
    for m in root.iter("{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}metadata"):
        if m.get("name") and m.text:
            metas[m.get("name")] = m.text
    assert metas["SourceSHA"] == "deadbeef"
    assert metas["ComponentRevision"] == "v1"


# ---------------------------------------------------------------------------
# Package writer + validity
# ---------------------------------------------------------------------------
def test_write_3mf_package_produces_valid_zip(tmp_path: Path):
    parts = (
        (
            "top",
            1,
            Tessellation(
                vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                triangles=((0, 1, 2),),
            ),
        ),
        (
            "bottom",
            2,
            Tessellation(
                vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                triangles=((0, 1, 2),),
            ),
        ),
    )
    out = tmp_path / "controller.3mf"
    contents = write_3mf_package(out, parts=parts, metadata={"SourceSHA": "abc"})
    assert out.exists()
    # The ZIP contains the documented entries.
    assert contents.entries == (
        "[Content_Types].xml",
        "_rels/.rels",
        "3D/3dmodel.model",
    )
    with zipfile.ZipFile(out, "r") as zf:
        names = set(zf.namelist())
    assert names == set(contents.entries)


def test_3mf_zip_entries_are_valid_xml(tmp_path: Path):
    out = tmp_path / "x.3mf"
    write_3mf_package(
        out,
        parts=(
            (
                "top",
                1,
                Tessellation(
                    vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                    triangles=((0, 1, 2),),
                ),
            ),
        ),
    )
    entries = read_3mf_package(out)
    # Every entry is XML; ``3D/3dmodel.model`` is XML too (3MF Core
    # spec — no extension here).
    for name, content in entries.items():
        assert name.endswith(".xml") or name.endswith(".rels") or name == "3D/3dmodel.model"
        _parse_xml(content.decode("utf-8"))


def test_3mf_model_declares_both_objects(tmp_path: Path):
    parts = (
        (
            "top",
            1,
            Tessellation(
                vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                triangles=((0, 1, 2),),
            ),
        ),
        (
            "bottom",
            2,
            Tessellation(
                vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                triangles=((0, 1, 2),),
            ),
        ),
    )
    out = tmp_path / "controller.3mf"
    write_3mf_package(out, parts=parts)
    entries = read_3mf_package(out)
    objects, _metadata = parse_model_xml_entries(entries["3D/3dmodel.model"])
    names = {o[0] for o in objects}
    assert "top" in names
    assert "bottom" in names
    # Each object has at least one triangle.
    for _name, obj_id, tri_count in objects:
        assert tri_count >= 1
        assert obj_id >= 1


def test_3mf_metadata_anchors_provenance(tmp_path: Path):
    """Production metadata keys surface in the 3MF model XML.

    The keys are written under the workshop's ``hf:`` namespace
    (declared on the ``<model>`` root). The 3MF Core spec
    encourages namespaced metadata names so consumers can
    disambiguate the workshop's keys from future 3MF extensions.
    """
    out = tmp_path / "x.3mf"
    write_3mf_package(
        out,
        parts=(
            (
                "top",
                1,
                Tessellation(
                    vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                    triangles=((0, 1, 2),),
                ),
            ),
        ),
        metadata={
            "hf:SourceSHA": "deadbeef",
            "hf:ComponentRevision": "pid4516@v2026-08-15",
            "hf:PrinterProfile": "ender3_pro_0.4",
            "hf:Filament": "Prusament_Galaxy_Black_ASIN_B0BJXPNG4M",
            "hf:NozzleDiameterMM": "0.4",
            "hf:SnapClearanceMM": "0.3",
            "hf:SlicerTimeSeconds": "3600",
            "hf:GeometryEvidenceSHA256": "abc123",
            "hf:PrinterEvidenceSHA256": "def456",
        },
    )
    entries = read_3mf_package(out)
    _objects, metadata = parse_model_xml_entries(entries["3D/3dmodel.model"])
    assert metadata["hf:SourceSHA"] == "deadbeef"
    assert metadata["hf:PrinterProfile"] == "ender3_pro_0.4"
    assert metadata["hf:GeometryEvidenceSHA256"] == "abc123"
    assert metadata["hf:PrinterEvidenceSHA256"] == "def456"


def test_read_3mf_package_rejects_non_zip(tmp_path: Path):
    """``read_3mf_package`` raises when the file is not a valid ZIP."""
    fake = tmp_path / "fake.3mf"
    fake.write_text("not a zip")
    with pytest.raises(zipfile.BadZipFile):
        read_3mf_package(fake)


def test_3mf_part_geometry_real_cube(tmp_path: Path):
    """End-to-end: a real cube part goes through STL → 3MF."""
    part = _box_part(10.0)
    tess = tessellate_part_to_vertices_and_triangles(part)
    out = tmp_path / "cube.3mf"
    write_3mf_package(
        out,
        parts=(("cube", 1, tess),),
    )
    entries = read_3mf_package(out)
    objects, _ = parse_model_xml_entries(entries["3D/3dmodel.model"])
    assert len(objects) == 1
    name, _obj_id, tri_count = objects[0]
    assert name == "cube"
    assert tri_count == 12  # a cube has 12 triangles (2 per face × 6)


# ---------------------------------------------------------------------------
# OPC / 3MF Core spec validity — exact requirements
# ---------------------------------------------------------------------------
def test_3mf_content_types_has_override_for_model_file(tmp_path: Path):
    """[Content_Types].xml must declare an ``Override`` for the 3D model.

    The 3MF Core spec (§6.4 Content_Types) requires the model part's
    content type to be the absolute form
    ``application/vnd.ms-package.3dmanufacturing-3dmodel+xml``.
    Without the ``Override``, strict consumers reject the package.
    """
    out = tmp_path / "x.3mf"
    write_3mf_package(
        out,
        parts=(
            (
                "top",
                1,
                Tessellation(
                    vertices=((0.0, 0.0, 0.0),),
                    triangles=(),
                ),
            ),
        ),
    )
    entries = read_3mf_package(out)
    ct = entries["[Content_Types].xml"].decode("utf-8")
    # The Override element must be present and point at the 3D
    # model file with the spec-correct content type.
    assert 'PartName="/3D/3dmodel.model"' in ct
    assert 'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"' in ct
    # And the Override must be inside the ``Types`` root.
    assert ct.startswith('<?xml version="1.0"')
    # Default extensions must still be present (for ``rels`` and
    # ``xml``).
    assert 'Extension="rels"' in ct
    assert 'Extension="xml"' in ct


def test_3mf_relationship_target_is_absolute_per_opc(tmp_path: Path):
    """``_rels/.rels`` must use the **absolute** Target (per OPC §6.3).

    Strict 3MF consumers reject relative targets for the default
    package's relationship. The Target must be
    ``/3D/3dmodel.model`` (with the leading slash).
    """
    out = tmp_path / "x.3mf"
    write_3mf_package(
        out,
        parts=(
            (
                "top",
                1,
                Tessellation(
                    vertices=((0.0, 0.0, 0.0),),
                    triangles=(),
                ),
            ),
        ),
    )
    entries = read_3mf_package(out)
    rels = entries["_rels/.rels"].decode("utf-8")
    # Absolute path: starts with a leading slash.
    assert 'Target="/3D/3dmodel.model"' in rels
    # The relationship type must be the 3MF model relationship.
    assert 'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"' in rels


def test_3mf_metadata_is_direct_child_of_model(tmp_path: Path):
    """Production metadata is a direct ``<metadata name="...">`` child of ``<model>``.

    The 3MF Core spec forbids a ``<Metadata>`` wrapper around the
    metadata elements. Each metadata element is a sibling of
    ``<resources>`` and ``<build>``.
    """
    out = tmp_path / "x.3mf"
    write_3mf_package(
        out,
        parts=(
            (
                "top",
                1,
                Tessellation(
                    vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                    triangles=((0, 1, 2),),
                ),
            ),
        ),
        metadata={
            "hf:SourceSHA": "deadbeef",
            "hf:ComponentRevision": "v1",
            "hf:PrinterProfile": "ender3_pro_0.4",
        },
    )
    entries = read_3mf_package(out)
    model = _parse_xml(entries["3D/3dmodel.model"])
    # Each namespaced ``<hf:SourceSHA>`` must be a direct child of
    # ``<model>`` and carry the workshop namespace prefix.
    # Collect Clark-notation tags + local names.
    direct = {child.tag for child in model}
    # The default-namespace ``<metadata>`` element is the
    # standard one (well-known names). Custom namespaced
    # metadata is emitted under the ``hf:`` prefix and
    # contributes Clark-notation tags of the form
    # ``{ns}metadata``.
    assert any(tag.split("}")[-1] == "metadata" for tag in direct)
    # Round-trip through ``parse_model_xml_entries`` — the
    # parser re-emits ``prefix:local`` keys.
    _objects, metadata = parse_model_xml_entries(entries["3D/3dmodel.model"])
    assert metadata["hf:SourceSHA"] == "deadbeef"
    assert metadata["hf:ComponentRevision"] == "v1"
    assert metadata["hf:PrinterProfile"] == "ender3_pro_0.4"
    # The classic ``<Metadata>`` wrapper must NOT be present.
    assert not any(
        child.tag == f"{_ET_3MF}Metadata"  # capital M
        for child in model
    )


def test_3mf_namespace_prefix_is_declared_on_model(tmp_path: Path):
    """The model root element declares the custom ``xmlns:hf``.

    Without the namespace declaration the namespaced metadata
    elements are invalid. The 3MF Core spec requires the
    declaration to be on the ``<model>`` element (or any of its
    ancestors).
    """
    out = tmp_path / "x.3mf"
    write_3mf_package(
        out,
        parts=(
            (
                "top",
                1,
                Tessellation(
                    vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                    triangles=((0, 1, 2),),
                ),
            ),
        ),
        metadata={"hf:SourceSHA": "abc"},
    )
    entries = read_3mf_package(out)
    model_xml = entries["3D/3dmodel.model"]
    # ElementTree's ``attrib`` accessor strips ``xmlns:*``
    # declarations — read the raw bytes to assert the literal
    # namespace declaration is present on the model element.
    assert b'xmlns:hf="https://hackerspaceshop.example/hf-il3-2026/metadata#"' in model_xml
    # Round-trip via the parser still surfaces the namespaced key.
    _, metadata = parse_model_xml_entries(model_xml)
    assert metadata["hf:SourceSHA"] == "abc"
