#!/usr/bin/env python3
"""Verified downloads shared by the local and cloud AutoPR helpers."""

from __future__ import annotations

import http.client
import json
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
DOWNLOAD_TIMEOUT = 60
YPK_MEMBERS = ("test-release.cdb", "test-strings.conf", "test-update.cdb")


class DownloadError(RuntimeError):
    """The upstream response could not be downloaded or validated."""


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


def download_file(
    url: str,
    target: Path,
    *,
    user_agent: str,
    log_fn: Callable[[str], None],
    validator: Callable[[Path], None] | None = None,
) -> None:
    """Retry incomplete transfers; publish the target only after validation."""
    target.parent.mkdir(parents=True, exist_ok=True)
    candidates = download_urls(url)
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        candidate = candidates[(attempt - 1) % len(candidates)]
        log_fn(f"Downloading {candidate} (attempt {attempt}/{DOWNLOAD_ATTEMPTS})")
        partial: Path | None = None
        final_url = candidate
        try:
            request = urllib.request.Request(candidate, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
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
                received = 0
                with tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False
                ) as output:
                    partial = Path(output.name)
                    while chunk := response.read(256 * 1024):
                        output.write(chunk)
                        received += len(chunk)
                # Sized HTTP reads can silently reach EOF before Content-Length.
                if expected is not None and received != expected:
                    raise DownloadError(
                        f"Incomplete download: received {received:,} of {expected:,} bytes "
                        f"(Content-Type: {content_type})"
                    )
                if received == 0:
                    raise DownloadError("Downloaded file is empty")
            if target.suffix.lower() == ".ypk" or urlsplit(url).path.lower().endswith(".ypk"):
                validate_ypk(partial)
            if validator is not None:
                validator(partial)
            partial.replace(target)
            log_fn(f"Downloaded {target} ({received:,} bytes, validated)")
            return
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            ConnectionError,
            DownloadError,
        ) as exc:
            message = f"Download failed from {final_url}: {exc}"
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
