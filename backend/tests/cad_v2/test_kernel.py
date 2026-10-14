"""Real OpenCascade integration; no geometry mocks."""
import json
import math
from pathlib import Path
import struct
import pytest
from build123d import import_step
from app.domain.cad_v2.compiler import compile_ir
from app.domain.cad_v2.errors import InvalidTopologyError, BooleanOperationError
from app.infrastructure.cad_v2.backend import Build123dBackend
from app.infrastructure.cad_v2.validation import validate


def f(id,type,params,inputs=()):
    return {"id":id,"type":type,"params":params,"inputs":list(inputs)}


def build(features,expected=None):
    return Build123dBackend().build(compile_ir({"features":features,"expected_geometry":expected or {}}))


def plate_ir(diameter=6):
    return {"schema_version":"2.0","document_id":"kernel-plate","features":[
        f("plate","Box",{"width":80,"depth":50,"height":8}),
        f("holes","Hole",{"diameter":diameter,"positions":[[10,10,0],[70,10,0],[10,40,0],[70,40,0]]},["plate"])],
        "expected_geometry":{"bbox":[80,50,8],"volume":32000-4*math.pi*(diameter/2)**2*8}}


def test_mounting_plate_real_holes_and_step_roundtrip(tmp_path):
    engine=Build123dBackend()
    result=engine.build(compile_ir(plate_ir()))
    report=validate(result)
    assert report['status']=='passed'
    assert len([c for c in report['checks'] if c['name'].endswith('.through')])==4
    artifacts=engine.export(result,tmp_path)
    assert {a['format'] for a in artifacts}=={'step','stl','glb'}
    restored=import_step(tmp_path/'model.step')
    assert restored.is_valid and len(restored.solids())==1
    assert restored.volume==pytest.approx(result.geometry['volume'],abs=1e-6)
    data=(tmp_path/'model.glb').read_bytes()
    size,_=struct.unpack_from('<II',data,12)
    gltf=json.loads(data[20:20+size])
    assert gltf['materials']
    assert all('NORMAL' in primitive['attributes'] for m in gltf['meshes'] for primitive in m['primitives'])
    assert all(a['size']>0 and len(a['sha256'])==64 for a in artifacts)


@pytest.mark.parametrize('features,volume,bbox',[
    ([f('b','Box',{'width':10,'depth':20,'height':30})],6000,[10,20,30]),
    ([f('c','Cylinder',{'radius':5,'height':8})],math.pi*25*8,[10,10,8]),
    ([f('s','Sphere',{'radius':5})],4/3*math.pi*125,[10,10,10]),
    ([f('sk','Rectangle',{'width':10,'height':20}),f('ex','Extrude',{'amount':5},['sk'])],1000,[10,20,5]),
    ([f('sk','Circle',{'radius':5}),f('ex','Extrude',{'amount':5},['sk'])],math.pi*125,[10,10,5]),
    ([f('sk','Polyline',{'points':[[0,0],[10,0],[0,10]]}),f('ex','Extrude',{'amount':3},['sk'])],150,[10,10,3]),
    ([f('sk','Rectangle',{'width':4,'height':10,'origin':[5,0,0],'plane':'XZ'}),f('rv','Revolve',{},['sk'])],math.pi*(81-25)*10,[18,18,10]),
    ([f('a','Box',{'width':10,'depth':10,'height':10}),f('b','Box',{'width':10,'depth':10,'height':10,'origin':[5,0,0]}),f('u','Union',{},['a','b'])],1500,[15,10,10]),
    ([f('a','Box',{'width':10,'depth':10,'height':10}),f('b','Box',{'width':10,'depth':10,'height':10,'origin':[5,0,0]}),f('c','Cut',{},['a','b'])],500,[5,10,10]),
    ([f('a','Box',{'width':10,'depth':10,'height':10}),f('b','Box',{'width':10,'depth':10,'height':10,'origin':[5,0,0]}),f('i','Intersection',{},['a','b'])],500,[5,10,10]),
    ([f('b','Box',{'width':10,'depth':20,'height':30}),f('t','Transform',{'translation':[5,6,7],'rotation':[0,0,90]},['b'])],6000,[20,10,30]),
    ([f('b','Box',{'width':20,'depth':20,'height':10}),f('sk','Rectangle',{'width':10,'height':10,'origin':[5,5,7]}),f('p','Pocket',{'depth':3},['b','sk'])],3700,[20,20,10]),
])
def test_core_operations(features,volume,bbox):
    result=build(features,{'volume':volume,'bbox':bbox})
    assert validate(result)['status']=='passed'
    assert result.geometry['volume']==pytest.approx(volume,abs=1e-6)


