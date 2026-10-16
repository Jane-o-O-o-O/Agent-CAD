"""Bounded sweep, loft, freeform profiles and real helical-groove coverage."""
import math
import json
import os
import time

import pytest

from app.domain.cad_v2.compiler import compile_ir
from app.domain.cad_v2.errors import DependencyError, ParameterError, SchemaValidationError


def feature(identifier, kind, params=None, inputs=()):
    return {"id":identifier,"type":kind,"params":params or {},"inputs":list(inputs)}


def engine():
    pytest.importorskip("build123d")
    from app.infrastructure.cad_v2.backend import Build123dBackend
    return Build123dBackend()


def thread_params(**changes):
    return {"major_diameter":10,"minor_diameter":9,"pitch":1.5,"length":6,**changes}


def thread_ir(internal=False, **changes):
    features = [feature("blank","Cylinder",{"radius":8 if internal else 5,"height":6})]
    if internal:
        features.append(feature("bore","Hole",{"diameter":9,"positions":[[0,0,0]]},["blank"]))
    features.append(feature("thread","HelicalThread",thread_params(internal=internal,**changes),["bore" if internal else "blank"]))
    return {"features":features,"expected_geometry":{"bbox":[16,16,6] if internal else [10,10,6]}}


def test_extended_lengths_expressions_and_flags_normalize_once():
    document = {
        "units":"cm", "parameters":{"r":{"value":1}},
        "features":[
            feature("profile","BSpline",{"points":[["r",0],[0,"r"],["-r",0],[0,"-r"]]}),
            feature("path","Path3D",{"points":[[0,0,0],[0,0,5]],"interpolation":"bspline","tangents":[[0,0,2],[0,0,3]]}),
            feature("swept","Sweep",inputs=["profile","path"]),
        ],
    }
    compiled = compile_ir(document)
    assert compiled.features[0].params["points"] == [[10,0],[0,10],[-10,0],[0,-10]]
    assert compiled.features[1].params["points"][-1] == [0,0,50]
    assert compiled.features[1].params["tangents"] == [[0,0,1],[0,0,1]]
    assert compiled.features[-1].params == {"is_frenet":False}
    document = thread_ir()
    document["units"] = "inch"
    compiled = compile_ir(document)
    params = compiled.features[-1].params
    assert params["pitch"] == pytest.approx(1.5*25.4)
    assert params["minor_diameter"] == pytest.approx(9*25.4)
    assert params["profile_angle"] == 60
    assert params["internal"] is False and params["left_hand"] is False


@pytest.mark.parametrize("features",[
    [feature("profile","Circle",{"radius":1}),feature("bad","Sweep",inputs=["profile","profile"])],
    [feature("body","Box",{"width":1,"depth":1,"height":1}),feature("bad","Loft",inputs=["body","body"])],
    [feature("profile","Circle",{"radius":1}),feature("bad","Loft",inputs=["profile"])],
    [feature("path","Path3D",{"points":[[0,0,0],[0,0,5]]})],
])
def test_extended_shape_contracts_fail_before_kernel(features):
    with pytest.raises(DependencyError):
        compile_ir({"features":features})


@pytest.mark.parametrize("changes",[
    {"major_diameter":8}, {"pitch":0}, {"pitch":0.5},
    {"length":0.5}, {"length":100}, {"profile_angle":5},
    {"axis_direction":[0,0,0]},
])
def test_thread_bounds_fail_before_kernel(changes):
    with pytest.raises(ParameterError):
        compile_ir(thread_ir(**changes))


def test_thread_multistart_or_conical_claims_are_not_silently_accepted():
    ir = thread_ir()
    ir["features"][-1]["params"]["starts"] = 2
    with pytest.raises(SchemaValidationError):
        compile_ir(ir)


@pytest.mark.parametrize("kind,params",[
    ("BSpline",{"points":[[0,0],[1,0],[0,1],[0,0]]}),
    ("Path3D",{"points":[[0,0,0],[0,0,0],[0,0,1]]}),
    ("Path3D",{"points":[[0,0,0],[0,0,1],[0,0,0]]}),
])
def test_degenerate_profile_and_path_rejected_before_kernel(kind,params):
    features = [feature("item",kind,params)]
    if kind == "BSpline":
        features.append(feature("out","Extrude",{"amount":1},["item"]))
    else:
        features.insert(0,feature("profile","Circle",{"radius":1}))
        features.append(feature("out","Sweep",inputs=["profile","item"]))
    with pytest.raises(ParameterError):
        compile_ir({"features":features})


