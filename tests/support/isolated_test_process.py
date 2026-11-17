"""Own one integration worker's HOME/XDG, tmux server and interruption cleanup."""
import argparse
import ctypes
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def reap_owned_children():
    if not sys.platform.startswith("linux"):
        return
    children_file = Path(f"/proc/self/task/{os.getpid()}/children")
    for sig in (signal.SIGTERM, signal.SIGKILL):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                while os.waitpid(-1, os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                return
            children = [int(pid) for pid in children_file.read_text().split()]
            for pid in children:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            time.sleep(.02)
    raise RuntimeError("owned test descendants remain")


def run(command, test_name, timeout):
    # Keep Unix socket paths short even if the caller's TMPDIR is a build path.
    root = Path(tempfile.mkdtemp(prefix="fg-test-", dir="/tmp"))
    directories = {name: root / name for name in ("home", "config", "state", "cache", "data", "tmux", "tmp")}
    for path in directories.values():
        path.mkdir(mode=0o700)
    env = dict(os.environ)
    for key in ("TMUX", "TMUX_PANE", "FORAGER_PROFILE", "AGENT_OF_EMPIRES_PROFILE"):
        env.pop(key, None)
    env.update(HOME=str(directories["home"]), XDG_CONFIG_HOME=str(directories["config"]),
               XDG_STATE_HOME=str(directories["state"]), XDG_CACHE_HOME=str(directories["cache"]),
               XDG_DATA_HOME=str(directories["data"]), TMUX_TMPDIR=str(directories["tmux"]),
               TMPDIR=str(directories["tmp"]), FORAGER_PRIVATE_TEST_NAME=test_name,
               FORAGER_PRIVATE_TEST_ROOT=str(root), RUST_TEST_THREADS="1", SHELL="/bin/sh")
    # Reap daemonized tmux descendants after a killed worker on Linux.
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot supervise test descendants")
    worker = None
    interrupted = False

    def stop(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, stop)
    code = 125
    try:
        if shutil.which("tmux"):
            socket_dir = directories["tmux"] / f"tmux-{os.getuid()}"
            socket_dir.mkdir(mode=0o700)
            subprocess.run(["tmux", "-f", "/dev/null", "-S", str(socket_dir / "default"),
                            "new-session", "-d", "-s", "forager_test_guard", "-c", str(root),
                            "exec sleep 600"], env=env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
        worker = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                  start_new_session=True)
        deadline = time.monotonic() + timeout
        while worker.poll() is None:
            if interrupted or time.monotonic() >= deadline:
                break
            readable, _, _ = select.select([sys.stdin.buffer], [], [], .05)
            if readable and not os.read(sys.stdin.fileno(), 1):
                break
        else:
            code = worker.returncode if worker.returncode >= 0 else 128 - worker.returncode
    finally:
        if worker is not None and worker.poll() is None:
            try:
                os.killpg(worker.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(worker.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                worker.wait(timeout=3)
        # An absolute private socket never resolves to the caller's default server.
        sockets = list(directories["tmux"].glob("tmux-*/default"))
        for socket in sockets:
            if socket.is_socket():
                subprocess.run(["tmux", "-S", str(socket), "kill-server"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=5, check=False)
        reap_owned_children()
        for socket in sockets:
            out = subprocess.run(["tmux", "-S", str(socket), "list-sessions"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=5, check=False)
            if out.returncode == 0:
                raise RuntimeError(f"private test server remains: {root}")
        shutil.rmtree(root)
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-name", required=True)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.timeout <= 0:
        parser.error("a worker command and positive timeout are required")
    raise SystemExit(run(command, args.test_name, args.timeout))
