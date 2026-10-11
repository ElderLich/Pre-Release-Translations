#!/usr/bin/env python3
"""Fetch fresh CI inputs from the upstream project's advertised HTTPS remote."""
from __future__ import annotations

import argparse
from pathlib import Path
import os
import signal
import subprocess
import tempfile
import time

# GitLab advertises git.moenext.com for SSH, code.moenext.com for HTTPS.
RUSH_SOURCE = "https://code.moenext.com/mycard/ygopro-rush-duel.git"
RUSH_TRANSLATIONS = "https://github.com/ElderLich/MDPro3_Data.git"
RUSH_DATABASES = "/Data/locales/*/rush_cards.cdb"
ATTEMPTS = 3
GIT_TIMEOUT = 90
SPARSE_TIMEOUT = 300


def run_git(command: list[str], *, env: dict, timeout: int) -> None:
    # Git spawns transport/index workers. Stop the complete process tree before
    # deleting a timed-out checkout, including on Windows where files stay locked.
    with subprocess.Popen(command, env=env, start_new_session=os.name != "nt") as process:
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait()
            raise
        if code:
            raise subprocess.CalledProcessError(code, command)


def clone_repository(url: str, branch: str, target: Path, *,
                     required: tuple[str, ...], sparse: str | None = None) -> None:
    """Retry in disposable folders; never reset an existing checkout or reuse stale data."""
    target = target.resolve()
    if target.exists():
        raise ValueError(f"Refusing to replace existing source checkout: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    command = ["git", "-c", "http.lowSpeedLimit=1024", "-c", "http.lowSpeedTime=20"]
    for attempt in range(1, ATTEMPTS + 1):
        print(f"Fetching {url} ({attempt}/{ATTEMPTS})", flush=True)
        try:
            with tempfile.TemporaryDirectory(prefix=f".{target.name}-", dir=target.parent) as directory:
                checkout = Path(directory) / "checkout"
                clone = command + ["clone", "--depth", "1", "--single-branch", "--branch", branch]
                if sparse:
                    clone += ["--filter=blob:none", "--sparse"]
                run_git(clone + [url, str(checkout)], timeout=GIT_TIMEOUT, env=env)
                if sparse:
                    print(f"Fetching translated databases in {sparse}", flush=True)
                    run_git(command + ["-C", str(checkout), "sparse-checkout", "set", "--no-cone", sparse],
                            timeout=SPARSE_TIMEOUT, env=env)
                for relative in required:
                    if not (checkout / relative).is_file():
                        raise ValueError(f"Fresh checkout is missing {relative}: {url}")
                checkout.rename(target)
            print(f"Fresh source ready: {target}", flush=True)
            return
        except (subprocess.SubprocessError, ValueError) as error:
            print(f"Source fetch failed: {error}", flush=True)
            if attempt == ATTEMPTS:
                raise RuntimeError(f"Cannot fetch current sources from {url}; no packages were built.") from error
            time.sleep(attempt * 2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--translations-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        clone_repository(RUSH_SOURCE, "master", args.source_root,
                         required=("RD Patch.cdb", "sets.md", "script/RDBase.lua"))
        clone_repository(RUSH_TRANSLATIONS, "main", args.translations_root,
                         sparse=RUSH_DATABASES, required=("Data/locales/en-US/rush_cards.cdb",))
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f"Rush source update failed: {error}\n")


if __name__ == "__main__":
    main()
