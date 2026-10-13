"""Offline publication checks: no network, real repository writes, or live tags."""
import base64
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import requests


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("zane_publish_autopr", ROOT / "Tools/Zane_Tests/AutoPR.py")
zane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zane)


class FakeGitHub:
    def __init__(self, contents):
        self.contents = contents
        self.head = "old-head"
        self.calls = []
        self.ref_failure = None

    def request(self, method, endpoint, data=None):
        self.calls.append((method, endpoint, data))
        if method == "GET" and endpoint.startswith("git/ref/heads/"):
            return {"object": {"sha": self.head}}
        if method == "GET" and endpoint == "git/commits/old-head":
            return {"tree": {"sha": "complete-existing-tree"}}
        if method == "GET" and endpoint.startswith("contents/"):
            assert endpoint.endswith("?ref=old-head"), endpoint
            parent = endpoint[len("contents/"):].split("?")[0]
            return [{"path": path, "sha": zane._blob_sha(content)}
                    for path, content in self.contents.items() if path.rsplit("/", 1)[0] == parent]
        if (method, endpoint) == ("POST", "git/blobs"):
            return {"sha": zane._blob_sha(base64.b64decode(data["content"]))}
        if (method, endpoint) == ("POST", "git/trees"):
            return {"sha": "updated-tree"}
        if (method, endpoint) == ("POST", "git/commits"):
            return {"sha": "new-commit"}
        if method == "PATCH" and endpoint.startswith("git/refs/heads/"):
            assert data["force"] is False
            if self.ref_failure == "race":
                self.head = "someone-elses-commit"
                response = requests.Response()
                response.status_code = 422
                raise requests.HTTPError("Not a fast forward", response=response)
            if self.ref_failure == "lost-response":
                self.head = data["sha"]
                raise requests.Timeout("Response lost after server accepted publication")
            self.head = data["sha"]
            return {"object": {"sha": self.head}}
        raise AssertionError((method, endpoint, data))


class ZanePublicationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cdb = self.root / "test-release.cdb"
        self.conf = self.root / "test-strings.conf"
        self.cdb.write_bytes(b"translated-cdb")
        self.conf.write_bytes(b"!setname 0x1 Nombre\n")
        self.cdb_remote = zane.STANDARD_FILES["cdb"]
        self.conf_remote = zane.STANDARD_FILES["conf"]
        self.api = FakeGitHub({self.cdb_remote: b"old-cdb", self.conf_remote: b"old-conf"})
        self.expected = {path: zane._blob_sha(content) for path, content in self.api.contents.items()}
        self.api_patch = patch.object(zane, "github_api_request", side_effect=self.api.request)
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)

    def publish(self, files=None, expected=None):
        return zane.upload_files_via_api(
            files or [(self.cdb, self.cdb_remote), (self.conf, self.conf_remote)],
            "Spanish translations", expected_shas=self.expected if expected is None else expected
        )

    def test_two_files_publish_in_one_commit_and_one_branch_update(self):
        result = self.publish()
        self.assertEqual(result["changed"], [self.cdb_remote, self.conf_remote])
        trees = [data for method, path, data in self.api.calls if path == "git/trees"]
        self.assertEqual(len(trees), 1)
        self.assertEqual(trees[0]["base_tree"], "complete-existing-tree")
        self.assertEqual({entry["path"] for entry in trees[0]["tree"]}, set(self.expected))
        commits = [data for method, path, data in self.api.calls if path == "git/commits"]
        self.assertEqual(commits, [{"message": "Spanish translations", "tree": "updated-tree", "parents": ["old-head"]}])
        updates = [data for method, path, data in self.api.calls if method == "PATCH"]
        self.assertEqual(updates, [{"sha": "new-commit", "force": False}])
        self.assertFalse(any("tags" in path for _, path, _ in self.api.calls))
        self.assertEqual(result["contents"][self.cdb_remote], b"translated-cdb")

    def test_unchanged_files_do_not_upload_or_create_commits(self):
        self.api.contents = {self.cdb_remote: self.cdb.read_bytes(), self.conf_remote: self.conf.read_bytes()}
        result = self.publish(expected={})
        self.assertEqual(result["changed"], [])
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.calls))

    def test_changed_remote_is_rejected_before_uploading_any_blobs(self):
        self.api.contents[self.conf_remote] = b"someone else's translation"
        with self.assertRaisesRegex(RuntimeError, "sincroniza"):
            self.publish()
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.calls))

    def test_missing_sync_baseline_cannot_overwrite_remote(self):
        with self.assertRaisesRegex(RuntimeError, "base sincronizada"):
            self.publish(expected={})
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.calls))

    def test_unchanged_file_is_excluded_from_upload(self):
        self.api.contents[self.conf_remote] = self.conf.read_bytes()
        result = self.publish()
        self.assertEqual(result["changed"], [self.cdb_remote])
        self.assertEqual(len([1 for _, path, _ in self.api.calls if path == "git/blobs"]), 1)

    def test_concurrent_push_is_never_force_overwritten(self):
        self.api.ref_failure = "race"
        with self.assertRaisesRegex(RuntimeError, "no se forzó"):
            self.publish()
        self.assertEqual(self.api.head, "someone-elses-commit")
        self.assertEqual(len([1 for method, _, _ in self.api.calls if method == "PATCH"]), 1)

    def test_dropped_response_is_confirmed_without_repeating_write(self):
        self.api.ref_failure = "lost-response"
        result = self.publish()
        self.assertEqual(result["commit"], "new-commit")
        self.assertEqual(len([1 for method, _, _ in self.api.calls if method == "PATCH"]), 1)

    def test_menu_saves_confirmed_baseline_without_requesting_a_tag(self):
        cdb_baseline = self.root / ".standard-baseline.cdb"
        conf_baseline = self.root / ".standard-baseline.conf"
        old_shas = {"cdb": self.expected[self.cdb_remote], "conf": self.expected[self.conf_remote]}
        with patch.object(zane, "RAW_DIR", self.root), \
             patch.object(zane, "STANDARD_BASELINE_CDB", cdb_baseline), \
             patch.object(zane, "STANDARD_BASELINE_CONF", conf_baseline), \
             patch.object(zane, "check_github_token", return_value=True), \
             patch.object(zane, "_load_stored_shas", return_value=old_shas), \
             patch.object(zane, "_save_stored_shas") as save, \
             patch("builtins.input", side_effect=["3"]):
            zane.option2_full_update()
        self.assertEqual(cdb_baseline.read_bytes(), self.cdb.read_bytes())
        self.assertEqual(conf_baseline.read_bytes(), self.conf.read_bytes())
        self.assertEqual(save.call_args.args[0]["cdb"], zane._blob_sha(self.cdb.read_bytes()))

    def test_unexpected_remote_path_is_rejected_without_requests(self):
        with self.assertRaises(ValueError):
            self.publish(files=[(self.cdb, ".github/workflows/es-release.yml")])
        self.assertEqual(self.api.calls, [])


