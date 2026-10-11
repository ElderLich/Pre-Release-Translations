"""Local Rush source refresh used by the shared prerelease window."""
from __future__ import annotations

import os
from contextlib import contextmanager
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile


@contextmanager
def build_lock(repo: Path):
    """All locale launchers share a nonblocking OS lock for this repository."""
    key = hashlib.sha256(os.path.normcase(str(repo.resolve())).encode()).hexdigest()[:20]
    path = Path(tempfile.gettempdir()) / f"transsuperpre-rush-{key}.lock"
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another Rush build is already running for this repository. Wait for it to finish.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def python_console() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return str(executable)


def fetch_source(source: Path, log=print) -> None:
    """Fast-forward only: never stash, reset, switch branches or discard edits."""
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes -oConnectTimeout=20")

    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=source, env=env, text=True,
                                encoding="utf-8", errors="replace", capture_output=True,
                                timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
        return result.stdout.strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != source.resolve():
        raise RuntimeError("Choose the ygopro-rush-duel checkout root.")
    if git("branch", "--show-current") != "master":
        raise RuntimeError("Rush source must be on master to fetch updates. Switch it yourself, or use Build All Locales with the current files.")
    if git("status", "--porcelain"):
        raise RuntimeError("Rush source has local changes. Commit or move them before fetching, or use Build All Locales with the current files.")
    log("Fetching Rush upstream (origin/master)...")
    git("fetch", "origin", "master")
    try:
        git("merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")
    except RuntimeError as error:
        raise RuntimeError("Rush source contains local commits or has diverged. Reconcile it in Git before fetching; no files were replaced.") from error
    log(git("merge", "--ff-only", "FETCH_HEAD"))
    log("Rush upstream is current: " + git("rev-parse", "--short", "HEAD"))
