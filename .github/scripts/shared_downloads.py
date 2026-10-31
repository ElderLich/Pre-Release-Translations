#!/usr/bin/env python3
"""Verified downloads shared by the local and cloud AutoPR helpers."""

from __future__ import annotations

import http.client
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path

# Both endpoints publish the same MyCard distribution. Keep TLS verification on.
UPSTREAM_BASES = ("https://cdn02.moecube.com:444/ygopro-super-pre/",
                  "https://cdntx2.moecube.com/ygopro-super-pre/")
YPK_URL_DEFAULT = UPSTREAM_BASES[0] + "archive/ygopro-super-pre.ypk"
JSON_URL_DEFAULT = UPSTREAM_BASES[0] + "data/test-release.json"
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_TIMEOUT = 20
# A read timeout alone does not stop a server that trickles bytes indefinitely.
# Bound each attempt separately so a slow first endpoint cannot consume the
# backup endpoint's time. With three attempts this permits up to six minutes,
# plus backoff and in-flight reads (up to one socket timeout per attempt).
DOWNLOAD_BUDGET = 120
MAX_DOWNLOAD_BYTES = 1024**3
CDB_VALIDATION_BUDGET = 10
YPK_MEMBERS = ("test-release.cdb", "test-strings.conf", "test-update.cdb")


class DownloadError(RuntimeError):
    """The upstream response could not be downloaded or validated."""


def _cache_directory() -> Path | None:
    """Opt-in cache shared by retry processes, never by different Actions runs.

    Local callers must provide a fresh directory per update operation. Actions
    additionally scope it by run/attempt even if a reusable directory is supplied.
    """
    directory = os.environ.get("AUTOPR_DOWNLOAD_CACHE")
    if not directory:
        return None
    root = Path(directory)
    run = os.environ.get("GITHUB_RUN_ID")
    if run:
        scope = hashlib.sha256((run + ":" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")).encode()).hexdigest()
        root /= scope
    return root


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".cache-", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate(path: Path, url: str, target: Path, validator) -> None:
    if target.suffix.lower() == ".ypk" or urlsplit(url).path.lower().endswith(".ypk"):
        validate_ypk(path)
    if target.suffix.lower() == ".cdb" or urlsplit(url).path.lower().endswith(".cdb"):
        validate_cdb(path)
    if validator is not None:
        validator(path)


def _cached_file(directory, url, target, validator, log_fn) -> bool:
    key = hashlib.sha256(url.encode()).hexdigest()
    payload, metadata = directory / (key + ".data"), directory / (key + ".json")
    temporary = None
    if not payload.exists() and not metadata.exists():
        return False
    try:
        if payload.is_symlink() or metadata.is_symlink() or metadata.stat().st_size > 16 * 1024:
            raise DownloadError("unsafe cache entry")
        info = json.loads(metadata.read_text(encoding="utf-8"))
        if (not isinstance(info, dict) or info.get("url") != url or
                not 0 < payload.stat().st_size <= MAX_DOWNLOAD_BYTES or
                info.get("size") != payload.stat().st_size):
            raise DownloadError("cache identity or size mismatch")
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False) as output:
            temporary = Path(output.name)
        shutil.copyfile(payload, temporary)
        if _digest(temporary) != info.get("sha256"):
            raise DownloadError("cache checksum mismatch")
        # Run the caller's current validator, including for cached JSON inputs.
        _validate(temporary, url, target, validator)
        temporary.replace(target)
        log_fn(f"Reused validated download for {url} ({info['size']:,} bytes)")
        return True
    except (OSError, ValueError, DownloadError) as exc:
        log_fn(f"Ignoring unusable download cache ({type(exc).__name__}); downloading again")
        for path in (payload, metadata):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _remember_download(directory, url, target, candidate, log_fn) -> None:
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode()).hexdigest()
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".cache-", delete=False) as output:
            temporary = Path(output.name)
        shutil.copyfile(target, temporary)
        info = {"url": url, "size": temporary.stat().st_size, "sha256": _digest(temporary)}
        temporary.replace(directory / (key + ".data"))
        _atomic_write(directory / (key + ".json"), json.dumps(info).encode())
        # Only trust one of our known endpoints after the actual bytes validated.
        for base in UPSTREAM_BASES:
            if candidate.startswith(base):
                _atomic_write(directory / "preferred-endpoint.json", json.dumps(base).encode())
                break
    except OSError:
        log_fn("Could not save the optional download cache; validated output is available")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _preferred_urls(url, directory):
    candidates = download_urls(url)
    if directory is not None and len(candidates) > 1:
        try:
            path = directory / "preferred-endpoint.json"
            if path.stat().st_size > 1024:
                return candidates
            preferred = json.loads(path.read_text(encoding="utf-8"))
            if preferred in UPSTREAM_BASES:
                candidates = tuple(sorted(candidates, key=lambda candidate: not candidate.startswith(preferred)))
        except (OSError, ValueError):
            pass
    return candidates


def download_urls(url: str) -> tuple[str, ...]:
    """Only use known upstream alternatives; explicit custom URLs stay isolated."""
    for base in UPSTREAM_BASES:
        if url.startswith(base):
            suffix = url[len(base):]
            return (url, *(other + suffix for other in UPSTREAM_BASES if other != base))
    return (url,)


def validate_ypk(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            missing = set(YPK_MEMBERS) - set(archive.namelist())
            if missing:
                raise DownloadError(f"YPK is missing required files: {', '.join(sorted(missing))}")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise DownloadError(f"YPK checksum failed for {bad_member}")
    except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError, zlib.error) as exc:
        raise DownloadError(f"Invalid YPK archive: {exc}") from exc


