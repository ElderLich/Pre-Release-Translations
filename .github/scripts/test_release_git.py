"""Exercise shallow branch refresh and the actual standard publication shell."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from release_git import next_number

ROOT = Path(__file__).resolve().parents[2]


class ReleaseGitTests(unittest.TestCase):
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
        self.seed = self.root / "seed"
        self.seed.mkdir()
        self.git(self.seed, "init", "-b", "main")
        self.git(self.seed, "remote", "add", "origin", str(self.remote))
        for index in range(4):
            (self.seed / "history.txt").write_text(str(index), encoding="utf-8")
            self.git(self.seed, "add", ".")
            self.git(self.seed, "commit", "-m", str(index))
            if index == 0:
                for tag in ("EN-v9", "EN-v10", "EN-v1775706240", "EN-v11-preview", "FR-v88"):
                    self.git(self.seed, "tag", "-a", tag, "-m", tag)
        self.git(self.seed, "push", "--tags", "origin", "main")
        self.repo = self.root / "build"
        self.git(self.root, "clone", "--depth=1", "--no-tags", "--branch", "main",
                 self.remote.as_uri(), str(self.repo))
        helpers = self.repo / ".github/scripts"
        helpers.mkdir(parents=True)
        shutil.copyfile(ROOT / ".github/scripts/release_git.py", helpers / "release_git.py")
        self.git(self.repo, "config", "user.name", "Fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.invalid")
        self.before = self.git(self.remote, "rev-parse", "main")

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()

    def shell(self, script, success=True):
        result = subprocess.run([self.bash, "-c", "set -euo pipefail\n" + script],
                                cwd=self.repo, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
        return result.stdout

    def helper(self, *args):
        return subprocess.run([sys.executable, str(self.repo / ".github/scripts/release_git.py"), *args],
                              cwd=self.repo, capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()

    def stage_package(self, locale="EN"):
        folder = self.repo / locale
        folder.mkdir(exist_ok=True)
        (folder / "ygopro-super-pre.ypk").write_text("new package", encoding="utf-8")
        (folder / ".autopr-commit-body.txt").write_text("Fixture release\n", encoding="utf-8")
        self.git(self.repo, "add", f"{locale}/ygopro-super-pre.ypk")

    def publish(self, workflow="en-release.yml", success=True):
        text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
        start = text.index("            next_tag_number=")
        end = text.index('            echo "Push failed;', start)
        body = text[start:end].replace("python .github/scripts/release_git.py",
                                       shlex.quote(sys.executable.replace("\\", "/")) + " .github/scripts/release_git.py")
        self.shell("branch=main\n" + body + "\nexit 1\n", success=success)

    def refresh(self, workflow="en-release.yml"):
        text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
        start = text.index("            git fetch ")
        end = text.index("\n", text.index("            git checkout ", start))
        self.shell("branch=main\n" + text[start:end])

    def test_remote_numbers_include_old_annotated_tags_without_downloading_history(self):
        self.assertEqual(self.helper("next-number", "--prefix", "EN-v", "--exclude-from", "10000"), "11")
        self.assertEqual(self.git(self.repo, "rev-list", "--count", "HEAD"), "1")
        self.assertEqual(self.git(self.repo, "tag", "--list"), "")
        self.assertEqual(next_number(["EN_Rush-v10000", "EN_Rush-v9", "EN_Rush-v11-preview"], "EN_Rush-v"), 10001)

    def test_remote_lookup_failure_does_not_invent_first_tag(self):
        self.git(self.repo, "remote", "set-url", "origin", str(self.root / "missing.git"))
        result = subprocess.run([sys.executable, str(self.repo / ".github/scripts/release_git.py"),
                                 "next-number", "--prefix", "EN-v"], cwd=self.repo, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Unable to list remote release tags", result.stderr)

    def test_refresh_remains_shallow_and_standard_release_pushes_branch_and_tag(self):
        self.refresh()
        self.assertEqual(self.git(self.repo, "rev-list", "--count", "HEAD"), "1")
        self.assertEqual(self.git(self.repo, "tag", "--list"), "")
        self.stage_package()
        self.publish()
        head = self.git(self.remote, "rev-parse", "main")
        self.assertNotEqual(head, self.before)
        self.assertEqual(self.git(self.remote, "rev-parse", "EN-v11^{}"), head)
        self.assertEqual(self.git(self.remote, "cat-file", "-t", "EN-v11"), "tag")

    def test_competing_translation_rejects_tag_then_rebuilds_from_latest_tip(self):
        (self.seed / "translator.txt").write_text("keep this translation", encoding="utf-8")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "Human edit while building")
        self.git(self.seed, "push", "origin", "main")
        competing = self.git(self.remote, "rev-parse", "main")
        self.stage_package()
        self.publish(success=False)
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), competing)
        self.assertEqual(self.git(self.remote, "tag", "--list", "EN-v11"), "")
        self.assertEqual(self.git(self.repo, "tag", "--list"), "")
        self.refresh()
        self.assertEqual(self.git(self.repo, "rev-list", "--count", "HEAD"), "1")
        self.assertEqual((self.repo / "translator.txt").read_text(encoding="utf-8"), "keep this translation")
        self.stage_package()
        self.publish()
        self.assertEqual(self.git(self.remote, "show", "main:translator.txt"), "keep this translation")

    def test_tw_rebuild_publication_uses_legacy_prefix_without_rebase(self):
        self.refresh("autopr-zh-tw.yml")
        self.stage_package("ZH-TW")
        self.publish("autopr-zh-tw.yml")
        self.assertEqual(self.git(self.remote, "rev-parse", "TW-v1^{}"), self.git(self.remote, "rev-parse", "main"))


if __name__ == "__main__":
    unittest.main()
