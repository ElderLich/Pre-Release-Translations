import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import rush_source_archive as source

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Tools"))
import rush_prerelease

SHA = "a" * 40
ROOT = f"ygopro-rush-duel-{SHA}-{SHA}"


def archive_bytes(extra=None, omit=None):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for name in ("RD Patch.cdb", "sets.md", "script/RDBase.lua"):
            if name == omit:
                continue
            member = tarfile.TarInfo(f"{ROOT}/{name}")
            member.size = 5
            archive.addfile(member, io.BytesIO(b"fresh"))
        if extra:
            archive.addfile(extra, io.BytesIO(b""))
    return data.getvalue()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory / "source"

    def test_fresh_branch_is_resolved_and_archive_is_pinned_to_its_commit(self):
        responses = [json.dumps({"commit": {"id": SHA}}).encode(), archive_bytes()]
        with patch.object(source, "request_bytes", side_effect=responses) as request:
            source.fetch_archive(self.target)
        self.assertEqual(request.call_args_list[0].args[0], source.API + "/branches/master")
        self.assertEqual(request.call_args_list[1].args[0], source.API + "/archive.tar.gz?sha=" + SHA)
        self.assertEqual((self.target / "RD Patch.cdb").read_bytes(), b"fresh")
        self.assertEqual(rush_prerelease.revision(self.target), SHA)

    def test_invalid_branch_response_does_not_download_an_unpinned_archive(self):
        for response in (None, {}, {"commit": None}, {"commit": {"id": "master"}}):
            with self.subTest(response=response), \
                 patch.object(source, "request_bytes", return_value=json.dumps(response).encode()) as request:
                with self.assertRaisesRegex(ValueError, "current Rush commit"):
                    source.fetch_archive(self.target)
                self.assertEqual(request.call_count, 1)
                self.assertFalse(self.target.exists())

    def test_mismatched_revision_root_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unexpected Rush archive entry"):
            source.extract_archive(archive_bytes(), "b" * 40, self.target)
        self.assertFalse(self.target.exists())

    def test_traversal_git_metadata_and_links_are_rejected_before_writing(self):
        members = [tarfile.TarInfo(name) for name in (
            f"{ROOT}/../outside", "/absolute", f"{ROOT}/.git/config",
            f"{ROOT}/{source.REVISION_FILE}", f"{ROOT}/C:/outside", f"{ROOT}/..\\outside")]
        link = tarfile.TarInfo(f"{ROOT}/linked")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        members.append(link)
        for member in members:
            with self.subTest(name=member.name), self.assertRaises(ValueError):
                source.extract_archive(archive_bytes(extra=member), SHA, self.target)
            self.assertFalse(self.target.exists())

    def test_missing_required_file_has_no_accepted_revision_receipt(self):
        with self.assertRaisesRegex(ValueError, "missing sets.md"):
            source.extract_archive(archive_bytes(omit="sets.md"), SHA, self.target)
        self.assertFalse((self.target / source.REVISION_FILE).exists())

    def test_existing_source_is_preserved(self):
        self.target.mkdir()
        sentinel = self.target / "local-edit"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(ValueError, "Refusing to replace"):
            source.extract_archive(archive_bytes(), SHA, self.target)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_oversized_archive_is_rejected_before_writing(self):
        with patch.object(source, "MAX_EXPANDED", 10), self.assertRaisesRegex(ValueError, "size limit"):
            source.extract_archive(archive_bytes(), SHA, self.target)
        self.assertFalse(self.target.exists())

    def test_corrupt_archive_is_rejected(self):
        with self.assertRaises(tarfile.TarError):
            source.extract_archive(b"not a gzip archive", SHA, self.target)
        self.assertFalse(self.target.exists())

    def test_revision_receipt_does_not_override_real_git_checkout(self):
        self.target.mkdir()
        (self.target / ".git").mkdir()
        (self.target / source.REVISION_FILE).write_text(SHA)
        with patch.object(rush_prerelease.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "b" * 40
            self.assertEqual(rush_prerelease.revision(self.target), "b" * 40)


if __name__ == "__main__":
    unittest.main()
