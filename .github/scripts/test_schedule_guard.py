import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location("schedule_guard", Path(__file__).with_name("schedule_guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@contextmanager
def in_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class ScheduleGuardTests(unittest.TestCase):
    def test_manual_release_commit_controls_next_schedule_not_unrelated_commits(self):
        with tempfile.TemporaryDirectory() as directory, in_directory(directory):
            def run(*args, **kwargs):
                return subprocess.run(["git", *args], check=True, capture_output=True, **kwargs)
            run("init")
            run("config", "user.name", "Schedule test")
            run("config", "user.email", "test@example.invalid")
            version = Path("EN/version.txt")
            version.parent.mkdir()
            version.write_text("old", encoding="utf-8")
            run("add", ".")
            run("commit", "-m", "Old release", env={**os.environ, "GIT_AUTHOR_DATE": "2026-10-09T08:00:00Z", "GIT_COMMITTER_DATE": "2026-10-09T08:00:00Z"})
            version.write_text("new", encoding="utf-8")
            run("add", ".")
            run("commit", "-m", "Manual English release", env={**os.environ, "GIT_AUTHOR_DATE": "2026-10-10T22:10:00Z", "GIT_COMMITTER_DATE": "2026-10-10T22:10:00Z"})
            manual_release_time = int(guard.git("log", "-1", "--format=%ct"))
            Path("unrelated.txt").write_text("another locale changed", encoding="utf-8")
            run("add", ".")
            run("commit", "-m", "Unrelated update", env={**os.environ, "GIT_AUTHOR_DATE": "2026-10-11T07:10:00Z", "GIT_COMMITTER_DATE": "2026-10-11T07:10:00Z"})
            released = guard.latest_release("EN/version.txt")
            self.assertEqual(released, manual_release_time)
            self.assertFalse(guard.cooldown_decision("schedule", released, manual_release_time + 10 * 3600)[0])

    def test_single_locale_receipt_cannot_mark_all_rush_locales_current(self):
        with tempfile.TemporaryDirectory() as directory, in_directory(directory):
            receipt = Path("Shared/Rush/source.json")
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({"built_locales": ["EN"]}), encoding="utf-8")
            with patch.object(guard.subprocess, "check_output") as remote:
                needed, reason = guard.rush_needs_update()
            self.assertTrue(needed)
            self.assertIn("every locale", reason)
            remote.assert_not_called()

    def test_pending_rush_translation_retries_even_with_unchanged_receipt(self):
        with tempfile.TemporaryDirectory() as directory, in_directory(directory):
            receipt = Path("Shared/Rush/source.json")
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({"built_locales": sorted(guard.RUSH_LOCALES)}), encoding="utf-8")
            report = Path("FR/Rush/report.json")
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps({"pending_translations": [{"id": 123, "fields": ["desc"]}]}), encoding="utf-8")
            with patch.object(guard.subprocess, "check_output") as remote:
                needed, reason = guard.rush_needs_update()
            self.assertTrue(needed)
            self.assertIn("retrying daily sources", reason)
            remote.assert_not_called()

    def test_ten_hour_old_release_skips_schedule(self):
        self.assertFalse(guard.cooldown_decision("schedule", 100_000, 136_000)[0])

    def test_manual_and_push_are_never_cooled_down(self):
        for event in ("workflow_dispatch", "push"):
            self.assertTrue(guard.cooldown_decision(event, 100_000, 136_000)[0])

    def test_daily_poll_resumes_after_twenty_four_hours(self):
        self.assertTrue(guard.cooldown_decision("schedule", 100_000, 186_400)[0])

    def test_new_upstream_bypasses_cooldown(self):
        self.assertTrue(guard.cooldown_decision("schedule", 100_000, 136_000, upstream_modified=110_000)[0])

    def test_old_or_missing_upstream_does_not_bypass_cooldown(self):
        for modified in (None, 99_000):
            self.assertFalse(guard.cooldown_decision("schedule", 100_000, 136_000, upstream_modified=modified)[0])

    def test_first_run_and_invalid_timestamps_run(self):
        for released in (None, 0, 200_000):
            self.assertTrue(guard.cooldown_decision("schedule", released, 136_000)[0])

    def test_active_workflow_is_not_dispatched_twice(self):
        with patch.object(guard, "github_api", return_value={"workflow_runs": [{"status": "in_progress"}]}) as api:
            self.assertFalse(guard.dispatch_workflow("en-workspace-sync.yml", "main"))
        self.assertEqual(api.call_count, 1)

    def test_finished_workflow_can_be_dispatched(self):
        with patch.object(guard, "github_api", return_value={"workflow_runs": [{"status": "completed"}]}) as api:
            self.assertTrue(guard.dispatch_workflow("en-workspace-sync.yml", "main"))
        self.assertEqual(api.call_count, 2)

    def test_all_fresh_locales_create_no_locale_workflows(self):
        with patch.object(guard, "latest_release", return_value=100_000), patch.object(guard.time, "time", return_value=136_000), patch.object(guard, "upstream_modified", return_value=None), patch.object(guard, "rush_needs_update", return_value=(False, "unchanged")), patch.object(guard, "dispatch_workflow") as dispatch:
            guard.dispatch_daily(False, 24)
        dispatch.assert_not_called()

    def test_new_source_dispatches_despite_recent_releases(self):
        with patch.object(guard, "latest_release", return_value=100_000), patch.object(guard.time, "time", return_value=136_000), patch.object(guard, "upstream_modified", return_value=120_000), patch.object(guard, "rush_needs_update", return_value=(False, "unchanged")), patch.object(guard, "dispatch_workflow", return_value=True) as dispatch:
            guard.dispatch_daily(False, 24)
        self.assertEqual(dispatch.call_count, len(guard.LOCALES))

    def test_rush_changed_dispatches_only_rush_when_other_locales_fresh(self):
        with patch.object(guard, "latest_release", return_value=100_000), patch.object(guard.time, "time", return_value=136_000), patch.object(guard, "upstream_modified", return_value=None), patch.object(guard, "rush_needs_update", return_value=(True, "new Rush cards")), patch.object(guard, "dispatch_workflow", return_value=True) as dispatch:
            guard.dispatch_daily(False, 24)
        dispatch.assert_called_once_with("rush-prerelease.yml", "main")


if __name__ == "__main__":
    unittest.main()
