"""Run the Rush workflow's publication shell against disposable Git remotes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Tools"))
from rush_prerelease import LANGS


class RushReleaseTagTests(unittest.TestCase):
    def setUp(self):
        git = Path(shutil.which("git"))
        bash = git.parent.parent / "bin/bash.exe" if os.name == "nt" else shutil.which("bash")
        if not bash or not Path(bash).is_file():
            self.skipTest("Git Bash is required to exercise the workflow shell")
        self.bash = str(bash)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "remote.git"
        self.git(self.root, "init", "--bare", str(self.remote))
        self.repo = self.root / "build"
        self.repo.mkdir()
        self.git(self.repo, "init", "-b", "main")
        self.git(self.repo, "config", "user.name", "Fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.invalid")
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        for locale in ("EN", "ES"):
            self.stage(f"{locale}/rush-pre-release.ypk", f"{locale} previous package")
            self.stage(f"{locale}/rush-version.txt", "1")
        self.git(self.repo, "commit", "-m", "Previous packages")
        self.git(self.repo, "push", "origin", "HEAD:main")
        self.previous = self.git(self.repo, "rev-parse", "HEAD")
        (self.root / "rush-summary.md").write_text("Rush build outcome\n", encoding="utf-8")
        self.summary = self.root / "step-summary.md"

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    def stage(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.git(self.repo, "add", "--", path)

    def publish(self, success=True):
        workflow = (ROOT / ".github/workflows/rush-prerelease.yml").read_text(encoding="utf-8")
        start = workflow.index("            if git diff --cached --quiet; then")
        end = workflow.index('            echo "Branch changed while building;')
        # Execute the actual no-op guard, tag selection, commit, atomic push and cleanup.
        script = 'set -euo pipefail\nexport PATH="/usr/bin:/bin:$PATH"\nbranch=main\n'
        script += workflow[start:end] + "\nexit 1\n"
        result = subprocess.run(
            [self.bash, "-c", script], cwd=self.repo, capture_output=True, text=True,
            env={**os.environ, "RUNNER_TEMP": self.root.as_posix(),
                 "GITHUB_STEP_SUMMARY": self.summary.as_posix(),
                 "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "fixture/translations"}
        )
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)

    def remote_tags(self):
        return self.git(self.remote, "tag", "--list").splitlines()

    def test_first_release_publishes_annotated_tags_at_the_package_commit(self):
        for locale in ("EN", "ES"):
            self.stage(f"{locale}/rush-pre-release.ypk", f"{locale} translated package")
        self.publish()
        self.assertEqual(self.remote_tags(), ["EN_Rush-v1", "ES_Rush-v1"])
        head = self.git(self.remote, "rev-parse", "refs/heads/main")
        for tag in self.remote_tags():
            self.assertEqual(self.git(self.remote, "cat-file", "-t", tag), "tag")
            self.assertEqual(self.git(self.remote, "rev-parse", f"{tag}^{{}}"), head)
            self.assertIn(f"/tree/{tag}", self.summary.read_text(encoding="utf-8"))
        self.assertIn("EN_Rush-v1 ES_Rush-v1", self.git(self.remote, "show", "-s", "--format=%B", head))

    def test_counters_are_numeric_independent_and_ignore_standard_or_preview_tags(self):
        for tag in ("EN_Rush-v9", "EN_Rush-v10", "ES_Rush-v3", "FR_Rush-v100",
                    "EN-v999", "EN_Rush-v99-preview", "ES_Rush-vlatest"):
            self.git(self.repo, "tag", tag)
        for locale in ("EN", "ES"):
            self.stage(f"{locale}/rush-pre-release.ypk", "changed")
        self.publish()
        self.assertEqual(self.remote_tags(), ["EN_Rush-v11", "ES_Rush-v4"])

    def test_all_configured_locales_get_their_own_tags(self):
        locales = [folder for folder, _ in LANGS.values()]
        for locale in locales:
            self.stage(f"{locale}/rush-pre-release.ypk", f"{locale} published package")
        self.publish()
        self.assertEqual(self.remote_tags(), sorted(f"{locale}_Rush-v1" for locale in locales))

    def test_pending_workspace_and_report_changes_do_not_tag_unchanged_packages(self):
        self.stage("EN/Rush/Workspace/rush-pre-release.cdb", "pending translation")
        self.stage("EN/Rush/report.json", '{"pending": 16}')
        self.stage("ES/rush-pre-release.ypk", "completed Spanish translation")
        self.publish()
        self.assertEqual(self.remote_tags(), ["ES_Rush-v1"])

    def test_metadata_only_commit_publishes_without_tags(self):
        self.stage("EN/rush-version.txt", "2")
        self.stage("EN/Rush/report.json", '{"pending": 16}')
        self.publish()
        self.assertEqual(self.remote_tags(), [])
        self.assertNotEqual(self.git(self.remote, "rev-parse", "refs/heads/main"), self.previous)

    def test_unchanged_run_leaves_branch_and_tags_untouched(self):
        self.publish()
        self.assertEqual(self.remote_tags(), [])
        self.assertEqual(self.git(self.remote, "rev-parse", "refs/heads/main"), self.previous)

    def test_deleted_package_does_not_create_release_tag(self):
        self.git(self.repo, "rm", "EN/rush-pre-release.ypk")
        self.publish()
        self.assertEqual(self.remote_tags(), [])

    def test_tag_collision_rejects_entire_push_and_retry_uses_next_number(self):
        self.git(self.remote, "tag", "EN_Rush-v1", self.previous)
        for locale in ("EN", "ES"):
            self.stage(f"{locale}/rush-pre-release.ypk", "new package")
        self.publish(success=False)
        self.assertEqual(self.git(self.remote, "rev-parse", "refs/heads/main"), self.previous)
        self.assertEqual(self.remote_tags(), ["EN_Rush-v1"])
        self.assertEqual(self.git(self.repo, "tag", "--list", "*_Rush-v*"), "")
        # Match the workflow's refresh/rebuild after a rejected attempt.
        self.git(self.repo, "fetch", "--tags", "origin", "main")
        self.git(self.repo, "checkout", "-B", "main", "origin/main")
        for locale in ("EN", "ES"):
            self.stage(f"{locale}/rush-pre-release.ypk", "new package")
        self.publish()
        self.assertEqual(self.remote_tags(), ["EN_Rush-v1", "EN_Rush-v2", "ES_Rush-v1"])
        self.assertEqual(self.git(self.remote, "rev-parse", "EN_Rush-v1"), self.previous)

    def test_competing_branch_push_rejects_tags_and_preserves_existing_local_tags(self):
        self.git(self.repo, "tag", "EN_Rush-v5")
        other = self.root / "other"
        self.git(self.root, "clone", "-b", "main", str(self.remote), str(other))
        (other / "another-translation.txt").write_text("translator edit", encoding="utf-8")
        self.git(other, "add", ".")
        self.git(other, "commit", "-m", "Another translator's upload")
        self.git(other, "push", "origin", "HEAD:main")
        competing = self.git(other, "rev-parse", "HEAD")
        for locale in ("EN", "ES"):
            self.stage(f"{locale}/rush-pre-release.ypk", "new package")
        self.publish(success=False)
        self.assertEqual(self.git(self.remote, "rev-parse", "refs/heads/main"), competing)
        self.assertEqual(self.remote_tags(), [])
        self.assertEqual(self.git(self.repo, "tag", "--list"), "EN_Rush-v5")


if __name__ == "__main__":
    unittest.main()
