import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import publish_prerelease_r2 as publisher


SHA = "a" * 40
PUBLIC = "https://cards.example.com"


class S3Error(Exception):
    def __init__(self, status):
        super().__init__("server body with SECRET_ACCESS_TOKEN")
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.events = []
        self.before_put = None
        self.before_delete = None
        self.before_list = None
        self.page_size = 1000

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise S3Error(404)
        body = self.objects[Key]
        return {"Body": io.BytesIO(body), "ETag": self.etag(body)}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise S3Error(404)
        return {"ContentLength": len(self.objects[Key]), "ETag": self.etag(self.objects[Key]), "Metadata": {"sha256": "untrusted"}}

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        if self.before_list:
            self.before_list()
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        start = int(ContinuationToken or 0)
        end = min(start + self.page_size, len(keys))
        result = {"Contents": [{"Key": key, "Size": len(self.objects[key]), "ETag": self.etag(self.objects[key])}
                               for key in keys[start:end]], "IsTruncated": end < len(keys)}
        if end < len(keys):
            result["NextContinuationToken"] = str(end)
        self.events.append(("list", Prefix))
        return result

    def delete_object(self, *, Bucket, Key):
        if self.before_delete:
            self.before_delete(Key)
        self.objects.pop(Key, None)
        self.events.append(("delete", Key))
        return {}

    @staticmethod
    def etag(body):
        return '"' + hashlib.md5(body).hexdigest() + '"'

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        if self.before_put:
            self.before_put(key, kwargs)
        previous = self.objects.get(key)
        if kwargs.get("IfNoneMatch") == "*" and previous is not None:
            raise S3Error(412)
        if "IfMatch" in kwargs and (previous is None or kwargs["IfMatch"] != self.etag(previous)):
            raise S3Error(412)
        body = kwargs["Body"]
        if hasattr(body, "read"):
            body = body.read()
        self.objects[key] = body
        self.events.append(("put", key, kwargs))
        return {"ETag": self.etag(body)}

    def verify(self, url, size, digest):
        key = url.removeprefix(PUBLIC + "/")
        body = self.objects.get(key, b"")
        self.events.append(("verify", key))
        if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
            raise publisher.PublishError("Public payload mismatch.")