class SpanishReleaseGitTests(unittest.TestCase):
    """Exercise the workflow's atomic push against disposable local Git remotes."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.remote = self.root / "remote.git"
        self.git(self.root, "init", "--bare", str(self.remote))
        self.repo = self.root / "build"
        self.repo.mkdir()
        self.git(self.repo, "init", "-b", "main")
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        self.commit("workspace")
        self.git(self.repo, "push", "origin", "HEAD:main")
        self.workspace_sha = self.git(self.repo, "rev-parse", "HEAD")
        self.commit("compiled-package")
        self.git(self.repo, "tag", "-a", "ES-v1", "-m", "Release ES-v1")

    def git(self, cwd, *args, check=True):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=cwd, capture_output=True, text=True, check=check
        ).stdout.strip()

    def commit(self, content):
        (self.repo / "fixture.txt").write_text(content, encoding="utf-8")
        self.git(self.repo, "add", "fixture.txt")
        self.git(self.repo, "commit", "-m", content)

    def test_success_publishes_build_and_tag_together(self):
        self.git(self.repo, "push", "--atomic", "origin", "HEAD:main", "refs/tags/ES-v1")
        branch = self.git(self.remote, "rev-parse", "refs/heads/main")
        tag = self.git(self.remote, "rev-parse", "refs/tags/ES-v1^{}")
        self.assertEqual(branch, tag)
        self.assertNotEqual(branch, self.workspace_sha)

    def test_tag_collision_cannot_publish_an_untagged_build(self):
        self.git(self.remote, "tag", "ES-v1", self.workspace_sha)
        with self.assertRaises(subprocess.CalledProcessError):
            self.git(self.repo, "push", "--atomic", "origin", "HEAD:main", "refs/tags/ES-v1")
        self.assertEqual(self.git(self.remote, "rev-parse", "refs/heads/main"), self.workspace_sha)
        self.assertEqual(self.git(self.remote, "rev-parse", "refs/tags/ES-v1"), self.workspace_sha)

    def bash(self, script):
        git = Path(shutil.which("git"))
        executable = git.parent.parent / "bin/bash.exe" if os.name == "nt" else shutil.which("bash")
        if not executable or not Path(executable).is_file():
            self.skipTest("Git Bash is required to exercise the release shell")
        if os.name == "nt":
            script = 'export PATH="/usr/bin:/bin:$PATH"\n' + script
        return subprocess.run([str(executable), "-c", script], cwd=self.repo,
                              check=True, capture_output=True, text=True).stdout.strip()

    def workflow_tag_number(self):
        lines = (ROOT / ".github/workflows/es-release.yml").read_text(encoding="utf-8").splitlines()
        assignment = next(line.strip() for line in lines if "latest_tag=$(" in line)
        return self.bash('set -euo pipefail\n' + assignment + '\nprintf "%s" "$(( ${latest_tag:-0} + 1 ))"')

    def test_empty_tag_history_starts_at_one_under_pipefail(self):
        self.git(self.repo, "tag", "-d", "ES-v1")
        self.assertEqual(self.workflow_tag_number(), "1")

    def test_tag_number_ignores_legacy_timestamps_and_non_numeric_suffixes(self):
        for tag in ("ES-v1775706240", "ES-v3", "ES-v8", "ES-v9-preview"):
            self.git(self.repo, "tag", tag)
        self.assertEqual(self.workflow_tag_number(), "9")


if __name__ == "__main__":
    unittest.main()
