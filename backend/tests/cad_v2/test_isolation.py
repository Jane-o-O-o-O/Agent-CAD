"""Real kernel enforcement probes; a missing host facility is never called success."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.infrastructure.cad_v2.executor import CADExecutor
from app.infrastructure.cad_v2.sandbox import IsolationUnavailable, landlock_abi, normalize_mode, runtime_read_paths
from .test_executor import box_ir


def child(code):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    return subprocess.run([sys.executable, "-s", "-c", code], env=env, capture_output=True, text=True, timeout=20)


def test_explicit_development_opt_out_and_strict_default(monkeypatch):
    monkeypatch.delenv("CAD_V2_ISOLATION", raising=False)
    assert CADExecutor().isolation_mode == "strict"
    assert CADExecutor(isolation_mode="process-dev").isolation_mode == "process-dev"
    with pytest.raises(ValueError):
        normalize_mode("auto")
    paths = runtime_read_paths()
    assert Path("/") not in paths
    assert Path("/etc") not in paths


def test_real_seccomp_denies_network_process_escape_and_capabilities():
    code = '''
import ctypes, errno, json, os, socket, threading, tempfile
from app.infrastructure.cad_v2.sandbox import _no_privileges, install_seccomp
fd, temporary = tempfile.mkstemp()
os.close(fd)
_no_privileges()
install_seccomp()
result = {}
for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
    try:
        socket.socket(family, socket.SOCK_STREAM)
        result[str(family)] = False
    except OSError as error:
        result[str(family)] = error.errno == errno.EPERM
try:
    os.fork()
    result['fork_denied'] = False
except OSError as error:
    result['fork_denied'] = error.errno == errno.EPERM
try:
    os.execve('/usr/bin/true', ['true'], {})
    result['exec_denied'] = False
except OSError as error:
    result['exec_denied'] = error.errno == errno.EPERM
try:
    os.chmod(temporary, 0o600)
    result['metadata_write_denied'] = False
except OSError as error:
    result['metadata_write_denied'] = error.errno == errno.EPERM
os.unlink(temporary)
state = open('/proc/self/status').read()
result['capabilities_dropped'] = all(f'{key}:\\t0000000000000000' in state for key in ('CapInh','CapPrm','CapEff'))
result['no_new_privileges'] = 'NoNewPrivs:\\t1' in state
libc = ctypes.CDLL(None, use_errno=True)
libc.sigqueue.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
queued = libc.sigqueue(os.getppid(), 0, None)
result['queued_signal_denied'] = queued == -1 and ctypes.get_errno() == errno.EPERM
libc.prlimit.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
limited = libc.prlimit(os.getppid(), 7, None, None)
result['other_process_rlimit_denied'] = limited == -1 and ctypes.get_errno() == errno.EPERM
# New xattr-at operations stay denied even if libseccomp predates their names.
for number in (463, 466):
    denied = libc.syscall(number, -100, b'/does-not-exist', 0, 0, 0, 0)
    result[f'new_syscall_{number}_denied'] = denied == -1 and ctypes.get_errno() == errno.EPERM
thread = threading.Thread(target=lambda: result.update(thread_allowed=True))
thread.start()
thread.join()
print(json.dumps(result))
'''
    result = child(code)
    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert all(proof.values()), proof


def test_real_strict_filesystem_and_network_probes(tmp_path):
    outside = tmp_path / "supervisor-secret"
    outside.write_text("never readable by worker")
    runtime = tmp_path / "runtime-file"
    runtime.write_text("read-only runtime data")
    job = tmp_path / "job"
    job.mkdir(mode=0o700)
    code = f'''
import json
from pathlib import Path
from app.infrastructure.cad_v2.sandbox import confine, confinement_probe, IsolationUnavailable
try:
    isolation = confine(Path({str(job)!r}), Path({str(runtime)!r}))
except IsolationUnavailable as error:
    print(str(error))
    raise SystemExit(78)
print(json.dumps({{"isolation": isolation, "probe": confinement_probe(Path({str(job)!r}), Path({str(outside)!r}), Path({str(runtime)!r}))}}))
'''
    result = child(code)
    if result.returncode == 78:
        if os.getenv("CAD_V2_REQUIRE_STRICT_TESTS") == "1":
            pytest.fail(f"Required strict OS isolation unavailable: {result.stdout} {result.stderr}")
        pytest.skip(f"Strict OS isolation unavailable on this host: {result.stdout.strip()}")
    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert proof["isolation"]["os_enforced"]
    assert proof["probe"]["passed"], proof
    assert outside.read_text() == "never readable by worker"


@pytest.mark.asyncio
async def test_strict_executor_fails_closed_when_landlock_unavailable(tmp_path):
    try:
        available = landlock_abi() >= 3
    except IsolationUnavailable:
        available = False
    if available:
        pytest.skip("Host supplies Landlock; strict success is covered by the real probes")
    stages = []
    result = await CADExecutor(isolation_mode="strict").execute(box_ir(), tmp_path / "strict", on_stage=stages.append)
    assert result["status"] == "failed", result
    assert result["error"]["code"] == "IsolationUnavailableError", result
    assert result["isolation"] == {"requested_mode": "strict", "mode": "unavailable", "os_enforced": False}
    assert not stages
    assert not (tmp_path / "strict").exists()


@pytest.mark.asyncio
async def test_explicit_development_worker_reports_actual_mode(tmp_path):
    result = await CADExecutor(isolation_mode="process-dev").execute(box_ir(), tmp_path / "dev")
    assert result["status"] == "completed", result
    assert result["isolation"]["mode"] == "process-dev"
    assert result["isolation"]["os_enforced"] is False
    assert json.loads((tmp_path / "dev" / "result.json").read_text())["isolation"] == result["isolation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("flood", [False, True])
async def test_worker_output_and_callbacks_cannot_escape_wall_clock_budget(tmp_path, flood):
    wrapper = tmp_path / "lifecycle_fixture.py"
    code = ("os.write(1, b'x' * (2*1024*1024)); time.sleep(30)" if flood else
            "print('null\\n[]\\n42\\n{\"stage\":\"building\"}', flush=True); "
            "(pathlib.Path(sys.argv[sys.argv.index('--job-dir')+1])/'result.json').write_text('{\"status\":\"completed\"}')")
    wrapper.write_text(f"#!{sys.executable}\nimport os,sys,time,pathlib\n{code}\n")
    wrapper.chmod(0o700)
    async def slow_callback(_stage):
        await asyncio.sleep(5)
    start = time.monotonic()
    result = await asyncio.wait_for(CADExecutor(timeout_seconds=.15, python_executable=str(wrapper), isolation_mode="process-dev").execute(
        box_ir(), tmp_path / "output", on_stage=slow_callback), 3)
    assert result["error"]["code"] == "ExecutionTimeoutError", result
    assert time.monotonic() - start < 1
    assert not (tmp_path / "output").exists()


@pytest.mark.asyncio
async def test_abnormal_exit_cannot_publish_a_success_file(tmp_path):
    wrapper = tmp_path / "abnormal_fixture.py"
    wrapper.write_text(f"#!{sys.executable}\nimport pathlib,sys\n"
                       "(pathlib.Path(sys.argv[sys.argv.index('--job-dir')+1])/'result.json').write_text('{\"status\":\"completed\"}')\n"
                       "raise SystemExit(23)\n")
    wrapper.chmod(0o700)
    result = await CADExecutor(python_executable=str(wrapper), isolation_mode="process-dev").execute(box_ir(), tmp_path / "output")
    assert result["status"] == "failed"
    assert result["error"]["details"]["returncode"] == 23
    assert not (tmp_path / "output").exists()
