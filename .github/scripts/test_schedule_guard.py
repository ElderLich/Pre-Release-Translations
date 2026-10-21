import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from pathlib import Path
from datetime import datetime

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
    def test_import_history_uses_successful_source_check_start_not_release_or_completion(self):
        run = {"conclusion": "success", "head_branch": "es/mobile",
               "run_started_at": "2026-10-13T15:54:35Z", "updated_at": "2026-10-18T05:30:00Z"}
        with patch.object(guard, "github_api", return_value={"workflow_runs": [run]}) as api:
            checked = guard.latest_import("es-workspace-sync.yml", "es/mobile")
        self.assertEqual(checked, int(datetime.fromisoformat("2026-10-13T15:54:35+00:00").timestamp()))
        api.assert_called_once_with("actions/workflows/es-workspace-sync.yml/runs?branch=es%2Fmobile&status=success&per_page=1")

    def test_absent_failed_wrong_branch_and_invalid_import_history_never_suppress_check(self):
        valid = {"conclusion": "success", "head_branch": "main", "run_started_at": "2026-10-13T15:54:35Z"}
        for runs in ([], [{**valid, "conclusion": "failure"}], [{**valid, "head_branch": "other"}],
                     [{**valid, "run_started_at": "bad"}], [{**valid, "run_started_at": None}],
                     [{**valid, "run_started_at": "2026-10-13T15:54:35"}],
                     [{"conclusion": "success", "head_branch": "main"}]):
            with self.subTest(runs=runs), patch.object(guard, "github_api", return_value={"workflow_runs": runs}):
                self.assertIsNone(guard.latest_import("es-workspace-sync.yml", "main"))
        with patch.object(guard, "github_api", side_effect=OSError("offline")):
            self.assertIsNone(guard.latest_import("es-workspace-sync.yml", "main"))

    def test_frequent_translation_releases_cannot_starve_es_source_import(self):
        with tempfile.TemporaryDirectory() as directory, in_directory(directory):
            version = Path("ES/version.txt")
            version.parent.mkdir()
            version.write_text("186400", encoding="utf-8")  # Released just now.
            with patch.object(guard, "LOCALES", ("ES",)), \
                 patch.dict(os.environ, {"GITHUB_REF_NAME": "main"}), \
                 patch.object(guard, "latest_import", return_value=100_000) as imported, \
                 patch.object(guard.time, "time", return_value=186_400), \
                 patch.object(guard, "upstream_modified", return_value=99_000), \
                 patch.object(guard, "rush_needs_update", return_value=(False, "unchanged")), \
                 patch.object(guard, "dispatch_workflow", return_value=True) as dispatch:
                guard.dispatch_daily(False, 24)
            imported.assert_called_once_with("es-workspace-sync.yml", "main")
            dispatch.assert_called_once_with("es-workspace-sync.yml", "main")

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

    def test_ten_hour_old_successful_import_skips_schedule(self):
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
        with patch.object(guard, "latest_import", return_value=100_000), patch.object(guard.time, "time", return_value=136_000), patch.object(guard, "upstream_modified", return_value=None), patch.object(guard, "rush_needs_update", return_value=(False, "unchanged")), patch.object(guard, "dispatch_workflow") as dispatch:
            guard.dispatch_daily(False, 24)
        dispatch.assert_not_called()

    def test_new_source_dispatches_despite_recent_imports(self):
        with patch.object(guard, "latest_import", return_value=100_000), patch.object(guard.time, "time", return_value=136_000), patch.object(guard, "upstream_modified", return_value=120_000), patch.object(guard, "rush_needs_update", return_value=(False, "unchanged")), patch.object(guard, "dispatch_workflow", return_value=True) as dispatch:
            guard.dispatch_daily(False, 24)
        self.assertEqual(dispatch.call_count, len(guard.LOCALES))

    def test_rush_changed_dispatches_only_rush_when_other_locales_fresh(self):
        with patch.object(guard, "latest_import", return_value=100_000), patch.object(guard.time, "time", return_value=136_000), patch.object(guard, "upstream_modified", return_value=None), patch.object(guard, "rush_needs_update", return_value=(True, "new Rush cards")), patch.object(guard, "dispatch_workflow", return_value=True) as dispatch:
            guard.dispatch_daily(False, 24)
        dispatch.assert_called_once_with("rush-prerelease.yml", "main")

    def test_force_bypasses_history_and_dispatches_every_locale(self):
        with patch.object(guard, "latest_import") as imported, \
             patch.object(guard, "upstream_modified", return_value=None), \
             patch.object(guard, "rush_needs_update", return_value=(False, "unchanged")), \
             patch.object(guard, "dispatch_workflow", return_value=True) as dispatch:
            guard.dispatch_daily(True, 24)
        imported.assert_not_called()
        self.assertEqual(dispatch.call_count, len(guard.LOCALES) + 1)


if __name__ == "__main__":
    unittest.main()
