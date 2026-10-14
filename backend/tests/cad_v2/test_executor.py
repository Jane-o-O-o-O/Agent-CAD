"""Actual subprocess lifecycle and artifacts, separate from provider/network tests."""
import asyncio
import json
from pathlib import Path
import sys
import pytest
from app.infrastructure.cad_v2.executor import CADExecutor


def box_ir():
    return {'document_id':'executor-box','features':[{'id':'box','type':'Box','params':{'width':20,'depth':10,'height':8}}], 'expected_geometry':{'bbox':[20,10,8],'volume':1600}}


@pytest.mark.asyncio
async def test_worker_real_export_and_true_stages(tmp_path):
    directory=tmp_path/'job'
    stages=[]
    async def stage(name):
        stages.append(name)
    report=await CADExecutor().execute(box_ir(),directory,on_stage=stage)
    assert report['status']=='completed',report
    assert report['validation']['status']=='passed'
    assert stages==['building','validating','exporting']
    assert {a['format'] for a in report['artifacts']}=={'step','stl','glb','ir','python'}
    assert report['geometry']['step_roundtrip']['volume']==pytest.approx(1600)
    assert report['geometry']['preview']['up_axis']=='Y'
    assert not (directory/'input.json').exists()
    for item in report['artifacts']:
        assert (directory/item['filename']).stat().st_size==item['size']
    assert json.loads((directory/'model.ir.json').read_text())['document_id']=='executor-box'
    # Exported Python is a reproducible trusted IR replay, not LLM-produced arbitrary code.
    replay=await asyncio.create_subprocess_exec(sys.executable,str(directory/'model.py'),str(tmp_path/'replay'),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    stdout,stderr=await replay.communicate()
    assert replay.returncode==0,stderr.decode()
    assert (tmp_path/'replay'/'model.step').is_file()


@pytest.mark.asyncio
async def test_worker_geometry_failure_does_not_leave_artifacts(tmp_path):
    ir=box_ir()
    ir['expected_geometry']['volume']=100
    directory=tmp_path/'invalid'
    report=await CADExecutor().execute(ir,directory)
    assert report['status']=='failed'
    assert report['error']['code']=='ConstraintViolationError'
    assert not directory.exists()


@pytest.mark.asyncio
async def test_timeout_kills_real_worker_and_cleans_output(tmp_path):
    directory=tmp_path/'timeout'
    report=await CADExecutor(timeout_seconds=.01).execute(box_ir(),directory)
    assert report['error']['code']=='ExecutionTimeoutError'
    assert not directory.exists()


@pytest.mark.asyncio
async def test_cancel_event_kills_real_worker(tmp_path):
    directory=tmp_path/'cancel'
    event=asyncio.Event()
    async def stage(name):
        if name=='building':
            event.set()
    report=await CADExecutor().execute(box_ir(),directory,cancel_event=event,on_stage=stage)
    assert report['status']=='cancelled'
    assert not directory.exists()


@pytest.mark.asyncio
async def test_task_cancellation_cleans_worker_and_output(tmp_path):
    directory=tmp_path/'cancel-task'
    started=asyncio.Event()
    async def stage(name):
        started.set()
    task=asyncio.create_task(CADExecutor().execute(box_ir(),directory,on_stage=stage))
    await asyncio.wait_for(started.wait(),10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not directory.exists()


@pytest.mark.asyncio
async def test_existing_artifact_directory_is_never_overwritten(tmp_path):
    directory=tmp_path/'old'
    directory.mkdir()
    (directory/'model.step').write_text('existing revision')
    report=await CADExecutor().execute(box_ir(),directory)
    assert report['error']['code']=='InfrastructureError'
    assert (directory/'model.step').read_text()=='existing revision'


@pytest.mark.asyncio
async def test_unverified_requirements_are_not_published(tmp_path):
    ir=box_ir()
    ir['constraints']=[{'id':'finish','type':'custom','description':'Ra0.8 verified'}]
    directory=tmp_path/'unverified'
    report=await CADExecutor().execute(ir,directory)
    assert report['status']=='failed'
    assert report['error']['details']['validation']['status']=='unverified'
    assert not directory.exists()