def test_straight_sweep_measured_volume_and_step_roundtrip(tmp_path):
    backend = engine()
    result = backend.build(compile_ir({"features":[
        feature("profile","Circle",{"radius":2}),
        feature("path","Path3D",{"points":[[0,0,0],[0,0,12]]}),
        feature("swept","Sweep",inputs=["profile","path"]),
    ],"expected_geometry":{"bbox":[4,4,12],"volume":math.pi*4*12}}))
    assert backend.validate(result)["status"] == "passed"
    assert result.trace[-1]["evidence"]["path_length"] == pytest.approx(12)
    assert result.trace[-1]["evidence"]["profile_area"] == pytest.approx(math.pi*4)
    assert {entry["format"] for entry in backend.export(result,tmp_path)} == {"step","stl","glb"}
    assert result.geometry["step_roundtrip_checks"]["status"] == "passed"


def test_curved_bspline_path_sweep_is_a_valid_solid():
    backend = engine()
    result = backend.build(compile_ir({"features":[
        feature("profile","Circle",{"radius":0.6,"plane":"YZ"}),
        feature("path","Path3D",{"points":[[0,0,0],[5,0,0],[10,3,0],[15,8,0]],"interpolation":"bspline","tangents":[[1,0,0],[1,1,0]]}),
        feature("swept","Sweep",inputs=["profile","path"]),
    ]}))
    assert backend.validate(result)["status"] == "passed"
    assert result.geometry["solids"] == 1
    assert result.trace[-1]["evidence"]["path_length"] > 17


def test_sweep_does_not_guess_profile_orientation():
    backend = engine()
    with pytest.raises(ParameterError,match="normal"):
        backend.build(compile_ir({"features":[
            feature("profile","Circle",{"radius":1}),
            feature("path","Path3D",{"points":[[0,0,0],[10,0,0]]}),
            feature("swept","Sweep",inputs=["profile","path"]),
        ]}))


def test_ruled_loft_frustum_volume_and_roundtrip(tmp_path):
    backend = engine()
    volume = math.pi*10/3*(4+2+1)
    result = backend.build(compile_ir({"features":[
        feature("lower","Circle",{"radius":2}),
        feature("upper","Circle",{"radius":1,"origin":[0,0,10]}),
        feature("solid","Loft",{"ruled":True},["lower","upper"]),
    ],"expected_geometry":{"bbox":[4,4,10],"volume":volume}}))
    assert backend.validate(result)["status"] == "passed"
    assert result.trace[-1]["evidence"]["stations"] == [0,10]
    backend.export(result,tmp_path)
    assert result.geometry["step_roundtrip_checks"]["status"] == "passed"


def test_smooth_loft_of_periodic_bspline_profiles_exports_freeform_solid(tmp_path):
    backend = engine()
    points = [[2,0],[1,2],[-1,2],[-2,0],[-1,-1],[1,-1]]
    result = backend.build(compile_ir({"features":[
        feature("lower","BSpline",{"points":points}),
        feature("middle","BSpline",{"points":[[x*1.5,y*1.5] for x,y in points],"origin":[1,0,5]}),
        feature("upper","BSpline",{"points":points,"origin":[0,0,10]}),
        feature("solid","Loft",inputs=["lower","middle","upper"]),
    ]}))
    assert backend.validate(result)["status"] == "passed"
    assert any(face.geom_type.name == "BSPLINE" for face in result.shape.faces())
    assert len(result.trace[-1]["evidence"]["section_areas"]) == 3
    backend.export(result,tmp_path)
    assert result.geometry["step_roundtrip_checks"]["status"] == "passed"


@pytest.mark.parametrize("origins",[[[0,0,0],[0,0,0]],[[0,0,0],[0,0,5],[0,0,3]]])
def test_loft_rejects_coincident_or_unordered_sections(origins):
    backend = engine()
    profiles = [feature(f"section{i}","Circle",{"radius":1,"origin":origin}) for i,origin in enumerate(origins)]
    with pytest.raises(ParameterError,match="ordered"):
        backend.build(compile_ir({"features":[*profiles,feature("solid","Loft",inputs=[p["id"] for p in profiles])]}))


