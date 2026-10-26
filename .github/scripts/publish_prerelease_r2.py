"""Publish one complete, verified prerelease snapshot; promote its catalog last.

Only small conditional S3 PUTs are used. The SDK is optional for validation/tests.
Credentials come from environment variables and are never command arguments.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from http.client import IncompleteRead, RemoteDisconnected
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import zipfile


SOURCE_REPOSITORY = "ElderLich/Pre-Release-Translations"
CATALOG_KEY = "prerelease/catalog-v1.json"
STANDARD_LOCALES = ("DE", "EN", "ES", "FR", "IT", "JP", "KR", "PT", "TH", "ZH-TW")
RUSH_LOCALES = STANDARD_LOCALES + ("ZH-CN",)
KINDS = {
    "standard": (STANDARD_LOCALES, "ygopro-super-pre.ypk", "version.txt", "test-release.cdb"),
    "rush": (RUSH_LOCALES, "rush-pre-release.ypk", "rush-version.txt", "rush-pre-release.cdb"),
}
MAX_PUT_BYTES = 5 * 1024**3 - 1
MAX_CATALOG_BYTES = 1024 * 1024
CHUNK_BYTES = 1024 * 1024
PUBLIC_VERIFY_BUDGET = 120
PUBLIC_VERIFY_ATTEMPTS = 3
RETRYABLE_HTTP_STATUS = (408, 429, 500, 502, 503, 504)


class PublishError(RuntimeError):
    """A controlled diagnostic which contains no credentials or server body."""


class RetryableVerificationError(PublishError):
    """An incomplete transfer may succeed with a fresh full download."""


def canonical_json(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def https_base(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise PublishError("The public base must be an HTTPS URL without credentials, query or fragment.")
    if any(part in (".", "..") for part in parsed.path.split("/")):
        raise PublishError("The public base contains an unsafe path.")
    return value.rstrip("/")


def inventory_paths():
    return [f"{locale}/{name}" for locales, package, version, _ in KINDS.values()
            for locale in locales for name in (package, version)]


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_archive(path, database):
    try:
        with zipfile.ZipFile(path) as archive:
            names = set()
            for item in archive.infolist():
                name = item.filename
                parts = PurePosixPath(name).parts
                if (not name or name.startswith("/") or "\\" in name or ":" in name or
                        any(part in (".", "..") for part in parts) or
                        any(ord(character) < 32 for character in name) or
                        name.casefold() in names or item.flag_bits & 1 or
                        stat.S_ISLNK(item.external_attr >> 16)):
                    raise PublishError(f"Unsafe or duplicate archive entry in {path.name}.")
                names.add(name.casefold())
            if database not in archive.namelist() or archive.getinfo(database).is_dir():
                raise PublishError(f"Required database is missing from {path.name}.")
            if archive.testzip() is not None:
                raise PublishError(f"Archive CRC validation failed for {path.name}.")
            # Check the actual database, not merely a ZIP magic number or filename.
            with tempfile.TemporaryDirectory(prefix="mdpro4-prerelease-db-") as temporary:
                db_path = Path(temporary) / "cards.cdb"
                with archive.open(database) as source, db_path.open("wb") as target:
                    shutil.copyfileobj(source, target, CHUNK_BYTES)
                connection = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
                try:
                    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise PublishError(f"Database integrity validation failed for {path.name}.")
                    connection.execute("SELECT id FROM datas LIMIT 0")
                    connection.execute("SELECT id,name,desc FROM texts LIMIT 0")
                finally:
                    connection.close()
    except PublishError:
        raise
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, sqlite3.Error):
        raise PublishError(f"Invalid prerelease archive: {path.name}.") from None


def build_snapshot(source_dir, source_commit, public_base, source_repository=SOURCE_REPOSITORY):
    if source_repository != SOURCE_REPOSITORY or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise PublishError("Expected the configured source repository and a lowercase 40-character commit SHA.")
    public_base = https_base(public_base)
    root = Path(source_dir).resolve()
    packs, files = [], {}
    for kind, (locales, filename, version_name, database) in KINDS.items():
        for locale in locales:
            path = root / locale / filename
            version_path = root / locale / version_name
            for candidate in (path, version_path):
                if (not candidate.is_file() or candidate.is_symlink() or
                        not candidate.resolve().is_relative_to(root)):
                    raise PublishError(f"Missing or unsafe required input: {locale}/{candidate.name}.")
            size = path.stat().st_size
            if not 0 < size <= MAX_PUT_BYTES:
                raise PublishError(f"Package size is outside the conditional PUT limit: {locale}/{filename}.")
            if version_path.stat().st_size > 128:
                raise PublishError(f"Invalid version file: {locale}/{version_name}.")
            try:
                version = version_path.read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeError):
                raise PublishError(f"Unreadable version file: {locale}/{version_name}.") from None
            if not re.fullmatch(r"[0-9]{1,20}", version) or int(version) <= 0:
                raise PublishError(f"Invalid version file: {locale}/{version_name}.")
            validate_archive(path, database)
            digest = sha256_file(path)
            key = f"prerelease/{kind}/{locale}/{digest}/{filename}"
            packs.append({"locale": locale, "kind": kind, "version": version,
                          "fileName": filename, "sizeBytes": size, "sha256": digest,
                          "url": public_base + "/" + quote(key, safe="/"),
                          "mirrors": [f"https://raw.githubusercontent.com/{source_repository}/{source_commit}/{locale}/{filename}"]})
            files[key] = path
    return {"schemaVersion": 1, "sourceRepository": source_repository,
            "sourceCommit": source_commit, "packs": packs}, files


def verify_checkout(source_dir, source_commit):
    def git(*arguments):
        result = subprocess.run(["git", "-C", str(source_dir), *arguments],
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise PublishError("Could not verify the pinned source checkout.")
        return result.stdout.strip()
    if git("rev-parse", "HEAD") != source_commit:
        raise PublishError("The source checkout does not match sourceCommit.")
    paths = inventory_paths()
    git("ls-files", "--error-unmatch", "--", *paths)
    if git("status", "--porcelain", "--", *paths):
        raise PublishError("Required source files differ from the pinned commit.")


def current_main_commit(repository=SOURCE_REPOSITORY):
    if repository != SOURCE_REPOSITORY:
        raise PublishError("Unexpected source repository.")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "MDPro4-prerelease-publisher",
               "Cache-Control": "no-cache"}
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        # The ref response is small even when the newest commit changes large
        # card databases or translation files. Commit details include file diffs.
        request = Request(f"https://api.github.com/repos/{repository}/git/ref/heads/main", headers=headers)
        with urlopen(request, timeout=30) as response:
            data = response.read(MAX_CATALOG_BYTES + 1)
        if len(data) > MAX_CATALOG_BYTES:
            raise ValueError("response size")
        value = json.loads(data)
        if value.get("ref") != "refs/heads/main" or value.get("object", {}).get("type") != "commit":
            raise ValueError("ref identity")
        commit = value["object"]["sha"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("commit")
        return commit
    except Exception:
        raise PublishError("Could not verify the current source main commit; catalog was not promoted.") from None


def verify_public(url, expected_size, expected_sha256):
    for attempt in range(1, PUBLIC_VERIFY_ATTEMPTS + 1):
        try:
            verify_public_once(url, expected_size, expected_sha256)
            return
        except RetryableVerificationError as error:
            if attempt == PUBLIC_VERIFY_ATTEMPTS:
                raise PublishError(f"{error} Failed after {attempt} attempts.") from None
            delay = 2 ** attempt
            print(f"{error} Retrying full verification in {delay}s "
                  f"(attempt {attempt + 1}/{PUBLIC_VERIFY_ATTEMPTS}).", file=sys.stderr)
            time.sleep(delay)


def verify_public_once(url, expected_size, expected_sha256):
    # Exclude query strings, userinfo and exception bodies from diagnostics.
    parsed = urlsplit(url)
    identity = (parsed.hostname or "unknown host") + parsed.path
    started = time.monotonic()
    count = 0
    try:
        request = Request(url, headers={"Cache-Control": "no-cache", "Accept-Encoding": "identity",
                                        "User-Agent": "MDPro4-prerelease-publisher/1.0"})
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                failure = RetryableVerificationError if response.status in RETRYABLE_HTTP_STATUS else PublishError
                raise failure(f"HTTP {response.status}, expected 200")
            if urlsplit(response.geturl()).scheme != "https":
                raise PublishError("redirect did not preserve HTTPS")
            count, digest = 0, hashlib.sha256()
            read = getattr(response, "read1", response.read)
            while True:
                if time.monotonic() - started >= PUBLIC_VERIFY_BUDGET:
                    raise RetryableVerificationError(f"exceeded {PUBLIC_VERIFY_BUDGET}s transfer budget")
                chunk = read(CHUNK_BYTES)
                if not chunk:
                    break
                count += len(chunk)
                if count > expected_size:
                    raise PublishError(f"oversized response: received at least {count:,}, expected {expected_size:,} bytes")
                digest.update(chunk)
        if count != expected_size:
            raise RetryableVerificationError(f"size mismatch: received {count:,}, expected {expected_size:,} bytes")
        if digest.hexdigest() != expected_sha256:
            raise PublishError(f"SHA-256 mismatch: received {digest.hexdigest()}, expected {expected_sha256}")
    except Exception as error:
        retryable = isinstance(error, RetryableVerificationError)
        if isinstance(error, PublishError):
            reason = str(error)
        elif isinstance(error, HTTPError):
            reason = f"HTTP {error.code}"
            retryable = error.code in RETRYABLE_HTTP_STATUS
            error.close()
        else:
            reason = type(error).__name__
            retryable = isinstance(error, (URLError, TimeoutError, ConnectionError, IncompleteRead, RemoteDisconnected))
        failure = RetryableVerificationError if retryable else PublishError
        raise failure(
            f"Public verification failed for {identity}: {reason} "
            f"({count:,} bytes read in {time.monotonic() - started:.1f}s)."
        ) from None


def error_status(error):
    response = getattr(error, "response", {})
    return response.get("ResponseMetadata", {}).get("HTTPStatusCode")


def read_catalog(client, bucket, key):
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        try:
            data = body.read(MAX_CATALOG_BYTES + 1)
        finally:
            body.close()
        if len(data) > MAX_CATALOG_BYTES or not response.get("ETag"):
            raise PublishError("Existing catalog is oversized or has no ETag.")
        return data, response["ETag"]
    except PublishError:
        raise
    except Exception as error:
        if error_status(error) == 404:
            return None, None
        raise PublishError("Could not read the current R2 catalog.") from None


def ensure_payload(client, bucket, key, path, pack, public_verifier):
    # A HEAD avoids resending an existing file, but never substitutes for a full
    # public read-back. Its metadata/ETag is not a trusted SHA-256 proof.
    label = f"{pack['kind']}/{pack['locale']}/{pack['fileName']}"
    absent = False
    try:
        result = client.head_object(Bucket=bucket, Key=key)
        if result.get("ContentLength") != pack["sizeBytes"]:
            raise PublishError(f"Immutable payload size conflicts for {label}: R2 has {result.get('ContentLength')}, expected {pack['sizeBytes']} bytes.")
    except PublishError:
        raise
    except Exception as error:
        if error_status(error) != 404:
            raise PublishError(f"Could not inspect immutable R2 payload {label} (HTTP {error_status(error) or 'unknown'}).") from None
        absent = True
    if absent:
        # Re-hash at the write boundary, so changed local files cannot be uploaded
        # with an earlier snapshot's identity. The runner owns this checkout.
        if path.stat().st_size != pack["sizeBytes"] or sha256_file(path) != pack["sha256"]:
            raise PublishError(f"Local package {label} changed after validation.")
        md5 = hashlib.md5(usedforsecurity=False)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
                md5.update(chunk)
            stream.seek(0)
            try:
                client.put_object(Bucket=bucket, Key=key, Body=stream, IfNoneMatch="*",
                                  ContentLength=pack["sizeBytes"],
                                  ContentMD5=base64.b64encode(md5.digest()).decode("ascii"),
                                  Metadata={"sha256": pack["sha256"]},
                                  ContentType="application/zip", CacheControl="public, max-age=31536000, immutable")
            except Exception as error:
                if error_status(error) != 412:
                    raise PublishError(f"R2 upload failed for {label} (HTTP {error_status(error) or 'unknown'}); catalog was not promoted.") from None
                # Another create-once writer won. Compare its actual public bytes.
    try:
        public_verifier(pack["url"], pack["sizeBytes"], pack["sha256"])
    except PublishError as error:
        raise PublishError(f"Payload {label}: {error} Catalog was not promoted.") from None
    except Exception as error:
        raise PublishError(f"Public verification failed for {label} ({type(error).__name__}); catalog was not promoted.") from None


def publish_snapshot(client, bucket, catalog, files, public_verifier=verify_public,
                     main_resolver=current_main_commit, catalog_key=CATALOG_KEY,
                     retention="off", retention_report=None):
    if retention not in ("off", "preview", "apply"):
        raise PublishError("Unknown prerelease retention mode.")
    if retention != "off" and (bucket != "mdpro4-prerelease" or retention_report is None):
        raise PublishError("Retention requires the prerelease bucket and a local receipt path.")
    if catalog_key != CATALOG_KEY:
        raise PublishError("Unexpected catalog key.")
    expected = {(kind, locale) for kind, (locales, _, _, _) in KINDS.items() for locale in locales}
    packs = catalog.get("packs", [])
    actual = [(pack.get("kind"), pack.get("locale")) for pack in packs]
    if (catalog.get("schemaVersion") != 1 or catalog.get("sourceRepository") != SOURCE_REPOSITORY or
            not re.fullmatch(r"[0-9a-f]{40}", catalog.get("sourceCommit", "")) or
            len(actual) != len(expected) or set(actual) != expected):
        raise PublishError("Refusing to publish an incomplete or unexpected catalog snapshot.")
    keys, public_bases = set(), set()
    for pack in packs:
        kind, locale = pack["kind"], pack["locale"]
        filename = KINDS[kind][1]
        if (pack.get("fileName") != filename or not re.fullmatch(r"[0-9a-f]{64}", pack.get("sha256", "")) or
                type(pack.get("sizeBytes")) is not int or not 0 < pack["sizeBytes"] <= MAX_PUT_BYTES or
                not re.fullmatch(r"[0-9]{1,20}", pack.get("version", "")) or int(pack["version"]) <= 0):
            raise PublishError("Refusing to publish an invalid package descriptor.")
        key = f"prerelease/{kind}/{locale}/{pack['sha256']}/{filename}"
        https_base(pack.get("url", ""))
        if (not pack["url"].endswith("/" + key) or pack.get("mirrors") !=
                [f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/{catalog['sourceCommit']}/{locale}/{filename}"]):
            raise PublishError("Refusing to publish an unpinned or mismatched package URL.")
        keys.add(key)
        public_bases.add(pack["url"][:-(len(key) + 1)])
    if set(files) != keys:
        raise PublishError("Catalog and local package inventory differ.")
    if len(public_bases) != 1:
        raise PublishError("Catalog payloads must use one public delivery base.")
    # A queued older run can otherwise read every public package only to discover
    # that its snapshot can no longer be promoted. This is a safe no-op.
    if main_resolver(catalog["sourceRepository"]) != catalog["sourceCommit"]:
        return "superseded"
    public_catalog_url = next(iter(public_bases)) + "/" + catalog_key
    previous, etag = read_catalog(client, bucket, catalog_key)
    for pack in catalog["packs"]:
        key = f"prerelease/{pack['kind']}/{pack['locale']}/{pack['sha256']}/{pack['fileName']}"
        ensure_payload(client, bucket, key, files[key], pack, public_verifier)
    if main_resolver(catalog["sourceRepository"]) != catalog["sourceCommit"]:
        return "superseded"
    body = canonical_json(catalog)
    if previous == body:
        verify_public_catalog(public_verifier, public_catalog_url, body)
        if retention != "off":
            prune_verified_snapshot(client, bucket, catalog, keys, body, retention, retention_report, main_resolver)
        return "unchanged"
    conditions = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
    try:
        client.put_object(Bucket=bucket, Key=catalog_key, Body=body, **conditions,
                          ContentType="application/json; charset=utf-8",
                          CacheControl="no-cache, max-age=0, must-revalidate",
                          ContentMD5=base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode("ascii"))
    except Exception:
        # A timeout can mean the write committed. A conflict can be an identical
        # concurrent run. Neither permits replacing a different current catalog.
        current, _ = read_catalog(client, bucket, catalog_key)
        if current != body:
            raise PublishError("Catalog promotion conflicted or failed; current catalog was preserved.") from None
    confirmed, _ = read_catalog(client, bucket, catalog_key)
    if confirmed != body:
        raise PublishError("Catalog changed after promotion; rerun from current source main.")
    verify_public_catalog(public_verifier, public_catalog_url, body)
    if retention != "off":
        prune_verified_snapshot(client, bucket, catalog, keys, body, retention, retention_report, main_resolver)
    return "published"


def managed_payload_key(key):
    """Only our exact content-addressed package keys are eligible for deletion."""
    parts = key.split("/")
    if len(parts) != 5 or parts[0] != "prerelease" or parts[1] not in KINDS:
        return False
    locales, filename, _, _ = KINDS[parts[1]]
    return (parts[2] in locales and re.fullmatch(r"[0-9a-f]{64}", parts[3]) is not None
            and parts[4] == filename)


def inventory_prerelease(client, bucket):
    objects, tokens = {}, set()
    token = None
    while True:
        arguments = {"Bucket": bucket, "Prefix": "prerelease/", "MaxKeys": 1000}
        if token:
            arguments["ContinuationToken"] = token
        page = client.list_objects_v2(**arguments)
        for item in page.get("Contents", []):
            key, size, etag = item.get("Key"), item.get("Size"), item.get("ETag")
            if (not isinstance(key, str) or not key.startswith("prerelease/") or key in objects
                    or type(size) is not int or size < 0 or not isinstance(etag, str) or not etag):
                raise PublishError("Retention inventory contains an invalid or duplicate object.")
            objects[key] = {"key": key, "sizeBytes": size, "etag": etag}
        if not page.get("IsTruncated"):
            return objects
        token = page.get("NextContinuationToken")
        if not isinstance(token, str) or not token or token in tokens:
            raise PublishError("Retention inventory has an invalid pagination token.")
        tokens.add(token)


def prune_verified_snapshot(client, bucket, catalog, keep, body, mode, receipt_path, main_resolver):
    # Only publish_snapshot calls this, after every payload and the public catalog
    # pass full verification. Its workflow concurrency group owns all writers.
    report = {"schemaVersion": 1, "mode": mode, "sourceCommit": catalog["sourceCommit"],
              "bucket": bucket, "catalogSha256": hashlib.sha256(body).hexdigest(),
              "status": "planning", "retained": [], "obsolete": [], "removed": [], "unmanaged": []}
    receipt_path = Path(receipt_path)
    def save():
        receipt_path.write_bytes(canonical_json(report))
    def require_current():
        current, _ = read_catalog(client, bucket, CATALOG_KEY)
        if current != body:
            raise PublishError("Catalog changed during retention; no further objects were deleted.")
    def require_complete(objects):
        for pack in catalog["packs"]:
            key = f"prerelease/{pack['kind']}/{pack['locale']}/{pack['sha256']}/{pack['fileName']}"
            if key not in objects or objects[key]["sizeBytes"] != pack["sizeBytes"]:
                raise PublishError("Current payload inventory changed; retention stopped.")
    try:
        require_current()
        if main_resolver(catalog["sourceRepository"]) != catalog["sourceCommit"]:
            report["status"] = "superseded"
            return
        objects = inventory_prerelease(client, bucket)
        require_complete(objects)
        report["retained"] = [objects[key] for key in sorted(keep)]
        report["obsolete"] = [objects[key] for key in sorted(objects) if key not in keep and managed_payload_key(key)]
        report["unmanaged"] = [key for key in sorted(objects) if key not in keep and not managed_payload_key(key)]
        report["status"] = "preview" if mode == "preview" else "planned"
        save()  # Record the exact allowlist and retained identities before any delete.
        require_current()
        if mode == "apply":
            for item in report["obsolete"]:
                key = item["key"]
                try:
                    head = client.head_object(Bucket=bucket, Key=key)
                except Exception as error:
                    if error_status(error) == 404:
                        report["removed"].append(dict(item, alreadyAbsent=True))
                        continue
                    raise
                if head.get("ETag") != item["etag"] or head.get("ContentLength") != item["sizeBytes"]:
                    raise PublishError("An obsolete payload changed after inventory; retention stopped.")
                require_current()
                client.delete_object(Bucket=bucket, Key=key)
                report["removed"].append(item)
                save()
            require_current()
            remaining = inventory_prerelease(client, bucket)
            require_complete(remaining)
            extras = [key for key in remaining if managed_payload_key(key) and key not in keep]
            if extras:
                raise PublishError("Obsolete packages remain after retention; rerun from current main.")
            report["status"] = "complete"
        report["removedBytes"] = sum(item["sizeBytes"] for item in report["removed"])
        print(f"Prerelease retention {report['status']}: retained {len(keep)} current packages; "
              f"obsolete {len(report['obsolete'])}; removed {len(report['removed'])} "
              f"({report['removedBytes']:,} bytes).")
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error) if isinstance(error, PublishError) else type(error).__name__
        raise PublishError(f"Catalog is committed; retention stopped: {report['error']}") from None
    finally:
        save()


def verify_public_catalog(public_verifier, url, body):
    try:
        public_verifier(url, len(body), hashlib.sha256(body).hexdigest())
    except Exception as error:
        # The conditional write has already committed. Rolling it back could
        # erase another publisher's work and cannot repair a public cache rule.
        detail = str(error) if isinstance(error, PublishError) else type(error).__name__
        raise PublishError(f"Catalog is committed to R2 but public catalog verification failed: {detail} Check the public host and rerun publication.") from None


def create_client(endpoint):
    if not re.fullmatch(r"https://[a-z0-9]{32}(?:\.(?:eu|us|fedramp))?\.r2\.cloudflarestorage\.com/?", endpoint):
        raise PublishError("Use the account's HTTPS R2 S3 endpoint.")
    access = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not access or not secret:
        raise PublishError("R2 upload credentials are not configured.")
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise PublishError("Install requirements-prerelease-r2.txt before publishing.") from None
    return boto3.client("s3", endpoint_url=endpoint.rstrip("/"), region_name="auto",
                        aws_access_key_id=access, aws_secret_access_key=secret,
                        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
                        config=Config(signature_version="s3v4", connect_timeout=30, read_timeout=120,
                                      retries={"mode": "standard", "total_max_attempts": 3},
                                      request_checksum_calculation="when_required",
                                      response_checksum_validation="when_required"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-repository", default=SOURCE_REPOSITORY)
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--bucket", default="mdpro4-prerelease")
    parser.add_argument("--endpoint-url")
    parser.add_argument("--catalog-key", default=CATALOG_KEY)
    parser.add_argument("--retention", choices=("off", "preview", "apply"), default="off")
    parser.add_argument("--retention-report", type=Path, help="Local inventory/deletion receipt; required for retention")
    parser.add_argument("--dry-run", action="store_true", help="Validate local inputs without credentials or network access")
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", args.bucket):
            raise PublishError("Invalid R2 bucket name.")
        verify_checkout(args.source_dir, args.source_commit)
        catalog, files = build_snapshot(args.source_dir, args.source_commit, args.public_base_url, args.source_repository)
        if args.dry_run:
            print(f"Validated {len(catalog['packs'])} packages; dry run uploaded nothing.")
            return 0
        if not args.endpoint_url:
            raise PublishError("The R2 S3 endpoint is not configured.")
        result = publish_snapshot(create_client(args.endpoint_url), args.bucket, catalog, files,
                                  catalog_key=args.catalog_key, retention=args.retention,
                                  retention_report=args.retention_report)
        if result == "superseded":
            print("Skipped superseded snapshot: source main advanced; the current catalog was preserved.")
            return 0
        print(f"Prerelease catalog {result}; {len(catalog['packs'])} packages verified.")
        return 0
    except PublishError as error:
        print(f"Publication stopped: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # SDK/HTTP exception text can contain signed URLs or credential material.
        print(f"Publication stopped: {type(error).__name__}; no diagnostic response body was logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
