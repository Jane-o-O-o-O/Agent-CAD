"""Independent DXF parsing of real exact-kernel engineering drawings."""
import json
import math
import xml.etree.ElementTree as ET

import ezdxf
import pytest
from build123d import Align, Axis, Box, Cylinder, Plane, Pos, Sphere, Torus, Rectangle
from app.infrastructure.cad_v2.drawings import export_drawings
from app.domain.cad_v2.errors import ExportError


def plate(diameter=6, translation=(0, 0, 0)):
    shape = Box(80, 50, 8, align=Align.MIN)
    for x, y in ((10, 10), (70, 10), (10, 40), (70, 40)):
        shape -= Pos(x, y, 0)*Cylinder(diameter/2, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return shape.translate(translation)


def tagged(document, view, layer=None):
    return [entity for entity in document.modelspace()
            if entity.has_xdata("CAD_DRAWING") and entity.get_xdata("CAD_DRAWING")[0].value == view
            and (layer is None or entity.dxf.layer == layer)]


def dimension_values(document, role):
    return [e.get_measurement() for e in document.modelspace().query("DIMENSION")
            if e.get_xdata("CAD_DRAWING")[1].value == role]


def test_plate_three_views_units_native_dimensions_and_hidden_bores(tmp_path):
    output = export_drawings(plate(), tmp_path, revision_id="plate-r1")
    doc = ezdxf.readfile(output["artifacts"]["dxf"])
    assert not doc.audit().has_errors
    assert doc.header["$INSUNITS"] == ezdxf.units.MM
    assert doc.header["$MEASUREMENT"] == 1
    assert {"VISIBLE", "HIDDEN", "CENTER", "DIMENSIONS"} <= {layer.dxf.name for layer in doc.layers}
    assert doc.layers.get("HIDDEN").dxf.linetype == "CAD_HIDDEN"
    assert doc.layers.get("CENTER").dxf.linetype == "CAD_CENTER"
    report = output["report"]
    assert report["method"] == "exact_brep_hlr"
    assert report["projection"] == "third_angle"
    assert report["bbox"]["size"] == pytest.approx([80, 50, 8])
    views = {v["name"]: v for v in report["views"]}
    assert views["top"]["offset"][1] > 8
    assert views["right"]["offset"][0] > 80
    for name, size in (("front", [80, 8]), ("top", [80, 50]), ("right", [50, 8])):
        assert views[name]["bounds"]["size"] == pytest.approx(size)
    circles = [e for e in tagged(doc, "top", "VISIBLE") if e.dxftype() == "CIRCLE"]
    ox, oy = views["top"]["offset"]
    assert sorted((round(e.dxf.center.x-ox, 6), round(e.dxf.center.y-oy, 6), e.dxf.radius) for e in circles) == [
        (10., 10., 3.), (10., 40., 3.), (70., 10., 3.), (70., 40., 3.)]
    # Actual hidden bore walls, with coincident outer-box edges removed.
    hidden = tagged(doc, "front", "HIDDEN")
    assert len(hidden) == 4
    assert sorted(round(e.dxf.start.x, 6) for e in hidden) == [7, 13, 67, 73]
    assert all(abs(e.dxf.end.y-e.dxf.start.y) == pytest.approx(8) for e in hidden)
    assert not tagged(doc, "top", "HIDDEN")
    assert dimension_values(doc, "overall_width") == pytest.approx([80])
    assert dimension_values(doc, "overall_depth") == pytest.approx([50])
    assert dimension_values(doc, "overall_height") == pytest.approx([8])
    assert dimension_values(doc, "hole_diameter") == pytest.approx([6])
    assert sorted(dimension_values(doc, "hole_x")) == pytest.approx([10, 70])
    assert sorted(dimension_values(doc, "hole_y")) == pytest.approx([10, 40])
    assert all(e.dxf.dimstyle == "CAD_MM" for e in doc.modelspace().query("DIMENSION"))
    assert doc.dimstyles.get("CAD_MM").dxf.dimlfac == 1
    assert len(tagged(doc, "top", "CENTER")) == 8
    svg = ET.parse(output["artifacts"]["svg"]).getroot()
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert json.loads(svg.find("svg:metadata", ns).text)["revision_id"] == "plate-r1"
    assert len(svg.findall(".//svg:circle", ns)) == 4
    assert {g.attrib.get("data-view") for g in svg.findall("svg:g", ns)} == {"front", "top", "right"}


def test_asymmetric_stepped_bracket_is_three_different_projections(tmp_path):
    shape = Box(60, 40, 6, align=Align.MIN)+Pos(0, 32, 6)*Box(20, 8, 24, align=Align.MIN)
    result = export_drawings(shape, tmp_path)
    doc = ezdxf.readfile(result["artifacts"]["dxf"])
    views = {v["name"]: v for v in result["report"]["views"]}
    signatures = []
    for name in ("front", "top", "right"):
        ox, oy = views[name]["offset"]
        edges = tagged(doc, name, "VISIBLE")
        signatures.append({tuple(sorted(((round(e.dxf.start.x-ox, 6), round(e.dxf.start.y-oy, 6)),
                                        (round(e.dxf.end.x-ox, 6), round(e.dxf.end.y-oy, 6))))) for e in edges})
    assert signatures[0] != signatures[1] != signatures[2] != signatures[0]
    # The front step is 20 mm wide at z=30; the right step is 8 mm deep.
    assert ((0., 30.), (20., 30.)) in signatures[0]
    assert ((32., 30.), (40., 30.)) in signatures[2]
    assert dimension_values(doc, "overall_width") == pytest.approx([60])
    assert dimension_values(doc, "overall_depth") == pytest.approx([40])
    assert dimension_values(doc, "overall_height") == pytest.approx([30])


def test_translation_keeps_dimensions_relative_to_measured_bbox(tmp_path):
    result = export_drawings(plate(10, (123, -57, 4)), tmp_path)
    doc = ezdxf.readfile(result["artifacts"]["dxf"])
    assert result["report"]["bbox"]["min"] == pytest.approx([123, -57, 4])
    assert dimension_values(doc, "hole_diameter") == pytest.approx([10])
    assert sorted(dimension_values(doc, "hole_x")) == pytest.approx([10, 70])
    assert sorted(dimension_values(doc, "hole_y")) == pytest.approx([10, 40])
    assert sorted(round(h["axis_origin"][0], 6) for h in result["report"]["holes"]) == [133, 133, 193, 193]


def test_zero_rotation_box_has_no_fake_hidden_or_center_lines(tmp_path):
    result = export_drawings(Box(11, 17, 23, align=Align.MIN), tmp_path)
    doc = ezdxf.readfile(result["artifacts"]["dxf"])
    for name in ("front", "top", "right"):
        assert len(tagged(doc, name, "VISIBLE")) == 4
        assert not tagged(doc, name, "HIDDEN")
        assert not tagged(doc, name, "CENTER")
    assert len(list(doc.modelspace().query("DIMENSION"))) == 3
    assert not result["report"]["holes"]


def test_oblique_cylinder_retains_exact_ellipses_and_declares_svg_sampling(tmp_path):
    result = export_drawings(Cylinder(7, 20).rotate(Axis.X, 32), tmp_path)
    doc = ezdxf.readfile(result["artifacts"]["dxf"])
    ellipses = [e for e in tagged(doc, "top", "VISIBLE") if e.dxftype() == "ELLIPSE"]
    assert ellipses
    assert all(e.dxf.major_axis.magnitude == pytest.approx(7) for e in ellipses)
    assert all(e.dxf.ratio == pytest.approx(math.cos(math.radians(32))) for e in ellipses)
    assert any(v["svg_approximated_curves"] for v in result["report"]["views"])
    assert result["report"]["svg_curve_deflection_mm"] == .01
    assert not result["report"]["holes"]


def test_side_hole_is_dimensioned_in_front_from_final_geometry(tmp_path):
    block = Box(30, 12, 20, align=Align.MIN)
    cutter = Plane(origin=(10, -1, 8), z_dir=(0, 1, 0))*Cylinder(2, 14, align=(Align.CENTER, Align.CENTER, Align.MIN))
    result = export_drawings(block-cutter, tmp_path)
    doc = ezdxf.readfile(result["artifacts"]["dxf"])
    circles = [e for e in tagged(doc, "front", "VISIBLE") if e.dxftype() == "CIRCLE"]
    assert len(circles) == 1
    assert tuple(circles[0].dxf.center) == pytest.approx((10, 8, 0))
    dimensions = [d for d in result["report"]["dimensions"] if d["label"] == "hole_diameter"]
    assert len(dimensions) == 1 and dimensions[0]["view"] == "front"
    assert dimension_values(doc, "hole_diameter") == pytest.approx([4])
    assert dimension_values(doc, "hole_x") == pytest.approx([10])
    assert dimension_values(doc, "hole_y") == pytest.approx([8])
    # The coordinate extension point is the measured axis, not a invented point on the edge.
    assert next(d for d in result["report"]["dimensions"] if d["label"] == "hole_x")["p2"] == pytest.approx((10, 8))


def test_sphere_uses_exact_projected_silhouette_circles_arcs(tmp_path):
    result = export_drawings(Sphere(8), tmp_path)
    doc = ezdxf.readfile(result["artifacts"]["dxf"])
    for name in ("front", "top", "right"):
        edges = tagged(doc, name, "VISIBLE")
        assert edges and all(e.dxftype() in {"ARC", "CIRCLE"} for e in edges)
        assert all(e.dxf.radius == pytest.approx(8) for e in edges)
    assert not result["report"]["holes"]


def test_svg_title_and_revision_are_escaped_and_report_is_deterministic(tmp_path):
    shape = Box(10, 20, 30, align=Align.MIN)
    first = export_drawings(shape, tmp_path/"a", title='<script>alert("x")</script>', revision_id='rev<&>')
    second = export_drawings(shape, tmp_path/"b", title='<script>alert("x")</script>', revision_id='rev<&>')
    assert first["report"] == second["report"]
    assert first["artifacts"]["svg"].read_bytes() == second["artifacts"]["svg"].read_bytes()
    root = ET.parse(first["artifacts"]["svg"]).getroot()
    assert not root.findall('.//{http://www.w3.org/2000/svg}script')
    metadata = json.loads(root.find('{http://www.w3.org/2000/svg}metadata').text)
    assert metadata["revision_id"] == 'rev<&>'


def test_curved_hlr_nurbs_survive_independent_dxf_read(tmp_path):
    result = export_drawings(Torus(10, 3).rotate(Axis.X, 33), tmp_path)
    document = ezdxf.readfile(result["artifacts"]["dxf"])
    splines = list(document.modelspace().query("SPLINE"))
    assert splines and not document.audit().has_errors
    assert not list(document.modelspace().query("LWPOLYLINE"))
    for spline in splines:
        tool = spline.construction_tool()
        assert len(spline.control_points) >= spline.dxf.degree+1
        assert all(math.isfinite(c) for p in tool.approximate(12) for c in p)
    assert result["report"]["dxf_geometry"] == "analytic_curves_and_nurbs"


def test_flat_shape_is_rejected_instead_of_fabricating_three_views(tmp_path):
    with pytest.raises(ExportError, match="valid final solid"):
        export_drawings(Rectangle(10, 20), tmp_path)
    assert not (tmp_path/"drawing.dxf").exists()
