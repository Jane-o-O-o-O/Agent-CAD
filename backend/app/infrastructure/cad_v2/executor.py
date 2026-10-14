"""Async bounded executor. Imports only stdlib, never the CAD kernel in the web process."""
from __future__ import annotations
import asyncio
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import time
from typing import Any, Callable

from .sandbox import normalize_mode


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"status":"failed","error":{"code":code,"message":message,"feature_id":None,"recoverable":False,"details":details}}


class CADExecutor:
    def __init__(self, timeout_seconds: float=60, max_concurrency: int=2,
                 python_executable: str | None=None, memory_bytes: int=4*1024**3,
                 file_bytes: int=128*1024**2, isolation_mode: str | None=None) -> None:
        if timeout_seconds<=0 or max_concurrency<1:
            raise ValueError("Execution timeout and concurrency must be positive")
        self.timeout_seconds=timeout_seconds
        self.python_executable=python_executable or os.getenv("CAD_V2_PYTHON",sys.executable)
        self.memory_bytes=memory_bytes
        self.file_bytes=file_bytes
        self.isolation_mode=normalize_mode(isolation_mode or os.getenv("CAD_V2_ISOLATION", "strict"))
        self._semaphore=asyncio.Semaphore(max_concurrency)

    async def execute(self, ir: Any, output_dir: str | Path,
                      cancel_event: asyncio.Event | None=None,
                      on_stage: Callable[[str],Any] | None=None,
                      execution_options: dict[str,Any] | None=None) -> dict[str,Any]:
        async with self._semaphore:
            return await self._execute(ir,output_dir,cancel_event,on_stage,execution_options)

    async def _execute(self, ir: Any, output_dir: str | Path,
                       cancel_event: asyncio.Event | None,
                       on_stage: Callable[[str],Any] | None,
                       execution_options: dict[str,Any] | None) -> dict[str,Any]:
        path=Path(output_dir)
        if path.is_symlink():
            return _failure("InfrastructureError","Job output directory cannot be a symlink")
        if path.exists() and any(path.iterdir()):
            return _failure("InfrastructureError","Job output directory must be empty")
        path=path.resolve()
        path.mkdir(parents=True,exist_ok=True)
        path.chmod(0o700)
        if cancel_event is not None and cancel_event.is_set():
            shutil.rmtree(path)
            return {**_failure("ExecutionCancelledError","Job cancelled"),"status":"cancelled"}
        request={"ir":ir.model_dump(mode="json") if hasattr(ir,"model_dump") else ir,
                 "output_dir":str(path),"execution_options":execution_options or {},
                 "limits":{"cpu_seconds":max(1,math.ceil(self.timeout_seconds)),"memory_bytes":self.memory_bytes,"file_bytes":self.file_bytes}}
        (path/"input.json").write_text(json.dumps(request,ensure_ascii=False,allow_nan=False))
        backend_root=Path(__file__).resolve().parents[3]
        # Credentials and proxy settings are not inherited by a deterministic geometry worker.
        env={"PATH":"/usr/local/bin:/usr/bin:/bin", "LANG":"C.UTF-8"}
        env.update({"PYTHONPATH":str(backend_root),"OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1","TMPDIR":str(path),"HOME":str(path),"PYTHONDONTWRITEBYTECODE":"1","PYTHONNOUSERSITE":"1"})
        process: asyncio.subprocess.Process | None=None
        tasks: list[asyncio.Task[Any]]=[]
        start=time.perf_counter()
        deadline=start+self.timeout_seconds
        isolation: dict[str,Any]={"requested_mode":self.isolation_mode,"mode":"unavailable","os_enforced":False}
        async def kill() -> None:
            if process is not None and process.returncode is None:
                try:
                    os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        try:
            process=await asyncio.create_subprocess_exec(self.python_executable,"-s","-m","app.infrastructure.cad_v2.sandbox","--mode",self.isolation_mode,"--job-dir",str(path),"--input",str(path/"input.json"),cwd=path,env=env,stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
            stage_queue: asyncio.Queue[str | None]=asyncio.Queue(maxsize=4)
            async def read_progress() -> None:
                nonlocal isolation
                assert process is not None and process.stdout is not None
                buffer=b""
                discarding=False
                seen=set()
                # Drain even malformed/oversized records. readline() stops on
                # overlong lines and can deadlock Process.wait after SIGKILL.
                while chunk:=await process.stdout.read(4096):
                    buffer+=chunk
                    while b"\n" in buffer:
                        line,buffer=buffer.split(b"\n",1)
                        drop=discarding or len(line)>16384
                        discarding=False
                        if drop:
                            continue
                        try:
                            event=json.loads(line)
                        except (ValueError,UnicodeDecodeError):
                            continue
                        if not isinstance(event,dict):
                            continue
                        if isinstance(event.get("isolation"),dict):
                            isolation=event["isolation"]
                        stage=event.get("stage")
                        if isinstance(stage,str) and stage in {"building","validating","exporting"} and stage not in seen:
                            seen.add(stage)
                            stage_queue.put_nowait(stage)
                    if len(buffer)>16384:
                        buffer=b""
                        discarding=True
                stage_queue.put_nowait(None)
            async def notify_stages() -> None:
                while (stage:=await stage_queue.get()) is not None:
                    if on_stage:
                        result=on_stage(stage)
                        if inspect.isawaitable(result):
                            await result
            async def read_errors() -> str:
                assert process is not None and process.stderr is not None
                tail=b""
                while chunk:=await process.stderr.read(4096):
                    tail=(tail+chunk)[-16000:]
                return tail.decode(errors="replace")
            progress=asyncio.create_task(read_progress())
            errors=asyncio.create_task(read_errors())
            callbacks=asyncio.create_task(notify_stages())
            finished=asyncio.create_task(process.wait())
            tasks=[progress,errors,callbacks,finished]
            waiting={finished}
            if cancel_event is not None:
                cancel=asyncio.create_task(cancel_event.wait())
                tasks.append(cancel)
                waiting.add(cancel)
            done,_=await asyncio.wait(waiting,timeout=max(0,deadline-time.perf_counter()),return_when=asyncio.FIRST_COMPLETED)
            if not done:
                await kill()
                report=_failure("ExecutionTimeoutError","CAD execution exceeded its wall-clock budget",timeout_seconds=self.timeout_seconds)
            elif finished not in done:
                await kill()
                report={**_failure("ExecutionCancelledError","Job cancelled"),"status":"cancelled"}
            else:
                _,pending=await asyncio.wait({progress,errors,callbacks},timeout=max(0,deadline-time.perf_counter()))
                if pending:
                    await kill()
                    report=_failure("ExecutionTimeoutError","CAD execution exceeded its wall-clock budget",timeout_seconds=self.timeout_seconds)
                    stderr=""
                else:
                    await progress
                    await callbacks
                    stderr=await errors
                if pending:
                    pass
                elif (path/"result.json").is_file():
                    report=json.loads((path/"result.json").read_text())
                    if report.get("status")=="completed" and process.returncode!=0:
                        report=_failure("InfrastructureError","CAD worker reported success but exited abnormally",
                                        returncode=process.returncode,diagnostic=stderr[-2000:])
                else:
                    cpu_exhausted = process.returncode == -signal.SIGXCPU
                    unavailable=process.returncode==78
                    report=_failure("ExecutionTimeoutError" if cpu_exhausted else "IsolationUnavailableError" if unavailable else "InfrastructureError",
                                    "CAD worker exhausted its CPU budget" if cpu_exhausted else "Strict OS isolation is unavailable; worker was not started" if unavailable else "CAD worker exited without a result",
                                    returncode=process.returncode,diagnostic=stderr[-2000:])
            report["isolation"]=isolation
            report["executor_elapsed_seconds"]=round(time.perf_counter()-start,6)
            if report["status"]=="completed":
                if self.isolation_mode=="strict" and not isolation.get("os_enforced"):
                    shutil.rmtree(path,ignore_errors=True)
                    return {**_failure("IsolationUnavailableError","Worker did not attest strict OS isolation"),"isolation":isolation}
                (path/"input.json").unlink(missing_ok=True)
                (path/"result.json").write_text(json.dumps(report,ensure_ascii=False,allow_nan=False))
            else:
                shutil.rmtree(path,ignore_errors=True)
            return report
        except asyncio.CancelledError:
            await kill()
            shutil.rmtree(path,ignore_errors=True)
            raise
        except Exception as exc:
            await kill()
            shutil.rmtree(path,ignore_errors=True)
            return {**_failure("InfrastructureError",str(exc),exception=type(exc).__name__),"isolation":isolation}
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks,return_exceptions=True)
