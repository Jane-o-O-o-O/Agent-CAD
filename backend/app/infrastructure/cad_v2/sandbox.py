"""Unprivileged, fail-closed Linux confinement for the trusted IR worker.

``strict`` requires Landlock ABI 3 and libseccomp. It never silently becomes a
plain subprocess. ``process-dev`` is an explicit development-only opt-out.
The policy confines filesystem *access*, not metadata visibility or CPU side
channels; it is not a claim of safe execution of arbitrary uploaded Python.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import json
import os
from pathlib import Path
import platform
import runpy
import socket
import sys
import sysconfig
from typing import Any


class IsolationUnavailable(RuntimeError):
    pass


def normalize_mode(mode: str) -> str:
    if mode not in {"strict", "process-dev"}:
        raise ValueError("CAD_V2_ISOLATION must be strict or process-dev")
    return mode


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def _check(result: int, operation: str) -> int:
    if result < 0:
        code = ctypes.get_errno()
        raise IsolationUnavailable(f"{operation}: {os.strerror(code)} (errno={code})")
    return result


def landlock_abi() -> int:
    if sys.platform != "linux" or platform.machine() not in {"x86_64", "aarch64"}:
        raise IsolationUnavailable("Strict isolation requires Linux x86_64 or aarch64")
    return _check(_libc().syscall(444, 0, 0, 1), "Landlock ABI query")


def _no_privileges() -> None:
    libc = _libc()
    _check(libc.prctl(38, 1, 0, 0, 0), "PR_SET_NO_NEW_PRIVS")
    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]
    header = Header(0x20080522, 0)
    data = (Data * 2)()
    _check(libc.capset(ctypes.byref(header), ctypes.byref(data)), "Drop capabilities")


def runtime_read_paths() -> list[Path]:
    """Only interpreter/dependency trees and application code, never / or /home."""
    values = [Path(sysconfig.get_path(key)) for key in ("stdlib", "platstdlib", "purelib", "platlib")]
    values += [Path(__file__).resolve().parents[2],
              Path("/usr/lib"), Path("/usr/lib64"), Path("/lib"), Path("/lib64"),
              Path("/etc/ld.so.cache"), Path("/etc/localtime"), Path("/dev/urandom")]
    paths = {p.resolve() for p in values if p.exists()}
    if Path("/") in paths:
        raise IsolationUnavailable("Runtime allowlist must not contain the filesystem root")
    return sorted(paths, key=str)


def _landlock(job_dir: Path, probe_runtime: Path | None = None) -> int:
    abi = landlock_abi()
    if abi < 3:
        raise IsolationUnavailable(f"Landlock ABI >= 3 required for truncate restrictions; found {abi}")
    libc = _libc()
    # Handle every filesystem right understood by this ABI (including REFER,
    # TRUNCATE and device ioctls) so missing allow-rules deny those operations.
    handled = (1 << (16 if abi >= 5 else 15)) - 1
    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]
    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    attr = Ruleset(handled)
    ruleset = _check(libc.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0), "Landlock create ruleset")
    def allow(path: Path, access: int) -> None:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = PathRule(access, fd)
            _check(libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0), f"Landlock allow {path}")
        finally:
            os.close(fd)
    try:
        for path in runtime_read_paths():
            allow(path, (1 << 2) | ((1 << 3) if path.is_dir() else 0))
        if probe_runtime is not None:
            allow(probe_runtime, 1 << 2)
        # The job owns its artifacts and temporary files, but may not create
        # devices, sockets, FIFOs or execute files even within its private dir.
        job_rights = sum(1 << bit for bit in (1, 2, 3, 4, 5, 7, 8, 12, 13, 14))
        allow(job_dir, job_rights)
        allow(Path("/dev/null"), (1 << 1) | (1 << 2))
        _check(libc.syscall(446, ruleset, 0), "Landlock restrict self")
    finally:
        os.close(ruleset)
    return abi


def install_seccomp() -> None:
    """Default-deny syscall policy; new kernel calls cannot expand privileges."""
    name = ctypes.util.find_library("seccomp")
    if not name:
        raise IsolationUnavailable("libseccomp.so.2 is required for strict isolation")
    sec = ctypes.CDLL(name, use_errno=True)
    sec.seccomp_init.argtypes = [ctypes.c_uint32]
    sec.seccomp_init.restype = ctypes.c_void_p
    sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sec.seccomp_syscall_resolve_name.restype = ctypes.c_int
    sec.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    sec.seccomp_load.argtypes = [ctypes.c_void_p]
    sec.seccomp_release.argtypes = [ctypes.c_void_p]
    class Compare(ctypes.Structure):
        _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int), ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]
    sec.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(Compare)]
    allowed = 0x7fff0000
    context = sec.seccomp_init(0x00050000 | errno.EPERM)
    if not context:
        raise IsolationUnavailable("seccomp_init failed")
    def rule(name: str, action: int = allowed, comparison=None, required=False) -> None:
        number = sec.seccomp_syscall_resolve_name(name.encode())
        if number == -1 and required:
            raise IsolationUnavailable(f"libseccomp cannot resolve required syscall {name}")
        if number >= 0:
            result = (sec.seccomp_rule_add(context, action, number, 0) if comparison is None else
                      sec.seccomp_rule_add_array(context, action, number, 1, ctypes.byref(comparison)))
            if result < 0:
                raise IsolationUnavailable(f"seccomp rule {name}: {os.strerror(-result)}")
    try:
        # Filesystem content access is mediated by Landlock. Metadata mutation,
        # sockets, exec, namespace changes, cross-process operations and kernel
        # interfaces such as io_uring are absent and therefore denied.
        for call in ("read", "write", "readv", "writev", "pread64", "pwrite64", "preadv", "pwritev", "preadv2", "pwritev2",
                     "open", "openat", "openat2", "close", "close_range", "lseek", "stat", "lstat", "fstat", "newfstatat", "statx",
                     "getdents", "getdents64", "readlink", "readlinkat", "access", "faccessat", "faccessat2", "getcwd", "chdir", "fchdir",
                     "mkdir", "mkdirat", "rmdir", "unlink", "unlinkat", "rename", "renameat", "renameat2", "link", "linkat", "symlink", "symlinkat",
                     "truncate", "ftruncate", "fallocate", "fsync", "fdatasync", "flock", "dup", "dup2", "dup3", "pipe", "pipe2",
                     "mmap", "mprotect", "munmap", "mremap", "msync", "madvise", "brk", "mincore",
                     "rt_sigaction", "rt_sigprocmask", "rt_sigreturn", "sigaltstack", "rt_sigsuspend", "rt_sigpending", "restart_syscall",
                     "futex", "futex_waitv", "set_tid_address", "set_robust_list", "get_robust_list", "rseq",
                     "getpid", "getppid", "gettid", "getuid", "geteuid", "getgid", "getegid", "getresuid", "getresgid", "getgroups", "capget",
                     "getrandom", "clock_gettime", "clock_getres", "gettimeofday", "time", "nanosleep", "clock_nanosleep",
                     "sched_yield", "sched_getaffinity", "sched_getparam", "sched_getscheduler", "sched_get_priority_min", "sched_get_priority_max",
                     "uname", "sysinfo", "getrlimit", "setrlimit", "getrusage", "times",
                     "poll", "ppoll", "select", "pselect6", "eventfd", "eventfd2", "epoll_create", "epoll_create1", "epoll_ctl", "epoll_wait", "epoll_pwait", "epoll_pwait2",
                     "landlock_create_ruleset", "landlock_add_rule", "landlock_restrict_self", "exit", "exit_group"):
            rule(call)
        # libc falls back to clone for pthreads when clone3 is unavailable.
        rule("clone3", 0x00050000 | errno.ENOSYS, required=True)
        rule("clone", comparison=Compare(0, 7, 0x00010000, 0x00010000), required=True)
        rule("tgkill", comparison=Compare(0, 4, os.getpid(), 0), required=True)
        rule("prlimit64", comparison=Compare(0, 4, 0, 0), required=True)
        # Do not expose fcntl ownership/asynchronous signal delivery or leases.
        for command in (0, 1, 2, 3, 4, 5, 6, 7, 1030):
            rule("fcntl", comparison=Compare(1, 4, command, 0), required=True)
        result = sec.seccomp_load(context)
        if result < 0:
            raise IsolationUnavailable(f"seccomp_load: {os.strerror(-result)}")
    finally:
        sec.seccomp_release(context)


def confine(job_dir: Path, probe_runtime: Path | None = None) -> dict[str, Any]:
    # Load seccomp before Landlock denies discovery helpers (/sbin/ldconfig).
    _no_privileges()
    install_seccomp()
    abi = _landlock(job_dir, probe_runtime)
    return {"requested_mode": "strict", "mode": "linux-landlock-seccomp", "os_enforced": True,
            "filesystem": "runtime-read-only-job-write-only", "network": "all-sockets-denied",
            "capabilities": "none", "no_new_privileges": True, "landlock_abi": abi}


def confinement_probe(job_dir: Path, outside: Path, runtime: Path | None = None) -> dict[str, Any]:
    """Real denied operations, intended for deployment gates (no CAD mocking)."""
    results: dict[str, Any] = {}
    for label, operation in (
        ("outside_read_denied", lambda: outside.read_bytes()),
        ("outside_write_denied", lambda: outside.write_bytes(b"forbidden")),
        ("outside_metadata_write_denied", lambda: outside.chmod(0o600)),
        ("runtime_write_denied", lambda: (runtime or Path(__file__)).open("ab")),
        ("ipv4_socket_denied", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)),
        ("ipv6_socket_denied", lambda: socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)),
        ("unix_socket_denied", lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)),
    ):
        try:
            result = operation()
            if hasattr(result, "close"):
                result.close()
            results[label] = False
        except OSError as exc:
            results[label] = exc.errno in {errno.EACCES, errno.EPERM} or (label == "runtime_write_denied" and exc.errno == errno.EROFS)
    own = job_dir / "isolation-probe.txt"
    own.write_text("private job write succeeds")
    results["job_write_read_allowed"] = own.read_text() == "private job write succeeds"
    own.unlink()
    if runtime is not None:
        results["runtime_read_allowed"] = runtime.read_text() == "read-only runtime data"
    results["passed"] = all(results.values())
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--mode", choices=("strict", "process-dev"), default="strict")
    parser.add_argument("--probe-outside")
    parser.add_argument("--probe-runtime")
    args = parser.parse_args()
    if args.probe_runtime and not args.probe_outside:
        parser.error("--probe-runtime is only available with --probe-outside")
    job_dir = Path(args.job_dir).resolve(strict=True)
    try:
        isolation = confine(job_dir, Path(args.probe_runtime).resolve() if args.probe_runtime else None) if args.mode == "strict" else {
            "requested_mode": "process-dev", "mode": "process-dev", "os_enforced": False,
            "filesystem": "unrestricted", "network": "unrestricted", "warning": "Explicit development mode; resource limits only"}
    except Exception as exc:
        print(json.dumps({"isolation_error": str(exc), "mode": "unavailable", "os_enforced": False}), file=sys.stderr, flush=True)
        return 78
    print(json.dumps({"isolation": isolation}), flush=True)
    if args.probe_outside:
        probe = confinement_probe(job_dir, Path(args.probe_outside), Path(args.probe_runtime) if args.probe_runtime else None)
        print(json.dumps({"probe": probe}), flush=True)
        return 0 if probe["passed"] else 1
    if not args.input:
        parser.error("--input is required without --probe-outside")
    sys.argv = ["cad-worker", "--input", args.input]
    runpy.run_module("app.infrastructure.cad_v2.worker", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