@pytest.mark.parametrize('kind,params',[
    ('LinearPattern',{'count':3,'spacing':[15,0,0]}),
    ('CircularPattern',{'count':4,'axis_origin':[0,0,0],'axis_direction':[0,0,1]})])
def test_patterns(kind,params):
    count=params['count']
    result=build([f('c','Cylinder',{'radius':2,'height':5,'origin':[10,0,0]}),f('p',kind,params,['c'])],{'solids':count,'volume':count*math.pi*4*5})
    assert validate(result)['status']=='passed'


@pytest.mark.parametrize('kind,sizekey',[('Fillet','radius'),('Chamfer','length')])
def test_semantic_edges_feature_changes(kind,sizekey):
    result=build([f('b','Box',{'width':20,'depth':10,'height':5}),
                  f('finish',kind,{sizekey:1,'selector':{'parallel':[0,0,1],'expected_count':4}},['b'])],{'bbox':[20,10,5]})
    assert validate(result)['status']=='passed'
    assert result.trace[-1]['before_volume']>result.trace[-1]['after_volume']
    assert result.trace[-1]['evidence']['selected_edges']==4


def test_actual_recoverable_precision_selector_failure():
    features=[f('b','Box',{'width':20,'depth':10,'height':8}),f('fillet','Fillet',{'radius':1,'selector':{'center':[.00005,0,4],'tolerance':1e-6,'expected_count':1}},['b'])]
    with pytest.raises(InvalidTopologyError) as error:
        build(features)
    assert error.value.recoverable and error.value.feature_id=='fillet'
    recommended=error.value.details['recommended_tolerance']
    assert 1e-6<recommended<=1e-3
    features[-1]['params']['selector']['tolerance']=recommended
    fixed=build(features,{'bbox':[20,10,8]})
    assert validate(fixed)['status']=='passed'
    assert fixed.compiled.features[-1].params['radius']==1


def test_ambiguous_selector_never_selects_first_edge():
    with pytest.raises(InvalidTopologyError) as error:
        build([f('b','Box',{'width':20,'depth':10,'height':8}),f('f','Fillet',{'radius':1,'selector':{'parallel':[0,0,1],'expected_count':1}},['b'])])
    assert not error.value.recoverable
    assert error.value.details['matched']==4


def test_empty_boolean_and_outside_hole_fail():
    with pytest.raises(BooleanOperationError):
        build([f('a','Box',{'width':10,'depth':10,'height':10}),f('b','Box',{'width':5,'depth':5,'height':5,'origin':[20,0,0]}),f('i','Intersection',{},['a','b'])])
    with pytest.raises(BooleanOperationError):
        build([f('b','Box',{'width':10,'depth':10,'height':10}),f('h','Hole',{'diameter':2,'positions':[[30,30,0]]},['b'])])


def test_blind_hole_depth_is_measured():
    result=build([f('b','Box',{'width':20,'depth':20,'height':10}),f('h','Hole',{'diameter':4,'positions':[[10,10,10]],'direction':[0,0,-1],'through':False,'depth':4},['b'])])
    assert validate(result)['status']=='passed'
    through_expected={**result.compiled.expected_geometry,'holes':[{'diameter':4,'positions':[[10,10,10]],'direction':[0,0,-1],'through':True}]}
    result.compiled.expected_geometry=through_expected
    assert validate(result)['status']=='failed'


def test_unimplemented_intent_is_unverified_not_passed():
    ir=plate_ir()
    ir['constraints']=[{'id':'manufacturable','type':'custom','description':'certified manufacturing fit'}]
    assert validate(Build123dBackend().build(compile_ir(ir)))['status']=='unverified'


