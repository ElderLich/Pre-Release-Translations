"""Release regressions: metadata churn must not create another version."""

from contextlib import ExitStack, closing
import io
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import autopr_zh_tw as tw
import es_autopr as es
import ocg_autopr as ocg
import tcg_autopr as tcg
from shared_builds import content_equal, copy_if_changed, publish_release, validate_release_strings, write_deterministic_package


def make_archive(path, members, *, date=(2020, 1, 1, 0, 0, 0), reverse=False):
    with zipfile.ZipFile(path, "w") as archive:
        archive.comment = str(date).encode()
        for name in sorted(members, reverse=reverse):
            info = zipfile.ZipInfo(name, date)
            info.create_system = 0 if reverse else 3
            info.external_attr = 0o777 << 16
            info.comment = b"upstream metadata"
            info.extra = b"\x55\x54\x01\x00\x00"
            archive.writestr(info, members[name])


def make_database(path, name="Translated card"):
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE datas (id INTEGER PRIMARY KEY, atk INTEGER)")
        db.execute("CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT, desc TEXT, " + ", ".join(f"str{i} TEXT" for i in range(1, 17)) + ")")
        db.execute("INSERT INTO datas VALUES (1, 100)")
        db.execute("INSERT INTO texts VALUES (" + ",".join("?" for _ in range(19)) + ")", (1, name, "Card effect", *("" for _ in range(16))))
        db.commit()