class PublishTests(unittest.TestCase):
    def setUp(self):
        sleep = patch.object(publisher.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        db = self.root / "fixture.cdb"
        connection = sqlite3.connect(db)
        connection.executescript("CREATE TABLE datas(id INTEGER); CREATE TABLE texts(id INTEGER,name TEXT,desc TEXT);")
        connection.close()
        self.db_bytes = db.read_bytes()
        for _, (locales, filename, version, database) in publisher.KINDS.items():
            for locale in locales:
                directory = self.root / locale
                directory.mkdir(exist_ok=True)
                with zipfile.ZipFile(directory / filename, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr(database, self.db_bytes)
                    archive.writestr("script/c1.lua", "return 1")
                (directory / version).write_text("1792256031\n", encoding="utf-8")
        self.s3 = FakeS3()

    def snapshot(self):
        return publisher.build_snapshot(self.root, SHA, PUBLIC)

    def publish(self, catalog=None, files=None, **kwargs):
        if catalog is None:
            catalog, files = self.snapshot()
        return publisher.publish_snapshot(self.s3, kwargs.pop("bucket", "fixture-bucket"), catalog, files,
                                          public_verifier=self.s3.verify,
                                          main_resolver=lambda repository: SHA, **kwargs)

    def test_inventory_and_pinned_mirrors(self):
        catalog, files = self.snapshot()
        self.assertEqual(21, len(catalog["packs"]))
        self.assertEqual(21, len(files))
        self.assertEqual(10, sum(pack["kind"] == "standard" for pack in catalog["packs"]))
        self.assertEqual(11, sum(pack["kind"] == "rush" for pack in catalog["packs"]))
        for pack in catalog["packs"]:
            self.assertIn("/" + SHA + "/", pack["mirrors"][0])
            self.assertIn("/" + pack["sha256"] + "/", pack["url"])
            self.assertEqual("1792256031", pack["version"])

    def test_missing_locale_blocks_validation(self):
        (self.root / "ZH-CN/rush-pre-release.ypk").unlink()
        with self.assertRaises(publisher.PublishError):
            self.snapshot()
        self.assertFalse(self.s3.events)

    def test_incomplete_catalog_cannot_reach_upload_boundary(self):
        catalog, files = self.snapshot()
        catalog["packs"].pop()
        with self.assertRaises(publisher.PublishError):
            self.publish(catalog, files)
        self.assertFalse(self.s3.events)

    def test_unpinned_fallback_cannot_reach_upload_boundary(self):
        catalog, files = self.snapshot()
        catalog["packs"][0]["mirrors"][0] = catalog["packs"][0]["mirrors"][0].replace(SHA, "main")
        with self.assertRaises(publisher.PublishError):
            self.publish(catalog, files)
        self.assertFalse(self.s3.events)

    def test_bad_pack_blocks_validation(self):
        (self.root / "EN/ygopro-super-pre.ypk").write_bytes(b"not a zip")
        with self.assertRaises(publisher.PublishError):
            self.snapshot()

    def test_missing_database_blocks_validation(self):
        with zipfile.ZipFile(self.root / "EN/ygopro-super-pre.ypk", "w") as archive:
            archive.writestr("unrelated.txt", "hello")
        with self.assertRaises(publisher.PublishError):
            self.snapshot()

    def test_invalid_database_blocks_validation(self):
        with zipfile.ZipFile(self.root / "EN/ygopro-super-pre.ypk", "w") as archive:
            archive.writestr("test-release.cdb", b"not SQLite")
        with self.assertRaises(publisher.PublishError):
            self.snapshot()

    def test_path_traversal_blocks_validation(self):
        with zipfile.ZipFile(self.root / "EN/ygopro-super-pre.ypk", "a") as archive:
            archive.writestr("../outside", "bad")
        with self.assertRaises(publisher.PublishError):
            self.snapshot()

    def test_invalid_version_blocks_validation(self):
        (self.root / "EN/version.txt").write_text("NaN")
        with self.assertRaises(publisher.PublishError):
            self.snapshot()

    def test_oversize_is_rejected_before_network(self):
        with patch.object(publisher, "MAX_PUT_BYTES", 1):
            with self.assertRaises(publisher.PublishError):
                self.snapshot()

    def test_catalog_is_last_after_all_public_checks(self):
        self.assertEqual("published", self.publish())
        self.assertEqual(("put", publisher.CATALOG_KEY), self.s3.events[-2][:2])
        self.assertEqual(("verify", publisher.CATALOG_KEY), self.s3.events[-1])
        self.assertEqual(22, sum(event[0] == "verify" for event in self.s3.events))
        self.assertEqual(21, len(json.loads(self.s3.objects[publisher.CATALOG_KEY])["packs"]))
        kwargs = self.s3.events[-2][2]
        self.assertEqual("*", kwargs["IfNoneMatch"])
        self.assertIn("must-revalidate", kwargs["CacheControl"])

    def test_repeat_fully_verifies_existing_objects_without_upload(self):
        self.publish()
        self.s3.events.clear()
        self.assertEqual("unchanged", self.publish())
        self.assertEqual(22, len(self.s3.events))
        self.assertTrue(all(event[0] == "verify" for event in self.s3.events))

    def test_partial_upload_failure_keeps_previous_catalog(self):
        self.s3.objects[publisher.CATALOG_KEY] = b"old complete catalog"
        def fail(key, kwargs):
            if "/standard/EN/" in key:
                raise S3Error(500)
        self.s3.before_put = fail
        with self.assertRaises(publisher.PublishError):
            self.publish()
        self.assertEqual(b"old complete catalog", self.s3.objects[publisher.CATALOG_KEY])

    def test_existing_payload_metadata_cannot_hide_wrong_bytes(self):
        catalog, files = self.snapshot()
        key = next(iter(files))
        self.s3.objects[key] = b"x" * catalog["packs"][0]["sizeBytes"]
        with self.assertRaises(publisher.PublishError):
            self.publish(catalog, files)
        self.assertNotIn(publisher.CATALOG_KEY, self.s3.objects)

    def test_upload_race_accepts_only_identical_winner(self):
        def race(key, kwargs):
            if key != publisher.CATALOG_KEY:
                body = kwargs["Body"]
                self.s3.objects[key] = body.read()
                body.seek(0)
        self.s3.before_put = race
        self.assertEqual("published", self.publish())
        self.assertEqual(22, sum(event[0] == "verify" for event in self.s3.events))

    def test_catalog_compare_and_swap_preserves_newer_writer(self):
        self.s3.objects[publisher.CATALOG_KEY] = b"old catalog"
        def race(key, kwargs):
            if key == publisher.CATALOG_KEY:
                self.s3.objects[key] = b"newer complete catalog"
        self.s3.before_put = race
        with self.assertRaises(publisher.PublishError):
            self.publish()
        self.assertEqual(b"newer complete catalog", self.s3.objects[publisher.CATALOG_KEY])

    def test_superseded_snapshot_skips_before_payload_work(self):
        catalog, files = self.snapshot()
        self.assertEqual("superseded", publisher.publish_snapshot(
            self.s3, "fixture-bucket", catalog, files,
            public_verifier=self.s3.verify, main_resolver=lambda _: "b" * 40))
        self.assertFalse(self.s3.events)
        self.assertFalse(self.s3.objects)

    def test_main_advancing_during_payload_verification_cannot_promote(self):
        catalog, files = self.snapshot()
        commits = iter((SHA, "b" * 40))
        self.assertEqual("superseded", publisher.publish_snapshot(
            self.s3, "fixture-bucket", catalog, files,
            public_verifier=self.s3.verify, main_resolver=lambda _: next(commits)))
        self.assertEqual(21, sum(event[0] == "verify" for event in self.s3.events))
        self.assertNotIn(publisher.CATALOG_KEY, self.s3.objects)

    def test_changed_rolling_payload_uses_new_object_key(self):
        first, _ = self.snapshot()
        with zipfile.ZipFile(self.root / "EN/ygopro-super-pre.ypk", "a") as archive:
            archive.writestr("script/c2.lua", "return 2")
        second, _ = self.snapshot()
        old = next(pack for pack in first["packs"] if pack["locale"] == "EN" and pack["kind"] == "standard")
        new = next(pack for pack in second["packs"] if pack["locale"] == "EN" and pack["kind"] == "standard")
        self.assertNotEqual(old["url"], new["url"])

    def test_local_change_after_validation_is_rejected(self):
        catalog, files = self.snapshot()
        next(iter(files.values())).write_bytes(b"changed")
        with self.assertRaises(publisher.PublishError):
            self.publish(catalog, files)
        self.assertFalse(self.s3.events)

    def test_successful_promotion_uses_previous_etag(self):
        previous = b"old catalog"
        self.s3.objects[publisher.CATALOG_KEY] = previous
        self.publish()
        self.assertEqual(self.s3.etag(previous), self.s3.events[-2][2]["IfMatch"])

    def test_public_catalog_failure_reports_committed_state_without_rollback(self):
        catalog, files = self.snapshot()
        def public_verify(url, size, digest):
            if url.endswith("/" + publisher.CATALOG_KEY):
                raise publisher.PublishError("stale public cache")
            self.s3.verify(url, size, digest)
        with self.assertRaisesRegex(publisher.PublishError, "committed to R2 but public catalog verification failed"):
            publisher.publish_snapshot(self.s3, "fixture-bucket", catalog, files,
                                       public_verifier=public_verify, main_resolver=lambda _: SHA)
        self.assertEqual(publisher.canonical_json(catalog), self.s3.objects[publisher.CATALOG_KEY])
        self.assertEqual("unchanged", self.publish(catalog, files))

    def test_unchanged_run_still_checks_public_catalog(self):
        catalog, files = self.snapshot()
        self.publish(catalog, files)
        def public_verify(url, size, digest):
            if url.endswith("/" + publisher.CATALOG_KEY):
                raise publisher.PublishError("public access denied")
            self.s3.verify(url, size, digest)
        with self.assertRaisesRegex(publisher.PublishError, "public catalog verification failed"):
            publisher.publish_snapshot(self.s3, "fixture-bucket", catalog, files,
                                       public_verifier=public_verify, main_resolver=lambda _: SHA)

    def test_current_main_reads_lightweight_exact_git_ref(self):
        payload = json.dumps({"ref": "refs/heads/main", "object": {"type": "commit", "sha": SHA}}).encode()
        with patch.object(publisher, "urlopen", return_value=io.BytesIO(payload)) as opener:
            self.assertEqual(SHA, publisher.current_main_commit())
        self.assertEqual("https://api.github.com/repos/ElderLich/Pre-Release-Translations/git/ref/heads/main",
                         opener.call_args.args[0].full_url)
        for bad in ({"ref": "refs/heads/other", "object": {"type": "commit", "sha": SHA}},
                    {"ref": "refs/heads/main", "object": {"type": "tag", "sha": SHA}},
                    {"ref": "refs/heads/main", "object": {"type": "commit", "sha": "main"}}):
            with self.subTest(payload=bad), patch.object(publisher, "urlopen", return_value=io.BytesIO(json.dumps(bad).encode())):
                with self.assertRaises(publisher.PublishError):
                    publisher.current_main_commit()

    def test_ambiguous_promotion_success_is_recognized(self):
        def timeout(key, kwargs):
            if key == publisher.CATALOG_KEY:
                self.s3.objects[key] = kwargs["Body"]
                raise TimeoutError("SECRET_ACCESS_TOKEN")
        self.s3.before_put = timeout
        self.assertEqual("published", self.publish())

    def test_client_error_does_not_echo_credentials(self):
        stderr = io.StringIO()
        arguments = ["--source-dir", str(self.root), "--source-commit", SHA,
                     "--public-base-url", PUBLIC, "--endpoint-url", "https://example.com"]
        with patch.object(publisher, "verify_checkout"), patch.object(publisher, "create_client", side_effect=RuntimeError("SECRET_ACCESS_TOKEN")):
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(1, publisher.main(arguments))
        self.assertNotIn("SECRET_ACCESS_TOKEN", stderr.getvalue())

    def test_upload_error_does_not_echo_server_body(self):
        self.s3.before_put = lambda key, kwargs: (_ for _ in ()).throw(S3Error(403))
        with self.assertRaises(publisher.PublishError) as raised:
            self.publish()
        self.assertNotIn("SECRET_ACCESS_TOKEN", str(raised.exception))

    def test_dry_run_needs_no_sdk_credentials_or_network(self):
        arguments = ["--source-dir", str(self.root), "--source-commit", SHA,
                     "--public-base-url", PUBLIC, "--dry-run"]
        with patch.object(publisher, "verify_checkout"), patch.object(publisher, "create_client") as client:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, publisher.main(arguments))
        client.assert_not_called()

    def test_public_verification_identifies_publisher_for_payload_and_catalog(self):
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return PUBLIC
        body = b"verified public bytes"
        for key in ("prerelease/standard/DE/hash/ygopro-super-pre.ypk", publisher.CATALOG_KEY):
            with self.subTest(key=key), patch.object(publisher, "urlopen", return_value=Response(body)) as opener:
                publisher.verify_public(PUBLIC + "/" + key, len(body), hashlib.sha256(body).hexdigest())
            request = opener.call_args.args[0]
            self.assertEqual("MDPro4-prerelease-publisher/1.0", request.get_header("User-agent"))
            self.assertEqual("no-cache", request.get_header("Cache-control"))
            self.assertEqual("identity", request.get_header("Accept-encoding"))
            self.assertIsNone(request.get_header("Authorization"))

    def test_public_verification_streams_actual_bytes(self):
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return PUBLIC + "/payload"
        body = b"actual public bytes"
        with patch.object(publisher, "urlopen", return_value=Response(body)):
            publisher.verify_public(PUBLIC + "/payload", len(body), hashlib.sha256(body).hexdigest())
        with patch.object(publisher, "urlopen", return_value=Response(body + b"wrong")):
            with self.assertRaises(publisher.PublishError):
                publisher.verify_public(PUBLIC + "/payload", len(body), hashlib.sha256(body).hexdigest())

    def test_public_verification_names_payload_and_failed_check_without_secrets(self):
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return PUBLIC
        url = PUBLIC + "/prerelease/standard/KR/hash/ygopro-super-pre.ypk?token=SECRET"
        checks = ((b'ab', 3, hashlib.sha256(b'abc').hexdigest(), 'size mismatch'),
                  (b'abc', 3, hashlib.sha256(b'xyz').hexdigest(), 'SHA-256 mismatch'),
                  (b'abcd', 3, hashlib.sha256(b'abc').hexdigest(), 'oversized response'))
        for body, size, digest, reason in checks:
            with self.subTest(reason=reason), patch.object(publisher, 'urlopen', side_effect=lambda *_args, **_kwargs: Response(body)):
                with self.assertRaises(publisher.PublishError) as raised:
                    publisher.verify_public(url, size, digest)
                message = str(raised.exception)
                self.assertIn('/standard/KR/', message)
                self.assertIn(reason, message)
                self.assertNotIn('SECRET', message)
        with patch.object(publisher, 'urlopen', side_effect=TimeoutError('SECRET_ACCESS_TOKEN')):
            with self.assertRaisesRegex(publisher.PublishError, 'TimeoutError') as raised:
                publisher.verify_public(url, 3, 'a' * 64)
        self.assertNotIn('SECRET', str(raised.exception))
        error = publisher.HTTPError(url, 403, 'SECRET_SERVER_MESSAGE', {}, io.BytesIO(b'SECRET_BODY'))
        with patch.object(publisher, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(publisher.PublishError, 'HTTP 403') as raised:
                publisher.verify_public(url, 3, 'a' * 64)
        self.assertNotIn('SECRET', str(raised.exception))

    def test_public_verification_stops_trickled_response_at_transfer_budget(self):
        clock = [0.0]
        class Trickle(io.BytesIO):
            status = 200
            def geturl(self):
                return PUBLIC
            def read1(self, size):
                if size <= 0:
                    raise AssertionError('Read must be sized')
                clock[0] += publisher.PUBLIC_VERIFY_BUDGET / 2
                return b'x'
            def read(self, *args):
                raise AssertionError('Use one bounded underlying read')
        with patch.object(publisher.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(
                publisher, 'urlopen', side_effect=lambda *_args, **_kwargs: Trickle()):
            with self.assertRaisesRegex(publisher.PublishError, 'transfer budget'):
                publisher.verify_public(PUBLIC + '/payload', 100, 'a' * 64)

    def test_payload_failure_identifies_locale_and_preserves_catalog(self):
        catalog, files = self.snapshot()
        def fail(url, size, digest):
            if '/standard/KR/' in url:
                raise publisher.PublishError('SHA-256 mismatch')
            self.s3.verify(url, size, digest)
        with self.assertRaisesRegex(publisher.PublishError, 'standard/KR/ygopro-super-pre.ypk.*SHA-256 mismatch'):
            publisher.publish_snapshot(self.s3, 'fixture-bucket', catalog, files,
                                       public_verifier=fail, main_resolver=lambda _: SHA)
        self.assertNotIn(publisher.CATALOG_KEY, self.s3.objects)

    def test_superseded_cli_summary_is_successful_and_does_not_claim_verification(self):
        arguments = ['--source-dir', str(self.root), '--source-commit', SHA,
                     '--public-base-url', PUBLIC, '--endpoint-url', 'https://example.com']
        output = io.StringIO()
        with patch.object(publisher, 'verify_checkout'), patch.object(publisher, 'create_client'), patch.object(
                publisher, 'publish_snapshot', return_value='superseded'), contextlib.redirect_stdout(output):
            self.assertEqual(0, publisher.main(arguments))
        self.assertIn('Skipped superseded snapshot', output.getvalue())
        self.assertNotIn('packages verified', output.getvalue())

    def retention_fixture(self):
        catalog, files = self.snapshot()
        old = set()
        for kind, (locales, filename, _, _) in publisher.KINDS.items():
            for locale in locales:
                key = f"prerelease/{kind}/{locale}/{'b' * 64}/{filename}"
                old.add(key)
                self.s3.objects[key] = b'old package'
        unrelated = {'mappings/v1/id-map.json', 'prerelease/unmanaged.ypk',
                     f"prerelease/standard/ZH-CN/{'b' * 64}/ygopro-super-pre.ypk",
                     f"prerelease/rush/EN/{'b' * 64}/unrelated.ypk"}
        for key in unrelated:
            self.s3.objects[key] = b'preserve me'
        return catalog, files, old, unrelated

    def test_retention_preview_records_paginated_allowlist_without_deleting(self):
        catalog, files, old, unrelated = self.retention_fixture()
        self.s3.page_size = 3
        path = self.root / 'retention.json'
        self.assertEqual('published', self.publish(catalog, files, bucket='mdpro4-prerelease',
            retention='preview', retention_report=path))
        receipt = json.loads(path.read_bytes())
        self.assertEqual('preview', receipt['status'])
        self.assertEqual(old, {row['key'] for row in receipt['obsolete']})
        self.assertEqual(set(files), {row['key'] for row in receipt['retained']})
        self.assertFalse(receipt['removed'])
        self.assertTrue((old | unrelated | set(files)) <= self.s3.objects.keys())
        self.assertFalse(any(event[0] == 'delete' for event in self.s3.events))

    def test_retention_deletes_only_obsolete_packages_after_all_verification(self):
        catalog, files, old, unrelated = self.retention_fixture()
        path = self.root / 'retention.json'
        self.assertEqual('published', self.publish(catalog, files, bucket='mdpro4-prerelease',
            retention='apply', retention_report=path))
        self.assertEqual(set(files) | unrelated | {publisher.CATALOG_KEY}, self.s3.objects.keys())
        first_delete = next(i for i, event in enumerate(self.s3.events) if event[0] == 'delete')
        self.assertEqual(22, sum(event[0] == 'verify' for event in self.s3.events[:first_delete]))
        receipt = json.loads(path.read_bytes())
        self.assertEqual('complete', receipt['status'])
        self.assertEqual(old, {row['key'] for row in receipt['removed']})
        self.assertEqual(len(old) * len(b'old package'), receipt['removedBytes'])
        self.s3.events.clear()
        self.assertEqual('unchanged', self.publish(catalog, files, bucket='mdpro4-prerelease',
            retention='apply', retention_report=path))
        self.assertFalse(any(event[0] == 'delete' for event in self.s3.events))
        self.assertEqual([], json.loads(path.read_bytes())['obsolete'])

    def test_failed_public_catalog_verification_never_deletes_old_packages(self):
        catalog, files, old, _ = self.retention_fixture()
        def verify(url, size, digest):
            if url.endswith(publisher.CATALOG_KEY):
                raise publisher.PublishError('public catalog unavailable')
            self.s3.verify(url, size, digest)
        with self.assertRaises(publisher.PublishError):
            publisher.publish_snapshot(self.s3, 'mdpro4-prerelease', catalog, files,
                public_verifier=verify, main_resolver=lambda _: SHA,
                retention='apply', retention_report=self.root / 'retention.json')
        self.assertTrue(old <= self.s3.objects.keys())
        self.assertFalse(any(event[0] in ('delete', 'list') for event in self.s3.events))

    def test_retention_refuses_changed_catalog_before_first_delete(self):
        catalog, files, old, _ = self.retention_fixture()
        self.s3.before_list = lambda: self.s3.objects.update({publisher.CATALOG_KEY: b'newer catalog'})
        path = self.root / 'retention.json'
        with self.assertRaisesRegex(publisher.PublishError, 'Catalog changed'):
            self.publish(catalog, files, bucket='mdpro4-prerelease', retention='apply', retention_report=path)
        self.assertTrue(old <= self.s3.objects.keys())
        self.assertEqual('failed', json.loads(path.read_bytes())['status'])

    def test_catalog_drift_mid_cleanup_stops_further_deletes(self):
        catalog, files, old, _ = self.retention_fixture()
        self.s3.before_delete = lambda key: self.s3.objects.update({publisher.CATALOG_KEY: b'newer catalog'})
        with self.assertRaisesRegex(publisher.PublishError, 'Catalog changed'):
            self.publish(catalog, files, bucket='mdpro4-prerelease', retention='apply', retention_report=self.root / 'receipt.json')
        self.assertEqual(1, sum(event[0] == 'delete' for event in self.s3.events))
        self.assertEqual(20, len(old & self.s3.objects.keys()))
        self.assertTrue(set(files) <= self.s3.objects.keys())

    def test_missing_current_inventory_package_stops_retention(self):
        catalog, files, old, _ = self.retention_fixture()
        def disappear():
            self.s3.objects.pop(next(iter(files)), None)
        self.s3.before_list = disappear
        with self.assertRaisesRegex(publisher.PublishError, 'Current payload inventory changed'):
            self.publish(catalog, files, bucket='mdpro4-prerelease', retention='apply', retention_report=self.root / 'receipt.json')
        self.assertTrue(old <= self.s3.objects.keys())

    def test_changed_obsolete_object_is_not_deleted(self):
        catalog, files, old, _ = self.retention_fixture()
        original = self.s3.head_object
        def changed(**kwargs):
            if kwargs['Key'] in old:
                self.s3.objects[kwargs['Key']] = b'changed after plan'
            return original(**kwargs)
        self.s3.head_object = changed
        with self.assertRaisesRegex(publisher.PublishError, 'obsolete payload changed'):
            self.publish(catalog, files, bucket='mdpro4-prerelease', retention='apply', retention_report=self.root / 'receipt.json')
        self.assertFalse(any(event[0] == 'delete' for event in self.s3.events))

    def test_retention_delete_failure_records_partial_work_and_retries_safely(self):
        catalog, files, old, _ = self.retention_fixture()
        count = [0]
        def fail_second(key):
            count[0] += 1
            if count[0] == 2:
                raise S3Error(503)
        self.s3.before_delete = fail_second
        path = self.root / 'receipt.json'
        with self.assertRaisesRegex(publisher.PublishError, 'retention stopped') as raised:
            self.publish(catalog, files, bucket='mdpro4-prerelease', retention='apply', retention_report=path)
        self.assertNotIn('SECRET', str(raised.exception))
        receipt = json.loads(path.read_bytes())
        self.assertEqual('failed', receipt['status'])
        self.assertEqual(1, len(receipt['removed']))
        self.assertEqual(publisher.canonical_json(catalog), self.s3.objects[publisher.CATALOG_KEY])
        self.s3.before_delete = None
        self.assertEqual('unchanged', self.publish(catalog, files, bucket='mdpro4-prerelease', retention='apply', retention_report=path))
        self.assertFalse(old & self.s3.objects.keys())

    def test_retention_rejects_other_bucket_or_missing_receipt_before_upload(self):
        for bucket, receipt in [('fixture-bucket', self.root / 'receipt.json'), ('mdpro4-prerelease', None)]:
            with self.subTest(bucket=bucket), self.assertRaises(publisher.PublishError):
                self.publish(bucket=bucket, retention='apply', retention_report=receipt)
        self.assertFalse(self.s3.events)

    def test_inventory_rejects_pagination_loop_before_deletes(self):
        self.s3.list_objects_v2 = lambda **_: {'Contents': [], 'IsTruncated': True, 'NextContinuationToken': 'same'}
        with self.assertRaisesRegex(publisher.PublishError, 'pagination token'):
            self.publish(bucket='mdpro4-prerelease', retention='apply', retention_report=self.root / 'receipt.json')
        self.assertFalse(any(event[0] == 'delete' for event in self.s3.events))

    def test_interrupted_public_read_restarts_size_and_hash_from_zero(self):
        body = b'complete public package'
        responses = []
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return PUBLIC
        class Broken(Response):
            def read1(self, size):
                if self.tell():
                    raise publisher.IncompleteRead(b'partial', len(body))
                return super().read1(4)
        for first in (Response(body[:4]), Broken(body), TimeoutError('SECRET')):
            with self.subTest(first=type(first).__name__):
                good = Response(body)
                responses.extend([first, good])
                with patch.object(publisher, 'urlopen', side_effect=[first, good]) as opener:
                    publisher.verify_public(PUBLIC + '/payload', len(body), hashlib.sha256(body).hexdigest())
                self.assertEqual(2, opener.call_count)
                self.assertTrue(good.closed)
                if isinstance(first, Response):
                    self.assertTrue(first.closed)
                for call in opener.call_args_list:
                    self.assertIsNone(call.args[0].get_header('Range'))

    def test_retryable_http_errors_recover_but_integrity_and_permanent_errors_stop(self):
        body = b'good'
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return PUBLIC
        for status in publisher.RETRYABLE_HTTP_STATUS:
            error = publisher.HTTPError(PUBLIC, status, 'SECRET', {}, io.BytesIO(b'SECRET'))
            with self.subTest(status=status), patch.object(publisher, 'urlopen', side_effect=[error, Response(body)]) as opener:
                publisher.verify_public(PUBLIC, len(body), hashlib.sha256(body).hexdigest())
                self.assertEqual(2, opener.call_count)
            self.assertTrue(error.fp.closed)
        for bad in (Response(b'evil'), Response(b'oversized'),
                    publisher.HTTPError(PUBLIC, 403, 'SECRET', {}, io.BytesIO(b'SECRET'))):
            with self.subTest(bad=type(bad).__name__), patch.object(publisher, 'urlopen', side_effect=[bad, Response(body)]) as opener:
                with self.assertRaises(publisher.PublishError):
                    publisher.verify_public(PUBLIC, len(body), hashlib.sha256(body).hexdigest())
                self.assertEqual(1, opener.call_count)

    def test_retries_are_bounded_back_off_and_redact_credentials(self):
        log = io.StringIO()
        with patch.object(publisher, 'urlopen', side_effect=TimeoutError('SECRET')) as opener, contextlib.redirect_stderr(log):
            with self.assertRaisesRegex(publisher.PublishError, 'after 3 attempts') as raised:
                publisher.verify_public('https://user:SECRET@cards.example.com/pack?token=SECRET', 10, 'a' * 64)
        self.assertEqual(3, opener.call_count)
        self.assertEqual([2, 4], [call.args[0] for call in self.sleep.call_args_list])
        self.assertNotIn('SECRET', str(raised.exception) + log.getvalue())

    def test_thai_rush_truncation_recovers_before_catalog_promotion(self):
        self.check_thai_rush_transfer(recovers=True)

    def test_persistent_thai_rush_truncation_preserves_previous_catalog(self):
        self.check_thai_rush_transfer(recovers=False)

    def check_thai_rush_transfer(self, recovers):
        catalog, files = self.snapshot()
        previous = b'previous complete catalog'
        self.s3.objects[publisher.CATALOG_KEY] = previous
        attempts = {}
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return PUBLIC
        def download(request, **kwargs):
            key = request.full_url.removeprefix(PUBLIC + '/')
            attempts[key] = attempts.get(key, 0) + 1
            body = self.s3.objects[key]
            if '/rush/TH/' in key and (attempts[key] == 1 or not recovers):
                self.assertEqual(previous, self.s3.objects[publisher.CATALOG_KEY])
                body = body[:8]
            return Response(body)
        with patch.object(publisher, 'urlopen', side_effect=download):
            def run():
                return publisher.publish_snapshot(self.s3, 'fixture-bucket', catalog, files,
                    public_verifier=publisher.verify_public, main_resolver=lambda _: SHA)
            if recovers:
                self.assertEqual('published', run())
                self.assertEqual(publisher.canonical_json(catalog), self.s3.objects[publisher.CATALOG_KEY])
                self.assertEqual(publisher.CATALOG_KEY, self.s3.events[-1][1])
                self.assertEqual(22, len(attempts))
            else:
                with self.assertRaisesRegex(publisher.PublishError, 'rush/TH/.*after 3 attempts'):
                    run()
                self.assertEqual(previous, self.s3.objects[publisher.CATALOG_KEY])
                self.assertTrue(all(event[1] != publisher.CATALOG_KEY for event in self.s3.events))
        thai = next(key for key in attempts if '/rush/TH/' in key)
        self.assertEqual(2 if recovers else 3, attempts[thai])


if __name__ == "__main__":
    unittest.main()
