"""Read published inputs from one fetched Git commit without discarding local work."""
from contextlib import contextmanager
from pathlib import Path
import re
import subprocess
import tempfile


@contextmanager
def published_snapshot(repo: Path, files: dict[str, bool], *, dry_run=False, revision=None, log=print):
    from local_workspace import child_creationflags, run

    def git(*args, binary=False):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=not binary, creationflags=child_creationflags())

    if revision is None:
        log("Fetching the latest published workspace from origin/main...")
        run(["git", "fetch", "origin", "main"], repo)
        result = git("rev-parse", "--verify", "FETCH_HEAD^{commit}")
        result.check_returncode()
        revision = result.stdout.strip()
        if not dry_run:
            branch = git("branch", "--show-current")
            branch.check_returncode()
            ancestor = git("merge-base", "--is-ancestor", "HEAD", revision)
            if branch.stdout.strip() == "main" and ancestor.returncode == 0:
                merged = git("merge", "--ff-only", revision)
                if merged.returncode == 0:
                    log(merged.stdout.strip())
                else:
                    log("Checkout could not fast-forward; keeping its local work and downloading published inputs separately.")
                    log(merged.stderr.strip())
            else:
                log("Keeping the current branch/local commits; downloading published inputs separately.")
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise ValueError("Invalid published commit in the Rush conflict report; pull published again.")
    log(f"Reading published workspace at {revision[:12]}.")
    with tempfile.TemporaryDirectory(prefix="published-workspace-") as directory:
        snapshot = Path(directory)
        for relative, required in files.items():
            # Callers supply a small fixed inventory, never archive entire repositories.
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or ":" in relative:
                raise ValueError(f"Invalid published workspace path: {relative}")
            exists = git("cat-file", "-e", f"{revision}:{relative}")
            if exists.returncode:
                if not required:
                    continue
                raise ValueError(f"Required published file is missing at {revision[:12]}: {relative}")
            content = git("show", f"{revision}:{relative}", binary=True)
            content.check_returncode()
            target = snapshot / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.stdout)
        yield snapshot, revision