class ReproducibleReleaseTests(unittest.TestCase):
    def test_normalizes_all_members_metadata_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            members = {"script/": b"", "script/c1.lua": b"function test() end", "test-strings.conf": b"old"}
            first, second = root / "first.zip", root / "second.zip"
            make_archive(first, members)
            make_archive(second, members, date=(2026, 10, 22, 12, 30, 0), reverse=True)
            payload = root / "strings.conf"
            payload.write_bytes(b"!setname 0x1 Translated\n")
            a, b = root / "a.ypk", root / "b.ypk"
            write_deterministic_package(first, a, {"test-strings.conf": payload})
            os.utime(payload, (1800000000, 1800000000))
            write_deterministic_package(second, b, {"test-strings.conf": payload})
            self.assertEqual(a.read_bytes(), b.read_bytes())
            with zipfile.ZipFile(a) as archive:
                self.assertEqual(archive.namelist(), sorted(members))
                self.assertEqual(archive.comment, b"")
                for info in archive.infolist():
                    self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                    self.assertEqual(info.extra, b"")
                    self.assertEqual(info.comment, b"")

    def test_existing_legacy_zip_and_version_survive_metadata_only_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "ygopro-super-pre.ypk"
            make_archive(package, {"script/c1.lua": b"card bytes", "test-strings.conf": b"translated"})
            old_package = package.read_bytes()
            (root / "test-release.json").write_bytes(b"[]\n")
            (root / "version.txt").write_bytes(b"100")
            candidate = root / "candidate.ypk"
            write_deterministic_package(package, candidate, {})
            candidate_json = root / "candidate.json"
            candidate_json.write_bytes(b"[]\n")
            self.assertFalse(publish_release(candidate, candidate_json, root, now=200))
            self.assertEqual(package.read_bytes(), old_package)
            self.assertEqual((root / "version.txt").read_bytes(), b"100")
            make_archive(candidate, {"script/c1.lua": b"changed card bytes", "test-strings.conf": b"translated"})
            self.assertTrue(publish_release(candidate, candidate_json, root, now=99))
            self.assertEqual((root / "version.txt").read_bytes(), b"101")
            with zipfile.ZipFile(package) as archive:
                self.assertEqual(archive.read("script/c1.lua"), b"changed card bytes")

    def test_json_only_change_and_missing_version_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, candidate = root / "ygopro-super-pre.ypk", root / "candidate.ypk"
            make_archive(package, {"card": b"unchanged"})
            shutil.copyfile(package, candidate)
            before = package.read_bytes()
            (root / "test-release.json").write_bytes(b"[]")
            new_json = root / "candidate.json"
            new_json.write_bytes(b"[]")
            self.assertTrue(publish_release(candidate, new_json, root, now=100))
            new_json.write_bytes(b'[{"name":"corrected"}]')
            self.assertTrue(publish_release(candidate, new_json, root, now=200))
            self.assertEqual(package.read_bytes(), before)
            self.assertEqual((root / "test-release.json").read_bytes(), new_json.read_bytes())
            self.assertEqual((root / "version.txt").read_text(), "200")
            for invalid in ("", "broken", "0", "-1"):
                with self.subTest(invalid_version=invalid):
                    (root / "version.txt").write_text(invalid, encoding="utf-8")
                    self.assertTrue(publish_release(candidate, new_json, root, now=300))
                    self.assertEqual((root / "version.txt").read_text(), "300")
                    self.assertEqual(package.read_bytes(), before)

    def test_sqlite_bookkeeping_is_ignored_but_card_and_user_version_changes_are_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, candidate = root / "original.cdb", root / "candidate.cdb"
            make_database(original)
            previous = original.read_bytes()
            shutil.copyfile(original, candidate)
            with closing(sqlite3.connect(candidate)) as db:
                db.execute("VACUUM")
            self.assertNotEqual(previous, candidate.read_bytes())
            self.assertTrue(content_equal(previous, candidate.read_bytes()))
            self.assertFalse(copy_if_changed(candidate, original))
            self.assertEqual(original.read_bytes(), previous)
            with closing(sqlite3.connect(candidate)) as db:
                db.execute("PRAGMA user_version = 1")
            self.assertFalse(content_equal(previous, candidate.read_bytes()))
            shutil.copyfile(original, candidate)
            with closing(sqlite3.connect(candidate)) as db:
                db.execute("UPDATE texts SET name='Changed card' WHERE id=1")
                db.commit()
            self.assertFalse(content_equal(previous, candidate.read_bytes()))

    def test_invalid_strings_identify_filename_line_and_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test-strings.conf"
            path.write_text("# comment\n!setname 0x1064\n!setname 0x1 Name\n!SETNAME 0X1 Duplicate\n!bad\n!setname 0x2 # pending\n", encoding="utf-8")
            with self.assertRaises(ValueError) as error:
                validate_release_strings(path)
            message = str(error.exception)
            self.assertIn("duplicate=1, malformed=1, missing_translation=2", message)
            self.assertIn(f"{path}:2: missing translation for !setname 0x1064", message)
            self.assertIn(f"{path}:4: duplicate !setname 0x1 (first defined on line 3)", message)
            self.assertIn(f"{path}:5: malformed", message)

    def test_every_builder_rejects_incomplete_strings_before_downloading(self):
        for builder in (tcg, ocg, es, tw):
            with self.subTest(builder=builder.__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for filename in ("test-release.cdb", "beta.cdb"):
                    (root / filename).write_bytes(b"unused")
                for filename in ("test-strings.conf", "pre-strings.conf"):
                    (root / filename).write_text("!setname 0x1064\n")
                with ExitStack() as stack:
                    if builder is tcg:
                        builder.configure("fr")
                    elif builder is ocg:
                        builder.configure("kr")
                    stack.enter_context(patch.object(builder, "CARDEDITOR_EXPANSIONS_DIR" if builder is tw else "WORK_DIR", root))
                    download = stack.enter_context(patch.object(builder, "download_file"))
                    stack.enter_context(patch("sys.stderr", new_callable=io.StringIO))
                    with self.assertRaises(SystemExit):
                        (builder.main if builder is tw else builder.release_build)()
                    download.assert_not_called()

    def test_standard_builders_preserve_noop_outputs_and_publish_changed_cards(self):
        for builder, language in ((tcg, "fr"), (ocg, "kr"), (es, None), (tw, None)):
            with self.subTest(builder=builder.__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output, workspace = root / "output", root / "workspace"
                output.mkdir()
                workspace.mkdir()
                source_release = workspace / ("beta.cdb" if builder is tw else "test-release.cdb")
                source_strings = workspace / ("pre-strings.conf" if builder is tw else "test-strings.conf")
                make_database(source_release)
                source_strings.write_text("!setname 0x1 Translated\n")
                cards = root / "cards.cdb"
                make_database(cards, "Official card")
                source = root / "source.ypk"
                make_archive(source, {"test-update.cdb": cards.read_bytes(), "script/c1.lua": b"card script"})
                def download(url, destination):
                    shutil.copyfile(source if url == builder.YPK_URL else cards, destination)
                with ExitStack() as stack:
                    if language:
                        builder.configure(language)
                    stack.enter_context(patch.object(builder, "ES_DIR" if builder is es else "OUTPUT_DIR", output))
                    stack.enter_context(patch.object(builder, "CARDEDITOR_EXPANSIONS_DIR" if builder is tw else "WORK_DIR", workspace))
                    stack.enter_context(patch.object(builder, "BASE_FILES_DIR" if builder is tw else "BASE_DIR", output / "Base Files"))
                    stack.enter_context(patch.object(builder, "download_file", side_effect=download))
                    stack.enter_context(patch.object(builder, "fetch_json", side_effect=lambda _: [{"name": "source", "picUrl": "https://example.org/1.jpg"}]))
                    if builder is not tw:
                        stack.enter_context(patch.object(builder, "apply_workspace_mappings_for_release"))
                    else:
                        stack.enter_context(patch.dict(os.environ, {"TW_AUTOPR_CARDS_CDB": str(cards)}))
                    stack.enter_context(patch("shared_builds.time.time", return_value=100))
                    stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
                    build = builder.main if builder is tw else builder.release_build
                    build()
                    paths = [output / name for name in ("ygopro-super-pre.ypk", "test-release.json", "version.txt", "Base Files/test-release.cdb", "Base Files/test-strings.conf")]
                    before = {path: path.read_bytes() for path in paths}
                    with closing(sqlite3.connect(source_release)) as db:
                        db.execute("VACUUM")
                    make_archive(source, {"script/c1.lua": b"card script", "test-update.cdb": cards.read_bytes()}, date=(2026, 10, 22, 12, 30, 0), reverse=True)
                    build()
                    self.assertEqual({path: path.read_bytes() for path in paths}, before)
                    with closing(sqlite3.connect(cards)) as db:
                        db.execute("UPDATE texts SET name='Changed official card' WHERE id=1")
                        db.commit()
                    build()
                    self.assertEqual((output / "version.txt").read_text(), "101")
                    with zipfile.ZipFile(output / "ygopro-super-pre.ypk") as archive:
                        extracted = root / "result.cdb"
                        extracted.write_bytes(archive.read("test-update.cdb"))
                    with closing(sqlite3.connect(extracted)) as db:
                        self.assertEqual(db.execute("SELECT name FROM texts WHERE id=1").fetchone()[0], "Changed official card")


if __name__ == "__main__":
    unittest.main()