@pytest.mark.parametrize("internal,left_hand",[(False,False),(False,True),(True,False),(True,True)])
def test_real_helical_thread_removes_stock_and_preserves_roundtrip(tmp_path,internal,left_hand):
    backend = engine()
    result = backend.build(compile_ir(thread_ir(internal,left_hand=left_hand)))
    report = backend.validate(result)
    assert report["status"] == "passed"
    evidence = result.trace[-1]["evidence"]["thread"]
    assert evidence["removed_volume"] > 1
    assert evidence["probes"] and evidence["matching_walls"]
    assert result.geometry["solids"] == 1
    backend.export(result,tmp_path)
    assert result.geometry["step_roundtrip_checks"]["status"] == "passed"


def test_thread_rejects_incorrect_body_datums():
    backend = engine()
    document = thread_ir()
    document["features"][0]["params"]["radius"] = 4
    with pytest.raises(ParameterError,match="cylindrical wall"):
        backend.build(compile_ir(document))


def test_thread_axis_and_origin_are_applied_to_real_geometry():
    backend = engine()
    document = {"features":[
        feature("blank","Cylinder",{"radius":5,"height":6}),
        feature("positioned","Transform",{"rotation":[0,90,0],"translation":[3,4,5]},["blank"]),
        feature("thread","HelicalThread",thread_params(axis_origin=[3,4,5],axis_direction=[1,0,0]),["positioned"]),
    ],"expected_geometry":{"bbox":[6,10,10]}}
    result = backend.build(compile_ir(document))
    assert backend.validate(result)["status"] == "passed"
    assert result.geometry["bbox"]["min"] == pytest.approx([3,-1,0],abs=1e-5)


def test_later_cut_cannot_erase_thread_and_claim_success():
    backend = engine()
    document = thread_ir()
    document["expected_geometry"] = {}
    document["features"].extend([
        feature("core","Cylinder",{"radius":4.4,"height":6}),
        feature("erased","Intersection",inputs=["thread","core"]),
    ])
    result = backend.build(compile_ir(document))
    report = backend.validate(result)
    assert report["status"] == "failed"
    assert next(check for check in report["checks"] if check["name"].endswith("final_thread"))["status"] == "failed"


@pytest.mark.skipif(os.name != "posix",reason="Strict CAD worker requires Linux OS isolation")
@pytest.mark.asyncio
async def test_internal_thread_completes_default_strict_worker_and_all_exports(tmp_path):
    from app.infrastructure.cad_v2.executor import CADExecutor

    started = time.perf_counter()
    def stage(name):
        print(json.dumps({"stage":name,"elapsed_seconds":time.perf_counter()-started}),flush=True)
    report = await CADExecutor(isolation_mode="strict").execute(thread_ir(True),tmp_path/"strict-thread",on_stage=stage)
    assert report["status"] == "completed",report
    assert report["isolation"]["requested_mode"] == "strict"
    assert report["isolation"]["os_enforced"] is True
    assert report["validation"]["status"] == "passed"
    assert {item["format"] for item in report["artifacts"]} == {"step","stl","glb","dxf","svg","ir","python"}
    assert report["geometry"]["step_roundtrip_checks"]["status"] == "passed"
    drawing = report["geometry"]["drawing"]
    assert drawing["method"] == "occt_polygonal_hlr"
    assert drawing["measurement_source"] == "final_brep"
    assert drawing["approximation_tolerance_mm"] == 0.01
    assert 0 < drawing["mesh_triangles"] <= 100000
    assert all(view["entities"]["visible"] > 0 for view in drawing["views"])
    import ezdxf
    drawing_file = ezdxf.readfile(tmp_path/"strict-thread"/"drawing.dxf")
    assert not drawing_file.audit().has_errors
    assert any("Projection: polygonal" in item.dxf.text for item in drawing_file.modelspace().query("TEXT"))
    dimensions = [item.get_measurement() for item in drawing_file.modelspace().query("DIMENSION")]
    assert sorted(dimensions) == pytest.approx([6,16,16])
    thread = report["trace"][-1]["evidence"]["thread"]
    assert thread["corrected_pcurves"]
    print(json.dumps({"isolation":report["isolation"],"elapsed_seconds":report["executor_elapsed_seconds"],
                      "volume_before":report["geometry"]["volume"],"step_volume":report["geometry"]["step_roundtrip"]["volume"],
                      "volume_delta":report["geometry"]["step_roundtrip"]["volume"]-report["geometry"]["volume"],
                      "corrected_pcurves":thread["corrected_pcurves"],
                      "drawing":{"method":drawing["method"],"mesh_triangles":drawing["mesh_triangles"],
                                 "approximation_tolerance_mm":drawing["approximation_tolerance_mm"]},
                      "artifacts":[item["format"] for item in report["artifacts"]]}))
