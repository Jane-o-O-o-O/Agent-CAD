"""Frozen analytic design corpus, defined before the first kernel benchmark run.

Volumes below come from elementary solid formulas, not measured kernel output.
The prompts describe the same designs but fixture mode deliberately bypasses NLP.
Coordinates are millimetres, right-handed Z-up; boxes use their minimum corner.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
from math import cos, pi, sin
from typing import Any

CORPUS_VERSION = "2026-10-14.1"


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    category: str
    prompt: str
    ir: dict[str, Any]
    bbox_min: tuple[float, float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.__dict__)


def f(identifier: str, kind: str, *, inputs: list[str] | None = None, **params: Any) -> dict:
    return {"id": identifier, "type": kind, "inputs": inputs or [], "params": params}


def box(identifier: str, width: float, depth: float, height: float, origin=(0, 0, 0)) -> dict:
    return f(identifier, "Box", width=width, depth=depth, height=height, origin=origin)


def cylinder(identifier: str, radius: float, height: float, origin=(0, 0, 0)) -> dict:
    return f(identifier, "Cylinder", radius=radius, height=height, origin=origin)


def holes(identifier: str, base: str, diameter: float, positions: list, direction=(0, 0, 1)) -> dict:
    return f(identifier, "Hole", inputs=[base], diameter=diameter, positions=positions,
             direction=direction, through=True)


def expected_holes(diameter: float, positions: list, direction=(0, 0, 1)) -> dict:
    return {"diameter": diameter, "positions": positions, "direction": direction, "through": True}


def selector(*, center=None, parallel=None, count=1) -> dict:
    value = {"kind": "edge", "geometry_type": "LINE", "expected_count": count, "tolerance": 0.0001}
    if center is not None:
        value["center"] = center
    if parallel is not None:
        value["parallel"] = parallel
    return value


def case(name: str, category: str, prompt: str, features: list[dict], bbox: list,
         volume: float | None, *, solids=1, expected: list | None = None,
         bbox_min=None, volume_min=None, volume_max=None) -> BenchmarkCase:
    geometry = {"bbox": bbox, "solids": solids, "holes": expected or [], "tolerance": 0.0001}
    if volume is not None:
        geometry["volume"] = volume
    if volume_min is not None:
        geometry["volume_min"] = volume_min
    if volume_max is not None:
        geometry["volume_max"] = volume_max
    return BenchmarkCase(name, category, prompt, {
        "schema_version": "2.0", "document_id": f"benchmark-{name}", "units": "mm",
        "coordinate_system": "right-handed-Z-up", "features": features,
        "output_feature": features[-1]["id"], "expected_geometry": geometry,
        "metadata": {"corpus_version": CORPUS_VERSION, "benchmark": name,
                     "expectation_source": "analytic design specification before execution"},
    }, bbox_min)


def plate_ir(diameter: float = 6) -> dict:
    """A/B fixture: independent fixed external dimensions and parameterized diameter."""
    positions = [[10, 10, 0], [70, 10, 0], [10, 40, 0], [70, 40, 0]]
    result = case("mounting_plate", "basic", "", [box("plate", 80, 50, 8),
                  holes("mount_holes", "plate", diameter, positions)], [80, 50, 8],
                  32000 - 4*pi*(diameter/2)**2*8,
                  expected=[expected_holes(diameter, positions)]).ir
    result["parameters"] = {"hole_diameter": {"kind": "length", "value": diameter, "min": 0.1, "max": 19}}
    result["features"][-1]["params"]["diameter"] = "hole_diameter"
    result["expected_geometry"]["holes"][0]["diameter"] = "hole_diameter"
    result["expected_geometry"]["volume"] = f"32000 - 4*{pi!r}*(hole_diameter/2)**2*8"
    return result


def repair_ir() -> dict:
    """Deliberately missed semantic edge, repairable without changing the design."""
    return {
        "schema_version": "2.0", "document_id": "precision-repair", "units": "mm",
        "parameters": {"radius": {"kind": "length", "value": 1}},
        "features": [
            {"id": "body", "type": "Box", "params": {"width": 40, "depth": 30, "height": 8}},
            {"id": "round", "type": "Fillet", "inputs": ["body"], "params": {
                "radius": "radius", "selector": {"kind": "edge", "geometry_type": "LINE",
                    "parallel": [0, 0, 1], "center": [0.00005, 0, 4], "expected_count": 1,
                    "tolerance": 0.000001}}},
        ],
        "output_feature": "round",
        "expected_geometry": {"bbox": [40, 30, 8], "solids": 1, "volume": 9592 + 2*pi},
    }


def corpus() -> list[BenchmarkCase]:
    p4 = [[10, 10, 0], [70, 10, 0], [10, 40, 0], [70, 40, 0]]
    p6 = [[25*cos(i*pi/3), 25*sin(i*pi/3), 0] for i in range(6)]
    p8 = [[x, y, 0] for x in (20, 40, 60, 80) for y in (15, 45)]
    vertical = selector(parallel=[0, 0, 1], count=4)
    cases = [
        case("01_box", "basic", "40×30×10 mm 长方体。", [box("body",40,30,10)], [40,30,10],12000),
        case("02_cylinder", "basic", "直径20、长25 mm 的圆柱。", [cylinder("body",10,25)], [20,20,25],2500*pi),
        case("03_hollow_tube", "basic", "外径40、内径20、长30 mm 空心轴套。", [cylinder("outer",20,30),cylinder("bore",10,30),f("body","Cut",inputs=["outer","bore"])], [40,40,30],9000*pi,
             expected=[expected_holes(20,[[0,0,0]])]),
        case("04_sphere", "basic", "半径12 mm 球体。", [f("body","Sphere",radius=12)], [24,24,24],2304*pi),
        case("05_single_hole_plate", "basic", "80×50×8 mm 板中心开直径6 mm 通孔。", [box("plate",80,50,8),holes("body","plate",6,[[40,25,0]])], [80,50,8],32000-72*pi,expected=[expected_holes(6,[[40,25,0]])]),
        BenchmarkCase("06_four_hole_plate", "basic", "生成一块长 80 mm、宽 50 mm、厚 8 mm 的安装板，在四角开四个直径 6 mm 的通孔，孔中心的水平距离为 60 mm，垂直距离为 30 mm。",plate_ir()),
        case("07_rectangle_extrude", "basic", "60×40 mm 矩形草图拉伸6 mm。", [f("sketch","Rectangle",width=60,height=40),f("body","Extrude",inputs=["sketch"],amount=6)], [60,40,6],14400),
        case("08_circle_extrude", "basic", "半径15 mm 圆草图拉伸12 mm。", [f("sketch","Circle",radius=15),f("body","Extrude",inputs=["sketch"],amount=12)], [30,30,12],2700*pi),
        case("09_triangle_extrude", "basic", "顶点(0,0)、(30,0)、(0,20)的闭合草图拉伸10 mm。", [f("sketch","Polyline",points=[[0,0],[30,0],[0,20]]),f("body","Extrude",inputs=["sketch"],amount=10)], [30,20,10],3000),
        case("10_stepped_shaft", "basic", "同轴阶梯轴：直径20长30，上接直径30长10 mm。", [cylinder("shaft",10,30),cylinder("shoulder",15,10,[0,0,30]),f("body","Union",inputs=["shaft","shoulder"])], [30,30,40],5250*pi),
        case("11_ring_flange", "basic", "外径60、内径24、厚8 mm 环形法兰。", [cylinder("disc",30,8),holes("body","disc",24,[[0,0,0]])], [60,60,8],6048*pi,expected=[expected_holes(24,[[0,0,0]])]),
        case("12_vertical_fillets", "basic", "40×30×10 mm 长方体四条竖边倒R2圆角。", [box("base",40,30,10),f("body","Fillet",inputs=["base"],radius=2,selector=vertical)], [40,30,10],12000-40*(4-pi)),
        case("13_vertical_chamfers", "basic", "40×30×10 mm 长方体四条竖边倒角2 mm。", [box("base",40,30,10),f("body","Chamfer",inputs=["base"],length=2,selector=vertical)], [40,30,10],11920),
        case("14_linear_pattern", "basic", "10×8×6 mm 实体沿X间距20 mm 阵列3份，保留独立实体。", [box("seed",10,8,6),f("body","LinearPattern",inputs=["seed"],count=3,spacing=[20,0,0])], [50,8,6],1440,solids=3),
        case("15_circular_pattern", "basic", "半径3高10 mm 圆柱中心在(20,0,0)，绕Z轴完整阵列4份。", [cylinder("seed",3,10,[20,0,0]),f("body","CircularPattern",inputs=["seed"],count=4)], [46,46,10],360*pi,solids=4),
        case("16_translation", "basic", "20×10×5 mm 块平移到(15,-8,4)。", [box("base",20,10,5),f("body","Transform",inputs=["base"],translation=[15,-8,4])], [20,10,5],1000,bbox_min=(15,-8,4)),
        case("17_rotation", "basic", "原点20×10×5 mm 块绕Z轴旋转90度。", [box("base",20,10,5),f("body","Transform",inputs=["base"],rotation=[0,0,90])], [10,20,5],1000,bbox_min=(-10,0,0)),
        case("18_intersection", "basic", "两个20×20×10块沿X相差10 mm，取交集。", [box("a",20,20,10),box("b",20,20,10,[10,0,0]),f("body","Intersection",inputs=["a","b"])], [10,20,10],2000,bbox_min=(10,0,0)),
        case("19_split_cut", "basic", "60×30×10 mm 块切掉中间10 mm 宽贯通槽，保留两实体。", [box("stock",60,30,10),box("tool",10,40,10,[25,-5,0]),f("body","Cut",inputs=["stock","tool"])], [60,30,10],15000,solids=2),
        case("20_revolve", "basic", "XZ平面径向10到20、轴向0到15 mm 矩形绕Z旋转360度。", [f("profile","Polyline",points=[[10,0],[20,0],[20,15],[10,15]],plane="XZ"),f("body","Revolve",inputs=["profile"])], [40,40,15],4500*pi,expected=[expected_holes(20,[[0,0,0]])]),
        case("21_bolt_flange", "medium", "外径70厚8法兰：中心20通孔、节圆50上的6个6通孔，外缘四周保持。", [cylinder("disc",35,8),holes("center_bore","disc",20,[[0,0,0]]),*[holes(f"bolt{i+1}","center_bore" if i==0 else f"bolt{i}",6,[p]) for i,p in enumerate(p6)]], [70,70,8],8568*pi,expected=[expected_holes(20,[[0,0,0]]),expected_holes(6,p6)]),
        case("22_l_bracket", "medium", "L型支架：60×40×6底板，后边60×6×40立板从z=6起；底面与立面各2个直径6通孔；立板顶前外边R2。", [box("base",60,40,6),box("wall",60,6,40,[0,0,6]),f("blank","Union",inputs=["base","wall"]),holes("base_holes","blank",6,[[15,25,0],[45,25,0]]),holes("wall_holes","base_holes",6,[[15,0,26],[45,0,26]],[0,1,0]),f("body","Fillet",inputs=["wall_holes"],radius=2,selector=selector(center=[30,0,46],parallel=[1,0,0]))], [60,40,46],28560-156*pi,expected=[expected_holes(6,[[15,25,0],[45,25,0]]),expected_holes(6,[[15,0,26],[45,0,26]],[0,1,0])]),
        case("23_ribbed_base", "medium", "60×40×6底板、60×6×30立板，中央6厚三角加强筋，底板两孔6 mm。", [box("base",60,40,6),box("wall",60,6,30,[0,0,6]),f("rib_profile","Polyline",points=[[6,6],[34,6],[6,30]],plane="YZ",origin=[27,0,0]),f("rib","Extrude",inputs=["rib_profile"],amount=6),f("blank","Union",inputs=["base","wall","rib"]),holes("body","blank",6,[[15,27,0],[45,27,0]])], [60,40,36],27216-108*pi,expected=[expected_holes(6,[[15,27,0],[45,27,0]])]),
        case("24_multilayer_steps", "medium", "80×60×5基台，居中60×40×8台阶，再居中40×20×12台阶，中心8 mm 通孔。", [box("lower",80,60,5),box("middle",60,40,8,[10,10,5]),box("upper",40,20,12,[20,20,13]),f("blank","Union",inputs=["lower","middle","upper"]),holes("body","blank",8,[[40,30,0]])], [80,60,25],52800-400*pi,expected=[expected_holes(8,[[40,30,0]])]),
        case("25_three_step_shaft", "medium", "同轴三阶轴：直径30长20、直径24长25、直径16长15；贯通6孔和顶端直径10深3沉孔。", [cylinder("lower",15,20),cylinder("middle",12,25,[0,0,20]),cylinder("upper",8,15,[0,0,45]),f("blank","Union",inputs=["lower","middle","upper"]),holes("bore","blank",6,[[0,0,0]]),cylinder("counterbore",5,3,[0,0,57]),f("body","Cut",inputs=["bore","counterbore"])], [30,30,60],8472*pi,
             expected=[]),
        case("26_symmetric_hole_array", "medium", "100×60×8板，x=20/40/60/80与y=15/45的8个6通孔，顶面中心20×20深2凹槽，四竖边1倒角。", [box("plate",100,60,8),f("chamfered","Chamfer",inputs=["plate"],length=1,selector=vertical),holes("drilled","chamfered",6,p8),f("pocket_sketch","Rectangle",width=20,height=20,origin=[40,20,6]),f("body","Pocket",inputs=["drilled","pocket_sketch"],depth=2)], [100,60,8],47184-576*pi,expected=[expected_holes(6,p8)]),
        case("27_multiple_cuts", "medium", "80×50×10板切3个10×30贯通矩形槽(x=10/35/60,y=10)，四角5 mm 边距直径4通孔。", [box("stock",80,50,10),box("slot1",10,30,10,[10,10,0]),f("cut1","Cut",inputs=["stock","slot1"]),box("slot2",10,30,10,[35,10,0]),f("cut2","Cut",inputs=["cut1","slot2"]),box("slot3",10,30,10,[60,10,0]),f("cut3","Cut",inputs=["cut2","slot3"]),holes("body","cut3",4,[[5,5,0],[75,5,0],[5,45,0],[75,45,0]])], [80,50,10],31000-160*pi,expected=[expected_holes(4,[[5,5,0],[75,5,0],[5,45,0],[75,45,0]])]),
        case("28_connector", "medium", "50×30×20块顶面两16直径高15凸台，中心x=10/40,y=15；两6通孔；底面中央20×8深10凹槽。", [box("base",50,30,20),cylinder("boss1",8,15,[10,15,20]),cylinder("boss2",8,15,[40,15,20]),f("blank","Union",inputs=["base","boss1","boss2"]),holes("drilled","blank",6,[[10,15,0],[40,15,0]]),box("slot",20,8,10,[15,11,0]),f("body","Cut",inputs=["drilled","slot"])], [50,30,35],28400+1290*pi,expected=[expected_holes(6,[[10,15,0],[40,15,0]])]),
        case("29_pattern_cut_flange", "medium", "外径70厚10盘，半径25的节圆六孔直径6，通过圆周阵列切除；顶面中心直径24深4凹槽。", [cylinder("stock",35,10),cylinder("tool",3,12,[25,0,-1]),f("tools","CircularPattern",inputs=["tool"],count=6),f("drilled","Cut",inputs=["stock","tools"]),cylinder("pocket",12,4,[0,0,6]),f("body","Cut",inputs=["drilled","pocket"])], [70,70,10],11134*pi,expected=[expected_holes(6,p6)]),
        case("30_double_rib_bracket", "medium", "70×50×6底板，70×6×35立板，两6厚30×25直角三角加强筋；底面立面各两6孔，立板顶前外边倒角1。", [box("base",70,50,6),box("wall",70,6,35,[0,0,6]),f("rib_profile","Polyline",points=[[6,6],[36,6],[6,31]],plane="YZ",origin=[10,0,0]),f("rib1","Extrude",inputs=["rib_profile"],amount=6),f("rib2","Transform",inputs=["rib1"],translation=[44,0,0]),f("blank","Union",inputs=["base","wall","rib1","rib2"]),holes("base_holes","blank",6,[[23,38,0],[47,38,0]]),holes("wall_holes","base_holes",6,[[23,0,24],[47,0,24]],[0,1,0]),f("body","Chamfer",inputs=["wall_holes"],length=1,selector=selector(center=[35,0,41],parallel=[1,0,0]))], [70,50,41],40165-216*pi,expected=[expected_holes(6,[[23,38,0],[47,38,0]]),expected_holes(6,[[23,0,24],[47,0,24]],[0,1,0])]),
    ]
    return cases


def corpus_hash() -> str:
    data = json.dumps([x.to_dict() for x in corpus()], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()
