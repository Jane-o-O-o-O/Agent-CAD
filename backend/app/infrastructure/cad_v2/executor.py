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


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"status":"failed","error":{"code":code,"message":message,"feature_id":None,"recoverable":False,"details":details}}


class CADExecutor:
    def __init__(self, timeout_seconds: float=60, max_concurrency: int=2,
                 python_executable: str | None=None, memory_bytes: int=4*1024**3,
                 file_bytes: int=128*1024**2) -> None:
        if timeout_seconds<=0 or max_concurrency<1:
            raise ValueError("Execution timeout and concurrency must be positive")
        self.timeout_seconds=timeout_seconds
        self.python_executable=python_executable or os.getenv("CAD_V2_PYTHON",sys.executable)
        self.memory_bytes=memory_bytes
        self.file_bytes=file_bytes
        self._semaphore=asyncio.Semaphore(max_concurrency)

    async def execute(self, ir: Any, output_dir: str | Path,
                      cancel_event: asyncio.Event | None=None,
                      on_stage: Callable[[str],Any] | None=None) -> dict[str,Any]:
        async with self._semaphore:
            return await self._execute(ir,output_dir,cancel_event,on_stage)

    async def _execute(self, ir: Any, output_dir: str | Path,
                       cancel_event: asyncio.Event | None,
                       on_stage: Callable[[str],Any] | None) -> dict[str,Any]:
        path=Path(output_dir)
        if path.is_symlink():
            return _failure("InfrastructureError","Job output directory cannot be a symlink")
        if path.exists() and any(path.iterdir()):
            return _failure("InfrastructureError","Job output directory must be empty")
        path=path.resolve()
        path.mkdir(parents=True,exist_ok=True)
        if cancel_event is not None and cancel_event.is_set():
            shutil.rmtree(path)
            return {**_failure("ExecutionCancelledError","Job cancelled"),"status":"cancelled"}
        request={"ir":ir.model_dump(mode="json") if hasattr(ir,"model_dump") else ir,
                 "output_dir":str(path),"limits":{"cpu_seconds":max(1,math.ceil(self.timeout_seconds)),"memory_bytes":self.memory_bytes,"file_bytes":self.file_bytes}}
        (path/"input.json").write_text(json.dumps(request,ensure_ascii=False,allow_nan=False))
        backend_root=Path(__file__).resolve().parents[3]
        # Credentials and proxy settings are not inherited by a deterministic geometry worker.
        env={key:os.environ[key] for key in ("PATH","LANG","LC_ALL","LD_LIBRARY_PATH","SYSTEMROOT") if key in os.environ}
        env.update({"PYTHONPATH":str(backend_root),"OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1","TMPDIR":str(path),"PYTHONDONTWRITEBYTECODE":"1"})
        process: asyncio.subprocess.Process | None=None
        tasks: list[asyncio.Task[Any]]=[]
        start=time.perf_counter()
        async def kill() -> None:
            if process is not None and process.returncode is None:
                try:
                    os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        try:
            process=await asyncio.create_subprocess_exec(self.python_executable,"-m","app.infrastructure.cad_v2.worker","--input",str(path/"input.json"),cwd=path,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
            async def read_progress() -> None:
                assert process is not None and process.stdout is not None
                async for line in process.stdout:
                    try:
                        event=json.loads(line)
                    except (ValueError,UnicodeDecodeError):
                        continue
                    if event.get("stage") in {"building","validating","exporting"} and on_stage:
                        result=on_stage(event["stage"])
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
            finished=asyncio.create_task(process.wait())
            tasks=[progress,errors,finished]
            waiting={finished}
            if cancel_event is not None:
                cancel=asyncio.create_task(cancel_event.wait())
                tasks.append(cancel)
                waiting.add(cancel)
            done,_=await asyncio.wait(waiting,timeout=self.timeout_seconds,return_when=asyncio.FIRST_COMPLETED)
            if not done:
                await kill()
                report=_failure("ExecutionTimeoutError","CAD execution exceeded its wall-clock budget",timeout_seconds=self.timeout_seconds)
            elif finished not in done:
                await kill()
                report={**_failure("ExecutionCancelledError","Job cancelled"),"status":"cancelled"}
            else:
                await progress
                stderr=await errors
                if (path/"result.json").is_file():
                    report=json.loads((path/"result.json").read_text())
                else:
                    cpu_exhausted = process.returncode == -signal.SIGXCPU
                    report=_failure("ExecutionTimeoutError" if cpu_exhausted else "InfrastructureError",
                                    "CAD worker exhausted its CPU budget" if cpu_exhausted else "CAD worker exited without a result",
                                    returncode=process.returncode,diagnostic=stderr[-2000:])
            report["executor_elapsed_seconds"]=round(time.perf_counter()-start,6)
            if report["status"]=="completed":
                (path/"input.json").unlink(missing_ok=True)
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
            return _failure("InfrastructureError",str(exc),exception=type(exc).__name__)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks,return_exceptions=True)