def validate_cdb(path: Path) -> None:
    """Check card databases without modifying them or creating SQLite sidecars."""
    try:
        with path.open("rb") as source:
            if source.read(16) != b"SQLite format 3\x00":
                raise DownloadError("Invalid CDB: missing SQLite header")
        deadline = time.monotonic() + CDB_VALIDATION_BUDGET
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)
        try:
            connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
                raise DownloadError("Invalid CDB: SQLite integrity check failed")
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name IN ('datas','texts')"
            )}
            if tables != {"datas", "texts"}:
                raise DownloadError("Invalid CDB: required datas/texts tables are missing")
            connection.execute("SELECT id FROM datas LIMIT 0")
            connection.execute("SELECT id,name,desc FROM texts LIMIT 0")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise DownloadError(f"Invalid CDB or validation exceeded {CDB_VALIDATION_BUDGET}s: {exc}") from exc


def download_file(
    url: str,
    target: Path,
    *,
    user_agent: str,
    log_fn: Callable[[str], None],
    validator: Callable[[Path], None] | None = None,
) -> None:
    """Retry bounded transfers; reuse only integrity-checked, validated inputs."""
    target.parent.mkdir(parents=True, exist_ok=True)
    directory = _cache_directory()
    if directory is not None and _cached_file(directory, url, target, validator, log_fn):
        return
    candidates = _preferred_urls(url, directory)
    started = time.monotonic()
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        attempt_started = time.monotonic()
        deadline = attempt_started + DOWNLOAD_BUDGET
        candidate = candidates[(attempt - 1) % len(candidates)]
        log_fn(f"Downloading {candidate} (attempt {attempt}/{DOWNLOAD_ATTEMPTS}, {DOWNLOAD_BUDGET}s budget)")
        partial: Path | None = None
        final_url = candidate
        received = 0
        next_progress = attempt_started + 10
        try:
            request = urllib.request.Request(candidate, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(request, timeout=min(DOWNLOAD_TIMEOUT, max(0.1, deadline - time.monotonic()))) as response:
                final_url = response.geturl()
                if final_url != candidate:
                    log_fn(f"Download redirected to {final_url}")
                if response.status not in (None, 200):
                    raise DownloadError(f"Expected a complete response, got HTTP {response.status}")
                content_type = response.headers.get("Content-Type", "unknown")
                length = response.headers.get("Content-Length")
                if length is not None and not length.isdigit():
                    raise DownloadError(f"Invalid Content-Length: {length!r}")
                expected = int(length) if length is not None else None
                if expected is not None and expected > MAX_DOWNLOAD_BYTES:
                    raise DownloadError("Download exceeds the maximum supported size")
                with tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False
                ) as output:
                    partial = Path(output.name)
                    # HTTPResponse.read1 performs at most one underlying read;
                    # read(n) can keep accumulating trickled data past a deadline.
                    read = getattr(response, "read1", response.read)
                    while True:
                        if time.monotonic() >= deadline:
                            raise DownloadError(f"Attempt budget of {DOWNLOAD_BUDGET}s exhausted")
                        chunk = read(256 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        received += len(chunk)
                        if received > MAX_DOWNLOAD_BYTES or (expected is not None and received > expected):
                            raise DownloadError("Response exceeds its expected or supported size")
                        now = time.monotonic()
                        if now >= next_progress:
                            progress = (f"{received:,} of {expected:,} bytes ({received / expected:.0%})"
                                        if expected else f"{received:,} bytes")
                            log_fn(f"Download progress: {progress}, {now - attempt_started:.1f}s "
                                   f"(attempt {attempt}/{DOWNLOAD_ATTEMPTS})")
                            next_progress = now + 10
                # Sized HTTP reads can silently reach EOF before Content-Length.
                if expected is not None and received != expected:
                    raise DownloadError(
                        f"Incomplete download: received {received:,} of {expected:,} bytes "
                        f"(Content-Type: {content_type})"
                    )
                if received == 0:
                    raise DownloadError("Downloaded file is empty")
            _validate(partial, url, target, validator)
            partial.replace(target)
            if directory is not None:
                _remember_download(directory, url, target, candidate, log_fn)
            log_fn(f"Downloaded {target} ({received:,} bytes, validated, {time.monotonic() - started:.1f}s elapsed)")
            return
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            ConnectionError,
            DownloadError,
        ) as exc:
            message = (f"Download failed from {final_url} after {time.monotonic() - attempt_started:.1f}s "
                       f"({received:,} bytes received): {exc}")
            log_fn(message)
            if attempt == DOWNLOAD_ATTEMPTS:
                raise DownloadError(
                    f"Unable to download {url} after {DOWNLOAD_ATTEMPTS} attempts. {message}"
                ) from exc
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)
        time.sleep(attempt)


def fetch_release_json(url: str, *, user_agent: str, log_fn: Callable[[str], None]) -> list[dict]:
    """Metadata uses the same verified endpoints/retries as the package."""
    def validate(path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as error:
            raise DownloadError(f"Invalid release JSON: {error}") from error
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise DownloadError("Release JSON must be a list of card objects")

    with tempfile.TemporaryDirectory(prefix="superpre-metadata-") as directory:
        target = Path(directory) / "test-release.json"
        download_file(url, target, user_agent=user_agent, log_fn=log_fn, validator=validate)
        return json.loads(target.read_text(encoding="utf-8"))
