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

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise S3Error(404)
        body = self.objects[Key]
        return {"Body": io.BytesIO(body), "ETag": self.etag(body)}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise S3Error(404)
        return {"ContentLength": len(self.objects[Key]), "Metadata": {"sha256": "untrusted"}}

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
        return publisher.publish_snapshot(self.s3, "fixture-bucket", catalog, files,
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

    def test_drifted_main_cannot_promote(self):
        catalog, files = self.snapshot()
        with self.assertRaises(publisher.PublishError):
            publisher.publish_snapshot(self.s3, "fixture-bucket", catalog, files,
                                       public_verifier=self.s3.verify, main_resolver=lambda _: "b" * 40)
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


if __name__ == "__main__":
    unittest.main()
