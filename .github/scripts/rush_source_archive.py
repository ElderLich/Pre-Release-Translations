#!/usr/bin/env python3
"""Fetch a fresh Rush commit through GitLab's archive API when Git stalls."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import urllib.request

API = "https://code.moenext.com/api/v4/projects/mycard%2Fygopro-rush-duel/repository"
REVISION_FILE = ".rush-source-revision"
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024


def request_bytes(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "PreReleaseTranslations-RushArchive/1.0",
                                                  "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Rush source response exceeds the download size limit")
    return data


def extract_archive(data: bytes, revision: str, target: Path) -> None:
    if target.exists():
        raise ValueError(f"Refusing to replace existing source checkout: {target}")
    expected_root = f"ygopro-rush-duel-{revision}-{revision}"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 20000 or sum(member.size for member in members) > MAX_EXPANDED:
            raise ValueError("Rush source archive exceeds the extraction size limit")
        paths = []
        seen = set()
        for member in members:
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != expected_root or ".." in parts or
                    "\\" in member.name or ":" in member.name or ".git" in parts or
                    REVISION_FILE in parts or not (member.isfile() or member.isdir())):
                raise ValueError(f"Unexpected Rush archive entry: {member.name}")
            relative = Path(*parts[1:])
            if relative in seen or (len(parts) == 1 and not member.isdir()):
                raise ValueError(f"Duplicate or invalid Rush archive entry: {member.name}")
            seen.add(relative)
            paths.append((member, target / relative))
        target.mkdir()
        for member, path in paths:
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, path.open("xb") as output:
                    shutil.copyfileobj(source, output)
    for relative in ("RD Patch.cdb", "sets.md", "script/RDBase.lua"):
        if not (target / relative).is_file():
            raise ValueError(f"Rush source archive is missing {relative}")
    (target / REVISION_FILE).write_text(revision + "\n", encoding="ascii")


def fetch_archive(target: Path) -> None:
    branch = json.loads(request_bytes(API + "/branches/master", 1024 * 1024))
    commit = branch.get("commit") if isinstance(branch, dict) else None
    revision = commit.get("id") if isinstance(commit, dict) else None
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("GitLab did not return a valid current Rush commit")
    print(f"Fetching Rush archive at current commit {revision}", flush=True)
    data = request_bytes(API + "/archive.tar.gz?sha=" + revision, MAX_ARCHIVE)
    extract_archive(data, revision, target)
    print(f"Verified fresh Rush archive: {revision}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    try:
        fetch_archive(args.target)
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"Rush archive fetch failed: {error}\n")
