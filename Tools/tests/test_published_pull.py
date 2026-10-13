"""Published pulls against temporary Git remotes; no network or live workspace writes."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_workspace as helper
import rush_prerelease as rush
import rush_translator as translator


class PublishedPullTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir()
        self.git(self.origin, "init", "-b", "main")
        self.write(self.origin / "DE/Workspace/test-release.cdb", b"original database")
        self.write(self.origin / "DE/Workspace/test-strings.conf", b"original strings")
        self.write(self.origin / "Shared/Mappings.csv", b"original mappings")
        self.rush_database(self.origin, "Published")
        self.commit(self.origin)
        self.repo = self.root / "checkout"
        self.git(self.root, "clone", str(self.origin), str(self.repo))
        self.local = self.root / "translations"
        (self.local / "DE").mkdir(parents=True)

    def git(self, cwd, *args):
        return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
                              cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit(self, path):
        self.git(path, "add", "-A")
        self.git(path, "commit", "-m", "fixture")

    def args(self, *command):
        return helper.build_parser().parse_args(["--repo-root", str(self.repo), "--local-root", str(self.local),
                                                 *command, "--lang", "de"])

    def pull(self, *options):
        args = self.args("pull", *options)
        helper.command_pull(args)

    def rush_database(self, root, effect):
        path = root / "DE/Rush/Workspace/rush-pre-release.cdb"
        self.write(path, rush.cdb_bytes({100: (100, 4, 0, 0, 33, 1000, 1000, 4, 1, 1, 0)},
                                      {100: (100, "Card", effect, *([""] * 16))}))

    def test_standard_fetches_new_commit_without_creating_backup_folders(self):
        self.write(self.local / "DE/raw2/test-release.cdb", b"unpublished translation")
        self.write(self.local / "DE/raw2/test-strings.conf", b"unpublished strings")
        self.write(self.local / "DE/Mappings.csv", b"previous mappings")
        self.write(self.origin / "DE/Workspace/test-release.cdb", b"latest database")
        self.commit(self.origin)
        self.pull()
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.git(self.origin, "rev-parse", "HEAD"))
        self.assertEqual((self.local / "DE/raw2/test-release.cdb").read_bytes(), b"latest database")
        self.assertEqual((self.local / "DE/raw2/test-strings.conf").read_bytes(), b"original strings")
        self.assertEqual((self.local / "DE/Mappings.csv").read_bytes(), b"original mappings")
        self.assertFalse((self.local / "DE/Backups").exists())
        self.assertEqual(list(self.local.rglob("*.tmp")), [])
        self.pull()
        self.assertFalse((self.local / "DE/Backups").exists())

    def test_failed_staging_preserves_all_local_files_and_cleans_temporary_files(self):
        originals = {
            self.local / "DE/raw2/test-release.cdb": b"unpublished translation",
            self.local / "DE/raw2/test-strings.conf": b"unpublished strings",
            self.local / "DE/Mappings.csv": b"previous mappings",
        }
        for path, data in originals.items():
            self.write(path, data)
        copy_file = helper.shutil.copy2

        def failing_copy(source, target):
            if source.name == "test-strings.conf":
                raise OSError("Not enough space")
            return copy_file(source, target)

        with patch.object(helper.shutil, "copy2", side_effect=failing_copy):
            with self.assertRaisesRegex(SystemExit, "Not enough space"):
                self.pull()
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertFalse((self.local / "DE/Backups").exists())
        self.assertEqual(list(self.local.rglob("*.tmp")), [])

    def test_dirty_checkout_cannot_block_or_contaminate_published_download(self):
        database = self.repo / "DE/Workspace/test-release.cdb"
        database.write_bytes(b"uncommitted repository work")
        self.write(self.origin / "DE/Workspace/test-release.cdb", b"latest published database")
        self.commit(self.origin)
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.pull()
        self.assertEqual(database.read_bytes(), b"uncommitted repository work")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual((self.local / "DE/raw2/test-release.cdb").read_bytes(), b"latest published database")

    def test_non_main_branch_and_local_commits_are_kept(self):
        self.git(self.repo, "checkout", "-b", "local-translations")
        self.write(self.repo / "DE/Workspace/test-release.cdb", b"local commit")
        self.commit(self.repo)
        self.write(self.origin / "DE/Workspace/test-release.cdb", b"remote commit")
        self.commit(self.origin)
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.pull()
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "local-translations")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual((self.local / "DE/raw2/test-release.cdb").read_bytes(), b"remote commit")

    def test_main_ahead_of_origin_still_downloads_origin_bytes(self):
        self.write(self.repo / "DE/Workspace/test-release.cdb", b"unpublished local commit")
        self.commit(self.repo)
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.pull()
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual((self.local / "DE/raw2/test-release.cdb").read_bytes(), b"original database")

    def test_missing_second_input_does_not_overwrite_local_database(self):
        self.write(self.local / "DE/raw2/test-release.cdb", b"local translation")
        (self.origin / "DE/Workspace/test-strings.conf").unlink()
        self.commit(self.origin)
        with self.assertRaisesRegex(SystemExit, "Required published file is missing"):
            self.pull()
        self.assertEqual((self.local / "DE/raw2/test-release.cdb").read_bytes(), b"local translation")
        self.assertFalse((self.local / "DE/Backups").exists())

    def test_failed_fetch_does_not_copy_stale_checkout(self):
        self.write(self.local / "DE/raw2/test-release.cdb", b"local translation")
        self.git(self.repo, "remote", "set-url", "origin", str(self.root / "missing-origin"))
        with self.assertRaises(SystemExit):
            self.pull()
        self.assertEqual((self.local / "DE/raw2/test-release.cdb").read_bytes(), b"local translation")
        self.assertFalse((self.local / "DE/Backups").exists())

    def test_dry_run_fetches_but_does_not_update_checkout_or_local_files(self):
        self.write(self.origin / "DE/Workspace/test-release.cdb", b"latest database")
        self.commit(self.origin)
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.pull("--dry-run")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(self.git(self.repo, "rev-parse", "FETCH_HEAD"), self.git(self.origin, "rev-parse", "HEAD"))
        self.assertEqual(list((self.local / "DE").iterdir()), [])

    def test_rush_pull_fetches_without_web_autofill_and_merges_published_text(self):
        args = self.args("rush-pull")
        with patch.object(translator, "fill_source_text", side_effect=AssertionError("Unexpected website lookup")):
            helper.command_rush_pull(args)
            self.rush_database(self.origin, "Latest translation")
            self.commit(self.origin)
            helper.command_rush_pull(args)
        local = self.local / "DE/Rush/raw2/rush-pre-release.cdb"
        self.assertEqual(rush.read_cdb(local)[1][100][2], "Latest translation")
        report = rush.read_json(self.local / "DE/Rush/Reports/sync.json")
        self.assertEqual(report["published_commit"], self.git(self.origin, "rev-parse", "HEAD"))

    def test_rush_conflict_resolution_uses_the_reviewed_commit(self):
        helper.command_rush_pull(self.args("rush-pull"))
        _, local, baseline, reports = translator.paths(self.repo, self.local, "DE")
        datas, rows = rush.read_cdb(local)
        rows[100] = (100, "Card", "Local edit", *([""] * 16))
        local.write_bytes(rush.cdb_bytes(datas, rows))
        self.rush_database(self.origin, "Remote edit")
        self.commit(self.origin)
        # Block the checkout's fast-forward; the published snapshot must still win.
        self.rush_database(self.repo, "Uncommitted repository edit")
        before = local.read_bytes()
        with self.assertRaisesRegex(SystemExit, "changed both locally"):
            helper.command_rush_pull(self.args("rush-pull"))
        self.assertEqual(local.read_bytes(), before)
        reviewed = rush.read_json(reports / "sync.json")["published_commit"]
        self.rush_database(self.origin, "Later unreviewed edit")
        self.commit(self.origin)
        helper.command_rush_refresh(self.args("rush-refresh", "--no-pull-first", "--keep-local", "--offline"))
        self.assertEqual(rush.read_cdb(local)[1][100][2], "Local edit")
        self.assertEqual(rush.read_cdb(baseline)[1][100][2], "Remote edit")
        self.assertEqual(rush.read_json(reports / "sync.json")["published_commit"], reviewed)

    def test_standard_upload_rejects_unsafe_options_before_copying(self):
        with patch.object(helper, "copy_workspace") as copy:
            with self.assertRaisesRegex(SystemExit, "either --push or --dry-run"):
                helper.command_upload(self.args("upload", "--push", "--dry-run"))
            copy.assert_not_called()
        self.write(self.repo / "staged.txt", b"unrelated work")
        self.git(self.repo, "add", "staged.txt")
        with patch.object(helper, "copy_workspace") as copy:
            with self.assertRaisesRegex(SystemExit, "already has staged changes"):
                helper.command_upload(self.args("upload", "--push"))
            copy.assert_not_called()

    def test_standard_upload_preflights_both_inputs(self):
        self.write(self.local / "DE/raw2/test-release.cdb", b"local translation")
        database = self.repo / "DE/Workspace/test-release.cdb"
        before = database.read_bytes()
        with self.assertRaisesRegex(SystemExit, "required file missing"):
            helper.copy_workspace(self.args("upload"))
        self.assertEqual(database.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
