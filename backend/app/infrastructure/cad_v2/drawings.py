"""Measured third-angle drawings from the final OpenCascade B-Rep.

Analytic solids use exact HLR curves; freeform surfaces use a fresh bounded
OCCT tessellation and polygonal HLR. Dimensions always measure the B-Rep.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import html
import json
import math
from pathlib import Path
from typing import Any

import ezdxf
from build123d import Compound, GeomType
from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepLib import BRepLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.BRepTools import BRepTools
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.Geom import Geom_TrimmedCurve
from OCP.GeomConvert import GeomConvert
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape, HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
from OCP.TopLoc import TopLoc_Location
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from app.domain.cad_v2.errors import ExportError

TOLERANCE = 1e-6
SVG_DEFLECTION_MM = 0.01
MAX_CURVE_POINTS = 8192
FREEFORM_DEFLECTION_MM = 0.01
FREEFORM_ANGULAR_DEFLECTION = 0.15
MAX_DRAWING_TRIANGLES = 100000
VIEWS = (
    ("front", (0, -1, 0), (1, 0, 0), (0, 0, 1)),
    ("top", (0, 0, 1), (1, 0, 0), (0, 1, 0)),
    ("right", (1, 0, 0), (0, 1, 0), (0, 0, 1)),
)


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _xy(point):
    return (float(point.X()), float(point.Y()))


def _bbox(shape):
    from .backend import _has_freeform_geometry, _precise_bounds
    if _has_freeform_geometry(shape):
        return _precise_bounds(shape)
    bounds = Bnd_Box()
    # Never use triangulation for dimension measurements.
    BRepBndLib.AddOptimal_s(shape.wrapped, bounds, False, False)
    values = bounds.Get()
    lo, hi = list(values[:3]), list(values[3:])
    return {"min": lo, "max": hi, "size": [b-a for a, b in zip(lo, hi)]}


def _samples(adaptor):
    sampling = GCPnts_QuasiUniformDeflection(adaptor, SVG_DEFLECTION_MM)
    if not sampling.IsDone() or sampling.NbPoints() > MAX_CURVE_POINTS:
        raise ExportError("Projected curve exceeds the bounded SVG sampling budget")
    return [_xy(sampling.Value(i)) for i in range(1, sampling.NbPoints()+1)]


def _primitive(edge):
    curve = BRepAdaptor_Curve(edge.wrapped)
    first, last = curve.FirstParameter(), curve.LastParameter()
    start, end = _xy(curve.Value(first)), _xy(curve.Value(last))
    if edge.geom_type == GeomType.LINE:
        return {"type": "line", "points": [start, end]}
    if edge.geom_type == GeomType.CIRCLE:
        circle = curve.Circle()
        center, radius = _xy(circle.Location()), float(circle.Radius())
        if abs(last-first) >= math.tau-TOLERANCE:
            return {"type": "circle", "center": center, "radius": radius}
        a = math.degrees(math.atan2(start[1]-center[1], start[0]-center[0])) % 360
        b = math.degrees(math.atan2(end[1]-center[1], end[0]-center[0])) % 360
        if circle.Axis().Direction().Z() < 0:
            a, b = b, a
        return {"type": "arc", "center": center, "radius": radius, "start": a, "end": b}
    points = _samples(curve)
    if edge.geom_type == GeomType.ELLIPSE:
        ellipse = curve.Ellipse()
        major = ellipse.MajorRadius()
        axis = ellipse.XAxis().Direction()
        if ellipse.Axis().Direction().Z() < 0:
            first, last = -last, -first
        return {"type": "ellipse", "center": _xy(ellipse.Location()),
                "major_axis": (major*axis.X(), major*axis.Y()),
                "ratio": ellipse.MinorRadius()/major,
                "start_param": first, "end_param": last, "points": points}
    # Preserve kernel NURBS degree, knots and rational weights. In particular,
    # do not use build123d.to_splines(), which approximates to degree three.
    trimmed = Geom_TrimmedCurve(curve.Curve().Curve(), first, last)
    spline = GeomConvert.CurveToBSplineCurve_s(trimmed)
    spline.Transform(edge.location.wrapped.Transformation())
    if spline.IsPeriodic():
        spline.SetNotPeriodic()
    poles = [_xy(spline.Pole(i)) for i in range(1, spline.NbPoles()+1)]
    # Edge-on circular rims become collinear NURBS in OCCT HLR. Collapse only
    # when ALL control points are collinear and within the endpoint interval.
    if math.dist(start, end) > TOLERANCE:
        dx, dy = end[0]-start[0], end[1]-start[1]
        length2 = dx*dx+dy*dy
        if all(abs((p[0]-start[0])*dy-(p[1]-start[1])*dx) <= TOLERANCE*math.sqrt(length2)
               and -TOLERANCE <= _dot((p[0]-start[0], p[1]-start[1]), (dx, dy))/length2 <= 1+TOLERANCE
               for p in poles):
            return {"type": "line", "points": [start, end]}
    return {"type": "spline", "poles": poles, "degree": spline.Degree(),
            "knots": list(spline.KnotSequence()),
            "weights": [spline.Weight(i) for i in range(1, spline.NbPoles()+1)],
            "points": points}


def _key(item):
    kind = item["type"]
    if kind == "line":
        data = sorted(tuple(round(n, 6) for n in p) for p in item["points"])
    elif kind in {"circle", "arc"}:
        data = [*(round(n, 6) for n in item["center"]), round(item["radius"], 6)]
        if kind == "arc":
            data += [round(item["start"], 6), round(item["end"], 6)]
    else:
        data = [tuple(round(n, 6) for n in p) for p in item["points"]]
        data = min(data, list(reversed(data)))
    return kind, repr(data)


def _subtract_line(line, blockers):
    """Visible strokes take precedence over coincident hidden edges."""
    a, b = line["points"]
    dx, dy = b[0]-a[0], b[1]-a[1]
    length2 = dx*dx+dy*dy
    if length2 < TOLERANCE**2:
        return []
    length = math.sqrt(length2)
    intervals = [(0., 1.)]
    for blocker in blockers:
        if blocker["type"] != "line":
            continue
        c, d = blocker["points"]
        if any(abs((p[0]-a[0])*dy-(p[1]-a[1])*dx) > TOLERANCE*length for p in (c, d)):
            continue
        lo, hi = sorted(_dot((p[0]-a[0], p[1]-a[1]), (dx, dy))/length2 for p in (c, d))
        remaining = []
        for left, right in intervals:
            if hi <= left or lo >= right:
                remaining.append((left, right))
            else:
                if lo > left:
                    remaining.append((left, min(lo, right)))
                if hi < right:
                    remaining.append((max(hi, left), right))
        intervals = remaining
    return [{"type": "line", "points": [(a[0]+lo*dx, a[1]+lo*dy), (a[0]+hi*dx, a[1]+hi*dy)]}
            for lo, hi in intervals if (hi-lo)*length > TOLERANCE]


def _clean(items, visible=()):
    result, seen = [], {_key(p) for p in visible}
    for item in items:
        if _key(item) in seen:
            continue
        pieces = _subtract_line(item, [*visible, *result]) if item["type"] == "line" else [item]
        for piece in pieces:
            key = _key(piece)
            if key not in seen:
                seen.add(key)
                result.append(piece)
    return result


def _freeform_mesh(shape):
    projected_shape = deepcopy(shape)
    BRepTools.Clean_s(projected_shape.wrapped)
    mesh = BRepMesh_IncrementalMesh(projected_shape.wrapped,FREEFORM_DEFLECTION_MM,False,FREEFORM_ANGULAR_DEFLECTION,False)
    if not mesh.IsDone():
        raise ExportError("Freeform drawing tessellation failed")
    count = 0
    for face in projected_shape.faces():
        triangulation = BRep_Tool.Triangulation_s(face.wrapped,TopLoc_Location())
        if triangulation is None:
            raise ExportError("Freeform drawing has an unmeshed face")
        count += triangulation.NbTriangles()
        if count > MAX_DRAWING_TRIANGLES:
            raise ExportError("Freeform drawing exceeds its triangle budget")
    return projected_shape,count


def _project(shape, name, normal, x_axis, y_axis, *, polygonal=False):
    projector = HLRAlgo_Projector(gp_Ax2(gp_Pnt(), gp_Dir(*normal), gp_Dir(*x_axis)))
    if polygonal:
        algo = HLRBRep_PolyAlgo()
        algo.Load(shape.wrapped)
        algo.Projector(projector)
        algo.Update()
        extraction = HLRBRep_PolyHLRToShape()
        extraction.Update(algo)
        extraction.Hide()
    else:
        algo = HLRBRep_Algo()
        algo.Add(shape.wrapped,0)
        algo.Projector(projector)
        algo.Update()
        algo.Hide()
        extraction = HLRBRep_HLRToShape(algo)
    layers = {}
    for layer, methods in (("visible", ("VCompound", "OutLineVCompound", "Rg1LineVCompound")),
                           ("hidden", ("HCompound", "OutLineHCompound", "Rg1LineHCompound"))):
        items = []
        for method in methods:
            projected = getattr(extraction, method)()
            if projected.IsNull():
                continue
            if not BRepLib.BuildCurves3d_s(projected):
                raise ExportError(f"Cannot reconstruct {name} projected curves")
            items.extend(_primitive(edge) for edge in Compound(projected).edges())
        layers[layer] = _clean(items, layers.get("visible", ()) if layer == "hidden" else ())
    if not layers["visible"]:
        raise ExportError(f"Projection returned no visible edges for {name}")
    return {"name": name, "normal": normal, "x_axis": x_axis, "y_axis": y_axis,
            **layers, "center": [], "dimensions": []}


def _holes(shape):
    """Recognize complete inward cylindrical walls in the FINAL shape only."""
    found = []
    for face in shape.faces().filter_by(GeomType.CYLINDER):
        adaptor = BRepAdaptor_Surface(face.wrapped)
        if abs(adaptor.LastUParameter()-adaptor.FirstUParameter()) < math.tau-1e-5:
            continue
        cylinder = adaptor.Cylinder()
        origin = (cylinder.Location().X(), cylinder.Location().Y(), cylinder.Location().Z())
        direction = (cylinder.Axis().Direction().X(), cylinder.Axis().Direction().Y(), cylinder.Axis().Direction().Z())
        point, normal = tuple(face.position_at(.25, .5)), tuple(face.normal_at(.25, .5))
        offset = [p-o for p, o in zip(point, origin)]
        axial = _dot(offset, direction)
        radial = [offset[i]-axial*direction[i] for i in range(3)]
        if _dot(radial, normal) >= 0:
            continue
        direction = tuple(-n for n in direction) if next((n for n in direction if abs(n)>TOLERANCE), 1)<0 else direction
        axis_origin = [origin[i]-_dot(origin, direction)*direction[i] for i in range(3)]
        radius = float(cylinder.Radius())
        if any(math.dist(axis_origin, h["axis_origin"])<TOLERANCE and math.dist(direction, h["direction"])<TOLERANCE
               and abs(radius-h["radius"])<TOLERANCE for h in found):
            continue
        found.append({"axis_origin": axis_origin, "direction": list(direction), "radius": radius,
                      "diameter": 2*radius, "source": "final_brep_inward_cylinder"})
    return sorted(found, key=lambda h: (*h["axis_origin"], h["radius"]))


def _dimension(view, kind, p1, p2, base, label, source, **extra):
    value = math.dist(p1, p2) if kind == "diameter" else abs(p2[0]-p1[0]) if kind == "horizontal" else abs(p2[1]-p1[1])
    if value <= TOLERANCE:
        return
    view["dimensions"].append({"kind": kind, "p1": p1, "p2": p2, "base": base,
                               "value_mm": value, "label": label, "source": source, **extra})


def _layout(views, bbox, holes):
    width, depth, height = bbox["size"]
    # All geometry stays 1:1 in mm; only drawing annotation size adapts.
    text_height = max(width, depth, height)*.025
    text_height = max(.8, min(5., text_height))
    lane = text_height*4.5
    max_coordinates = max((len({round(_dot(h["axis_origin"], axis), 6) for h in holes})
                           for _, _, x_axis, y_axis in VIEWS for axis in (x_axis, y_axis)), default=0)
    gap = max(lane*(max_coordinates+5), max(width, depth, height)*.2)
    offsets = {"front": (0., 0.), "top": (0., height+gap), "right": (width+gap, 0.)}
    by_name = {v["name"]: v for v in views}
    for view in views:
        lo = (_dot(bbox["min"], view["x_axis"]), _dot(bbox["min"], view["y_axis"]))
        hi = (_dot(bbox["max"], view["x_axis"]), _dot(bbox["max"], view["y_axis"]))
        anchor = offsets[view["name"]]
        view.update(bounds={"min": lo, "max": hi, "size": (hi[0]-lo[0], hi[1]-lo[1])},
                    offset=(anchor[0]-lo[0], anchor[1]-lo[1]), anchor=anchor)
    front, top = by_name["front"], by_name["top"]
    x, z = front["bounds"]["min"]
    _dimension(front, "horizontal", (x, z), (x+width, z), (x, z-lane), "overall_width", "final_brep_bbox")
    _dimension(front, "vertical", (x, z), (x, z+height), (x-lane, z), "overall_height", "final_brep_bbox")
    x, y = top["bounds"]["min"]
    _dimension(top, "vertical", (x, y), (x, y+depth), (x-lane, y), "overall_depth", "final_brep_bbox")
    for view in views:
        lx, ly = view["bounds"]["min"]
        hx, hy = view["bounds"]["max"]
        selected = []
        for index, hole in enumerate(holes):
            normal_alignment = abs(_dot(hole["direction"], view["normal"]))
            center = (_dot(hole["axis_origin"], view["x_axis"]), _dot(hole["axis_origin"], view["y_axis"]))
            if normal_alignment > 1-TOLERANCE:
                # A cylindrical cavity must have an actual visible circular rim in
                # this view before assigning a diameter callout to that rim.
                if not any(p["type"] == "circle" and math.dist(p["center"], center)<TOLERANCE
                           and abs(p["radius"]-hole["radius"])<TOLERANCE for p in view["visible"]):
                    continue
                r = hole["radius"]+text_height
                view["center"].extend([{"type": "line", "points": [(center[0]-r, center[1]), (center[0]+r, center[1])]},
                                       {"type": "line", "points": [(center[0], center[1]-r), (center[0], center[1]+r)]}])
                selected.append((index, hole, center))
            elif normal_alignment < TOLERANCE:
                axis_2d = (_dot(hole["direction"], view["x_axis"]), _dot(hole["direction"], view["y_axis"]))
                if abs(axis_2d[0]) > 1-TOLERANCE:
                    view["center"].append({"type": "line", "points": [(lx-text_height, center[1]), (hx+text_height, center[1])]})
                elif abs(axis_2d[1]) > 1-TOLERANCE:
                    view["center"].append({"type": "line", "points": [(center[0], ly-text_height), (center[0], hy+text_height)]})
        # One diameter callout per visible diameter group; no unsupported claim
        # that these are through holes, or that their depths are equal.
        groups = {}
        for index, hole, center in selected:
            groups.setdefault(round(hole["diameter"], 6), []).append((index, hole, center))
        for group_no, group in enumerate(groups.values()):
            index, hole, center = group[-1]
            r = hole["radius"]
            p1, p2 = (center[0]-r, center[1]), (center[0]+r, center[1])
            base = (hx+lane, hy-lane*group_no)
            _dimension(view, "diameter", p1, p2, base, "hole_diameter", "final_brep_inward_cylinder",
                       center=center, radius=r, count=len(group), hole_indices=[g[0] for g in group])
        # Baseline coordinates of the measured hole axes. Each distinct location
        # is annotated once, avoiding duplicate labels in regular hole patterns.
        for axis in (0, 1):
            coordinates = sorted({round(c[axis], 8) for _, _, c in selected})
            for slot, coordinate in enumerate(coordinates):
                center = next(c for _, _, c in selected if abs(c[axis]-coordinate)<TOLERANCE)
                if axis == 0:
                    occupied = 1 if view["name"] == "front" else 0
                    _dimension(view, "horizontal", (lx, center[1]), center, (lx, ly-lane*(slot+1+occupied)),
                               "hole_x", "final_brep_cylinder_axis")
                else:
                    _dimension(view, "vertical", (center[0], ly), center, (hx+lane*(slot+2), ly),
                               "hole_y", "final_brep_cylinder_axis")
        view["center"] = _clean(view["center"])
    return text_height, lane


def _move(p, offset):
    return (p[0]+offset[0], p[1]+offset[1])


def _tag(entity, view, role):
    entity.set_xdata("CAD_DRAWING", [(1000, view), (1000, role)])
    return entity


def _write_dxf(views, path, title, revision_id, text_height, projection_note=None):
    doc = ezdxf.new("R2013", setup=True, units=ezdxf.units.MM)
    doc.header["$MEASUREMENT"] = 1
    doc.appids.add("CAD_DRAWING")
    if "CAD_HIDDEN" not in doc.linetypes:
        doc.linetypes.add("CAD_HIDDEN", pattern=[text_height*2, text_height*1.2, -text_height*.8])
        doc.linetypes.add("CAD_CENTER", pattern=[text_height*4, text_height*2.4, -text_height*.6, text_height*.4, -text_height*.6])
    for name, color, weight, line_type in (("VISIBLE",7,35,"Continuous"),("HIDDEN",8,18,"CAD_HIDDEN"),
            ("CENTER",4,13,"CAD_CENTER"),("DIMENSIONS",7,18,"Continuous"),("ANNOTATIONS",7,18,"Continuous")):
        doc.layers.add(name, color=color, lineweight=weight, linetype=line_type)
    ms = doc.modelspace()
    style = {"dimtxt":text_height,"dimasz":text_height*.65,"dimexo":text_height*.4,"dimexe":text_height*.6,
             "dimgap":text_height*.3,"dimlfac":1.,"dimdec":3,"dimzin":8,"dimtad":1,"dimscale":1.}
    doc.dimstyles.new("CAD_MM", dxfattribs=style)
    for view in views:
        offset = view["offset"]
        for layer in ("hidden", "visible", "center"):
            attrs = {"layer":layer.upper()}
            for p in view[layer]:
                kind = p["type"]
                if kind == "line":
                    entity = ms.add_line(*[_move(point, offset) for point in p["points"]], dxfattribs=attrs)
                elif kind == "circle":
                    entity = ms.add_circle(_move(p["center"],offset),p["radius"],dxfattribs=attrs)
                elif kind == "arc":
                    entity = ms.add_arc(_move(p["center"],offset),p["radius"],p["start"],p["end"],dxfattribs=attrs)
                elif kind == "ellipse":
                    entity = ms.add_ellipse(_move(p["center"],offset),p["major_axis"],p["ratio"],p["start_param"],p["end_param"],dxfattribs=attrs)
                else:
                    construction = ezdxf.math.BSpline([_move(point,offset) for point in p["poles"]],
                                                      order=p["degree"]+1, knots=p["knots"], weights=p["weights"])
                    entity = ms.add_spline(dxfattribs=attrs).apply_construction_tool(construction)
                _tag(entity,view["name"],layer)
        for d in view["dimensions"]:
            attrs = {"layer":"DIMENSIONS"}
            if d["kind"] == "diameter":
                dimension = ms.add_diameter_dim(center=_move(d["center"],offset),radius=d["radius"],
                    location=_move(d["base"],offset),text=f'{d["count"]}x <>' if d["count"]>1 else "<>",
                    dimstyle="CAD_MM",override={"dimtad":0},dxfattribs=attrs)
            else:
                dimension = ms.add_linear_dim(base=_move(d["base"],offset),p1=_move(d["p1"],offset),p2=_move(d["p2"],offset),
                    angle=0 if d["kind"]=="horizontal" else 90,dimstyle="CAD_MM",dxfattribs=attrs)
            dimension.render()
            _tag(dimension.dimension,view["name"],d["label"])
        label_pos = _move((view["bounds"]["min"][0],view["bounds"]["max"][1]+text_height*2.5),offset)
        _tag(ms.add_text(view["name"].upper(),dxfattribs={"height":text_height,"insert":label_pos,"layer":"ANNOTATIONS"}),view["name"],"view_label")
    annotation_y = min((d["base"][1]+v["offset"][1] for v in views for d in v["dimensions"]), default=0)-text_height*8
    ms.add_text(title[:100], dxfattribs={"height":text_height*1.2,"insert":(0,annotation_y),"layer":"ANNOTATIONS"})
    ms.add_text(f'THIRD-ANGLE | mm | 1:1 | Revision: {revision_id or "current"}',
                dxfattribs={"height":text_height*.8,"insert":(0,annotation_y-text_height*2),"layer":"ANNOTATIONS"})
    if projection_note:
        ms.add_text(projection_note,dxfattribs={"height":text_height*.65,"insert":(0,annotation_y-text_height*3.5),"layer":"ANNOTATIONS"})
    from ezdxf import zoom
    zoom.extents(ms)
    if doc.audit().has_errors:
        raise ExportError("DXF drawing failed structural audit")
    doc.saveas(path)


def _number(n):
    return f"{n:.6f}".rstrip("0").rstrip(".") or "0"


def _write_svg(views, path, title, revision_id, report, text_height, lane):
    """Render the same projection and measured dimensions into an inert SVG."""
    parts, extents = [], []
    def point(p, offset=(0,0)):
        x,y = _move(p,offset)
        extents.append((x,-y))
        return f"{_number(x)},{_number(-y)}"
    def line(p1,p2,offset=(0,0),css="dimension"):
        a,b = point(p1,offset).split(','),point(p2,offset).split(',')
        parts.append(f'<line class="{css}" x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}"/>')
    def text(value,position,size=text_height,anchor="start",offset=(0,0),rotation=0):
        x,y = point(position,offset).split(',')
        transform = f' transform="rotate({rotation} {x} {y})"' if rotation else ""
        parts.append(f'<text x="{x}" y="{y}" font-size="{_number(size)}" text-anchor="{anchor}"{transform}>{html.escape(value)}</text>')
        # Include the text width in the page bounds, not just its origin.
        width = len(value)*size*.65
        px,py = _move(position,offset)
        extents.extend([(px-(width/2 if anchor=="middle" else 0),-py-size),
                        (px+(width/2 if anchor=="middle" else width),-py+size*.3)])
    def arrows(a,b,offset):
        length=math.dist(a,b)
        if length<TOLERANCE:return
        ux,uy=(b[0]-a[0])/length,(b[1]-a[1])/length
        size=min(text_height*.65,length/3)
        for tip,sign in ((a,1),(b,-1)):
            tail=(tip[0]+sign*ux*size,tip[1]+sign*uy*size)
            points=[tip,(tail[0]-uy*size*.3,tail[1]+ux*size*.3),(tail[0]+uy*size*.3,tail[1]-ux*size*.3)]
            parts.append('<polygon class="arrow" points="'+' '.join(point(p,offset) for p in points)+'"/>')
    for view in views:
        offset=view["offset"]
        parts.append(f'<g id="view-{view["name"]}" data-view="{view["name"]}">')
        for layer in ("hidden","visible","center"):
            parts.append(f'<g class="{layer}">')
            for p in view[layer]:
                kind=p["type"]
                if kind=="line":
                    line(*p["points"],offset=offset,css=layer)
                elif kind=="circle":
                    cx,cy=point(p["center"],offset).split(',')
                    r=p["radius"]
                    point((p["center"][0]-r,p["center"][1]-r),offset)
                    point((p["center"][0]+r,p["center"][1]+r),offset)
                    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{_number(r)}"/>')
                elif kind=="arc":
                    c,r=p["center"],p["radius"]
                    a,b=math.radians(p["start"]),math.radians(p["end"])
                    start=point((c[0]+r*math.cos(a),c[1]+r*math.sin(a)),offset)
                    end=point((c[0]+r*math.cos(b),c[1]+r*math.sin(b)),offset)
                    point((c[0]-r,c[1]-r),offset);point((c[0]+r,c[1]+r),offset)
                    large=int((p["end"]-p["start"])%360>180)
                    parts.append(f'<path d="M {start} A {_number(r)} {_number(r)} 0 {large} 0 {end}"/>')
                else:
                    parts.append('<polyline points="'+' '.join(point(v,offset) for v in p["points"])+'"/>')
            parts.append('</g>')
        for d in view["dimensions"]:
            p1,p2,base=d["p1"],d["p2"],d["base"]
            label=_number(round(d["value_mm"],3))
            if d["kind"]=="diameter":
                label=(f'{d["count"]}x ' if d["count"]>1 else '')+'⌀'+label
                center,r=d["center"],d["radius"]
                angle=math.atan2(base[1]-center[1],base[0]-center[0])
                rim=(center[0]+r*math.cos(angle),center[1]+r*math.sin(angle))
                far=(center[0]-r*math.cos(angle),center[1]-r*math.sin(angle))
                line(far,base,offset);arrows(far,rim,offset)
                text(label,(base[0]+text_height*.3,base[1]+text_height*.4),offset=offset)
            elif d["kind"]=="horizontal":
                a,b=(p1[0],base[1]),(p2[0],base[1])
                line(p1,(a[0],a[1]-text_height*.4),offset);line(p2,(b[0],b[1]-text_height*.4),offset)
                line(a,b,offset);arrows(a,b,offset)
                text(label,((a[0]+b[0])/2,a[1]+text_height*.5),anchor="middle",offset=offset)
            else:
                a,b=(base[0],p1[1]),(base[0],p2[1])
                line(p1,(a[0]-text_height*.4,a[1]),offset);line(p2,(b[0]-text_height*.4,b[1]),offset)
                line(a,b,offset);arrows(a,b,offset)
                text(label,(a[0]-text_height*.5,(a[1]+b[1])/2),anchor="middle",offset=offset,rotation=-90)
        text(view["name"].upper(),(view["bounds"]["min"][0],view["bounds"]["max"][1]+text_height*2.5),offset=offset)
        parts.append('</g>')
    annotation_y=min((d["base"][1]+v["offset"][1] for v in views for d in v["dimensions"]),default=0)-text_height*8
    text(title[:100],(0,annotation_y),size=text_height*1.2)
    text(f'THIRD-ANGLE · mm · 1:1 coordinates · Revision: {revision_id or "current"}',(0,annotation_y-text_height*2),size=text_height*.8)
    text('Dimensions: measured overall sizes and supported cylindrical hole axes / diameters.',(0,annotation_y-text_height*3.5),size=text_height*.65)
    if report.get("projection_note"):
        text(report["projection_note"],(0,annotation_y-text_height*5),size=text_height*.65)
    lo_x,lo_y=min(p[0] for p in extents)-lane,min(p[1] for p in extents)-lane
    hi_x,hi_y=max(p[0] for p in extents)+lane,max(p[1] for p in extents)+lane
    width,height=hi_x-lo_x,hi_y-lo_y
    style=f'''svg{{background:#fff}} text{{font-family:Arial,Helvetica,sans-serif;fill:#17212e;stroke:none}} .visible{{fill:none;stroke:#17212e;stroke-width:{text_height*.13}}} .hidden{{fill:none;stroke:#6b7280;stroke-width:{text_height*.07};stroke-dasharray:{text_height*1.2} {text_height*.8}}} .center{{fill:none;stroke:#256b86;stroke-width:{text_height*.06};stroke-dasharray:{text_height*2.4} {text_height*.6} {text_height*.4} {text_height*.6}}} .dimension{{fill:none;stroke:#374151;stroke-width:{text_height*.065}}} .arrow{{fill:#374151;stroke:none}}'''
    content=(f'<svg xmlns="http://www.w3.org/2000/svg" width="{_number(width)}mm" height="{_number(height)}mm" viewBox="{_number(lo_x)} {_number(lo_y)} {_number(width)} {_number(height)}" role="img" aria-labelledby="drawing-title drawing-description">'
             f'<title id="drawing-title">{html.escape(title)}</title><desc id="drawing-description">Measured front, top and right views; third-angle projection; millimetres.</desc>'
             f'<metadata>{html.escape(json.dumps(report,ensure_ascii=True))}</metadata><style>{style}</style>'
             f'<rect x="{_number(lo_x)}" y="{_number(lo_y)}" width="{_number(width)}" height="{_number(height)}" fill="white"/>'+''.join(parts)+'</svg>')
    path.write_text(content,encoding="utf-8")


def export_drawings(shape: Any, output_dir: Path, *, title: str = "Engineering drawing",
                    revision_id: str | None = None) -> dict[str, Any]:
    """Write drawing.dxf + drawing.svg and return paths and measurement evidence.

    The caller supplies the same final validated shape used for STEP/GLB export.
    No design parameters or intermediate feature annotations are trusted here.
    """
    if not shape or not shape.is_valid or not shape.solids():
        raise ExportError("Engineering drawings require a valid final solid")
    output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    try:
        bbox=_bbox(shape)
        if not all(math.isfinite(n) and 0<n<=1e9 for n in bbox["size"]):
            raise ExportError("Invalid drawing extents")
        polygonal = any(face.geom_type in {GeomType.BSPLINE,GeomType.BEZIER} for face in shape.faces())
        projection_shape,triangle_count = _freeform_mesh(shape) if polygonal else (shape,0)
        views=[_project(projection_shape,*spec,polygonal=polygonal) for spec in VIEWS]
        holes=_holes(shape)
        text_height,lane=_layout(views,bbox,holes)
        report={"method":"occt_polygonal_hlr" if polygonal else "exact_brep_hlr","projection":"third_angle","units":"mm","scale":1,
                "revision_id":revision_id,"bbox":bbox,"holes":holes,
                "measurement_source":"final_brep","dxf_geometry":"polygonal_segments" if polygonal else "analytic_curves_and_nurbs",
                "projection_mode":"bounded_tessellation" if polygonal else "exact",
                "approximation_tolerance_mm":FREEFORM_DEFLECTION_MM if polygonal else 0,
                "mesh_angular_deflection":FREEFORM_ANGULAR_DEFLECTION if polygonal else None,
                "mesh_triangles":triangle_count,
                "projection_note":f"Projection: polygonal | mesh deflection target: {FREEFORM_DEFLECTION_MM} mm | dimensions: B-Rep" if polygonal else None,
                "svg_curve_deflection_mm":SVG_DEFLECTION_MM,
                "scope":"Overall dimensions and visible axis-aligned complete cylindrical hole diameters/positions; no tolerance, fit or manufacturing certification",
                "views":[],"dimensions":[]}
        for view in views:
            report["views"].append({key:view[key] for key in ("name","normal","x_axis","y_axis","offset","bounds")}|{
                "entities":{layer:len(view[layer]) for layer in ("visible","hidden","center")},
                "curve_types":dict(Counter(p["type"] for layer in ("visible","hidden") for p in view[layer])),
                "svg_approximated_curves":sum(p["type"] in {"spline","ellipse"} for layer in ("visible","hidden") for p in view[layer])})
            report["dimensions"].extend({"view":view["name"],**d} for d in view["dimensions"])
        paths={"dxf":output_dir/"drawing.dxf","svg":output_dir/"drawing.svg"}
        _write_dxf(views,paths["dxf"],title,revision_id,text_height,report["projection_note"])
        _write_svg(views,paths["svg"],title,revision_id,report,text_height,lane)
        return {"artifacts":paths,"report":report}
    except ExportError:
        raise
    except Exception as exc:
        raise ExportError(f"Engineering drawing export failed: {exc}") from exc
