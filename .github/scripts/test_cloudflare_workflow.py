"""Exercise the actual mirror workflow shell without credentials or networking."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/cloudflare-prerelease.yml"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
REQUIRED = (
    "R2_ACCOUNT_ID", "R2_PRERELEASE_BUCKET", "R2_PRERELEASE_PUBLIC_BASE_URL",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
)


class CloudflareWorkflowTests(unittest.TestCase):
    def setUp(self):
        git_path = shutil.which("git")
        bash = Path(git_path).parent.parent / "bin/bash.exe" if os.name == "nt" and git_path else shutil.which("bash")
        if not bash or not Path(bash).is_file():
            self.skipTest("Git Bash is required to exercise the workflow shell")
        self.bash = str(bash)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.stub(bin_dir / "git", 'test "$*" = "rev-parse HEAD" || exit 90\nprintf "%s\\n" "$SOURCE_COMMIT"\n')
        self.stub(bin_dir / "python", 'printf "%s\\n" "$@" > "$CALLS_PATH"\nexit "${PUBLISH_EXIT:-0}"\n')
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step = workflow.split("      - name: Publish verified packages and catalog\n", 1)[1]
        self.script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        self.env = {key: value for key, value in os.environ.items() if key not in REQUIRED}
        self.env.update({
            "R2_ACCOUNT_ID": "a" * 32,
            "R2_PRERELEASE_BUCKET": "mdpro4-prerelease",
            "R2_PRERELEASE_PUBLIC_BASE_URL": "https://cards.example.invalid",
            "AWS_ACCESS_KEY_ID": "fixture-access-key",
            "AWS_SECRET_ACCESS_KEY": "fixture-secret-value",
            "GH_TOKEN": "fixture-github-token",
            "SOURCE_COMMIT": COMMIT,
            "GITHUB_WORKSPACE": self.root.as_posix(),
            "GITHUB_STEP_SUMMARY": "step-summary.md",
            "CALLS_PATH": "invocation.args",
        })

    @staticmethod
    def stub(path, body):
        path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def run_step(self, overrides=None):
        environment = {**self.env, **(overrides or {})}
        result = subprocess.run(
            [self.bash, "-c", 'export PATH="$PWD/bin:/usr/bin:/bin:$PATH"\n' + self.script],
            cwd=self.root, env=environment, capture_output=True, text=True,
        )
        self.assertNotIn(environment["AWS_ACCESS_KEY_ID"], result.stdout + result.stderr)
        self.assertNotIn(environment["AWS_SECRET_ACCESS_KEY"], result.stdout + result.stderr)
        self.assertNotIn(environment["GH_TOKEN"], result.stdout + result.stderr)
        return result

    def test_complete_setup_passes_pinned_source_and_public_destination(self):
        result = self.run_step()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = (self.root / "invocation.args").read_text(encoding="utf-8").splitlines()
        self.assertEqual(args, [
            ".github/scripts/publish_prerelease_r2.py",
            "--source-dir", self.root.as_posix(),
            "--source-repository", "ElderLich/Pre-Release-Translations",
            "--source-commit", COMMIT,
            "--public-base-url", "https://cards.example.invalid",
            "--bucket", "mdpro4-prerelease",
            "--endpoint-url", "https://" + "a" * 32 + ".r2.cloudflarestorage.com",
            "--catalog-key", "prerelease/catalog-v1.json",
        ])
        self.assertNotIn("fixture-secret-value", " ".join(args))
        self.assertNotIn("fixture-access-key", " ".join(args))

    def test_each_missing_setting_visibly_skips_without_invoking_publisher(self):
        for missing in REQUIRED:
            with self.subTest(missing=missing):
                # Blank is exactly how an unset Actions secret/variable resolves.
                saved = self.env[missing]
                self.env[missing] = ""
                try:
                    result = subprocess.run(
                        [self.bash, "-c", 'export PATH="$PWD/bin:/usr/bin:/bin:$PATH"\n' + self.script],
                        cwd=self.root, env=self.env, capture_output=True, text=True,
                    )
                finally:
                    self.env[missing] = saved
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse((self.root / "invocation.args").exists())
                self.assertIn("Nothing was uploaded or promoted", result.stdout)
                self.assertIn(missing, result.stdout)
                self.assertIn("not configured", (self.root / "step-summary.md").read_text(encoding="utf-8"))
                for secret in ("fixture-access-key", "fixture-secret-value", "fixture-github-token"):
                    self.assertNotIn(secret, result.stdout + result.stderr)

    def test_publisher_failure_fails_the_workflow(self):
        result = self.run_step({"PUBLISH_EXIT": "23"})
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertTrue((self.root / "invocation.args").exists())
        self.assertNotIn("not configured", result.stdout)

    def test_every_current_package_publisher_is_a_completion_trigger(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        trigger_block = workflow.split("    workflows:\n", 1)[1].split("    branches:", 1)[0]
        publishers = [*sorted((ROOT / ".github/workflows").glob("??-release.yml")),
                      ROOT / ".github/workflows/autopr-zh-tw.yml",
                      ROOT / ".github/workflows/rush-prerelease.yml"]
        self.assertEqual(len(publishers), 11)
        for path in publishers:
            name = path.read_text(encoding="utf-8").splitlines()[0].removeprefix("name: ").strip()
            self.assertIn('      - "' + name + '"', trigger_block, str(path))


if __name__ == "__main__":
    unittest.main()
