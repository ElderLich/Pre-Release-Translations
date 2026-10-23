"""Reproducible Standard packages and actionable release input checks."""

from __future__ import annotations

import re
import shutil
import time
import zipfile
from pathlib import Path


ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
STRINGS_ENTRY_RE = re.compile(r"^\s*!(?P<directive>[^\s#]+)\s+(?P<code>\S+)(?:\s+(?P<name>.*\S))?\s*$")


def validate_release_strings(path: Path) -> None:
    """Reject incomplete strings, including their exact source locations."""
    seen: dict[str, int] = {}
    issues: list[str] = []
    counts = {"duplicate": 0, "malformed": 0, "missing_translation": 0}
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith("!"):
            continue
        match = STRINGS_ENTRY_RE.match(raw_line)
        if not match:
            counts["malformed"] += 1
            issues.append(f"{path}:{lineno}: malformed string directive: {stripped}")
            continue
        key = f"!{match['directive'].lower()} {match['code'].lower()}"
        if key in seen:
            counts["duplicate"] += 1
            issues.append(f"{path}:{lineno}: duplicate {key} (first defined on line {seen[key]})")
        else:
            seen[key] = lineno
        # A comment after a key is not a translation.
        name = (match["name"] or "").strip()
        if not name or name.startswith("#"):
            counts["missing_translation"] += 1
            issues.append(f"{path}:{lineno}: missing translation for {key}")
    if issues:
        summary = ", ".join(f"{kind}={count}" for kind, count in counts.items())
        raise ValueError(f"{path.name} is not ready: {summary}\n" + "\n".join(issues))


def content_equal(left: bytes, right: bytes) -> bool:
    """Ignore only SQLite header bookkeeping when every other byte is equal.

    UPDATE/VACUUM can advance change counters and the schema cookie without a
    content change. Never ignore schema/data pages, user_version or application_id.
    This deliberately does not attempt to equate differently laid-out databases.
    """
    if left == right:
        return True
    if len(left) != len(right) or len(left) < 100 or not left.startswith(b"SQLite format 3\0") or not right.startswith(b"SQLite format 3\0"):
        return False
    return left[:24] == right[:24] and left[28:40] == right[28:40] and left[44:92] == right[44:92] and left[100:] == right[100:]


def copy_if_changed(source: Path, destination: Path) -> bool:
    if destination.is_file() and content_equal(source.read_bytes(), destination.read_bytes()):
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return True


def write_deterministic_package(base_ypk: Path, output_ypk: Path, payloads: dict[str, Path]) -> None:
    """Replace payloads and normalize metadata/order for *every* ZIP member."""
    output_ypk.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_ypk.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(base_ypk) as source, zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
            names = source.namelist()
            if len(names) != len(set(names)):
                raise ValueError(f"{base_ypk}: duplicate archive member names")
            for name in sorted(set(names) | set(payloads)):
                data = payloads[name].read_bytes() if name in payloads else source.read(name)
                info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o40755 << 16) | 0x10 if name.endswith("/") else 0o100644 << 16
                target.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
        temporary.replace(output_ypk)
    finally:
        if temporary.exists():
            temporary.unlink()


def packages_equal(left: Path, right: Path) -> bool:
    """Compare payloads, so older releases with dated ZIP metadata remain intact."""
    if not left.is_file() or not right.is_file():
        return False
    try:
        with zipfile.ZipFile(left) as previous, zipfile.ZipFile(right) as candidate:
            old_names, new_names = previous.namelist(), candidate.namelist()
            if len(old_names) != len(set(old_names)) or len(new_names) != len(set(new_names)) or set(old_names) != set(new_names):
                return False
            return all(content_equal(previous.read(name), candidate.read(name)) for name in old_names)
    except (OSError, zipfile.BadZipFile):
        return False


def publish_release(candidate_ypk: Path, candidate_json: Path, output_dir: Path, *, now: int | None = None) -> bool:
    """Advance the version only for changed shipped content or a missing version.

    Both artifacts must be built successfully in staging before calling this.
    Keep the exact prior bytes for unchanged artifacts, including legacy ZIPs.
    """
    output_ypk, output_json = output_dir / "ygopro-super-pre.ypk", output_dir / "test-release.json"
    package_changed = not packages_equal(output_ypk, candidate_ypk)
    json_changed = not output_json.is_file() or output_json.read_bytes() != candidate_json.read_bytes()
    version_path = output_dir / "version.txt"
    try:
        previous_version = int(version_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        previous_version = 0
    if not package_changed and not json_changed and previous_version > 0:
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    if package_changed:
        shutil.copyfile(candidate_ypk, output_ypk)
    if json_changed:
        shutil.copyfile(candidate_json, output_json)
    version = max(int(time.time()) if now is None else now, previous_version + 1)
    version_path.write_text(str(version), encoding="utf-8", newline="\n")
    return True