def test_yz_sketch_origin_is_world_translation():
    result=build([f('sk','Rectangle',{'width':10,'height':20,'plane':'YZ','origin':[27,0,0]}),f('ex','Extrude',{'amount':3},['sk'])],{'bbox':[3,10,20]})
    assert result.geometry['bbox']['min']==pytest.approx([27,0,0])
    assert validate(result)['status']=='passed'


def test_extra_hole_rejected_by_complete_hole_requirements():
    ir=plate_ir()
    ir['features'].append(f('extra','Hole',{'diameter':6,'positions':[[40,25,0]]},['holes']))
    ir['expected_geometry']={'bbox':[80,50,8],'holes':[{'diameter':6,'positions':[[10,10,0],[70,10,0],[10,40,0],[70,40,0]]}]}
    report=validate(Build123dBackend().build(compile_ir(ir)))
    assert report['status']=='failed'
    count=next(c for c in report['checks'] if c['name']=='complete_hole_profile_count')
    assert count['actual']==5 and count['expected']==4


def test_cut_away_fillet_is_not_verified_from_intermediate_trace():
    result=build([f('b','Box',{'width':20,'depth':10,'height':5}),
                  f('fillet','Fillet',{'radius':1,'selector':{'center':[0,0,2.5],'expected_count':1}},['b']),
                  f('tool','Box',{'width':3,'depth':12,'height':7,'origin':[-1,-1,-1]}),
                  f('cut','Cut',{},['fillet','tool'])],{'bbox':[18,10,5],'volume':900})
    report=validate(result)
    assert report['status']=='failed'
    assert next(c for c in report['checks'] if c['name']=='feature.fillet.final_finish')['status']=='failed'


def test_transformed_holes_use_final_complete_collection():
    ir=plate_ir()
    ir['features'].append(f('move','Transform',{'translation':[5,0,0]},['holes']))
    ir['expected_geometry']['holes']=[{'diameter':6,'positions':[[15,10,0],[75,10,0],[15,40,0],[75,40,0]]}]
    assert validate(Build123dBackend().build(compile_ir(ir)))['status']=='passed'


def test_arbitrary_feature_validation_condition_is_unverified():
    ir=plate_ir()
    ir['features'][0]['validation_conditions']=[{'type':'custom_surface_finish','value':'Ra0.8'}]
    assert validate(Build123dBackend().build(compile_ir(ir)))['status']=='unverified'


def test_backend_public_geometry_ports():
    engine=Build123dBackend()
    result=build([f('b','Box',{'width':10,'depth':20,'height':5})])
    assert engine.validate(result)['status']=='passed'
    assert engine.properties(result)['volume']==pytest.approx(1000)
    selected=engine.query_topology(result,{'kind':'edge','parallel':[0,0,1],'expected_count':4})
    assert len(selected)==4 and all(s['type']=='LINE' for s in selected)
    mesh=engine.tessellate(result)
    assert mesh['units']=='mm' and len(mesh['triangles'])==12
    engine.cleanup()


def test_unrelated_branch_transform_does_not_invalidate_finish():
    result=build([f('b','Box',{'width':20,'depth':10,'height':5}),
        f('f','Fillet',{'radius':1,'selector':{'parallel':[0,0,1],'expected_count':4}},['b']),
        f('s','Box',{'width':4,'depth':4,'height':5}),
        f('t','Transform',{'translation':[8,8,0]},['s']),
        f('u','Union',{},['f','t'])])
    assert validate(result)['status']=='passed'


def test_blind_pocket_is_not_an_unexpected_through_hole():
    result=build([f('b','Box',{'width':30,'depth':20,'height':8}),
        f('hole','Hole',{'diameter':4,'positions':[[5,10,0]]},['b']),
        f('sk','Circle',{'radius':4,'origin':[20,10,6]}),
        f('pocket','Pocket',{'depth':2},['hole','sk'])],
        {'holes':[{'diameter':4,'positions':[[5,10,0]],'through':True}]})
    assert validate(result)['status']=='passed'
