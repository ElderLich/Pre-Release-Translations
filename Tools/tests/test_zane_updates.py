"""Offline tests for the mobile updater; no real network or workspace writes."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests


SCRIPT = Path(__file__).resolve().parents[1] / "Zane_Tests/AutoPR.py"
spec = importlib.util.spec_from_file_location("zane_update_autopr", SCRIPT)
zane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zane)


class ZaneUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        values = {
            "BASE_DIR": self.root, "RAW_DIR": self.root / "raw2",
            "MAPPINGS_CSV": self.root / "Mappings.csv",
            "STANDARD_BASELINE_CDB": self.root / ".standard-baseline.cdb",
            "STANDARD_BASELINE_CONF": self.root / ".standard-baseline.conf",
            "STANDARD_REMOTE_SHA_FILE": self.root / ".last_standard_remote_sha",
            "GITHUB_TOKEN_FILE": self.root / "missing-token",
        }
        for name, value in values.items():
            patcher = patch.object(zane, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def database(self, path, rows, attack=100):
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE datas (id INTEGER PRIMARY KEY, attack INTEGER)")
            connection.execute("CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT, desc TEXT)")
            connection.executemany("INSERT INTO datas VALUES (?, ?)", [(row[0], attack) for row in rows])
            connection.executemany("INSERT INTO texts VALUES (?, ?, ?)", rows)
        connection.close()
        return path.read_bytes()

    def metadata(self, data, inline=False):
        info = {"sha": zane._blob_sha(data), "size": len(data)}
        if inline:
            info.update(encoding="base64", content=base64.b64encode(data).decode())
        return info

    def test_import_has_no_filesystem_side_effects(self):
        destination = self.root / "not-created"
        with patch.dict(os.environ, {"ZANE_AUTOPR_BASE_DIR": str(destination),
                                     "ZANE_AUTOPR_CONFIG": str(self.root / "missing.json")}):
            fresh = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fresh)
        self.assertFalse(destination.exists())

    def test_old_official_repository_name_migrates_but_fork_does_not(self):
        config = self.root / "config.json"
        for owner, expected in (("ElderLich", "Pre-Release-Translations"), ("MyFork", "TransSuperpre")):
            config.write_text(json.dumps({"github_owner": owner, "github_repo": "TransSuperpre"}))
            with patch.dict(os.environ, {"ZANE_AUTOPR_CONFIG": str(config)}):
                fresh = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(fresh)
            self.assertEqual(fresh.GITHUB_REPO, expected)

    def test_read_without_token_has_timeout_and_mutations_require_token(self):
        session = MagicMock()
        session.request.return_value.json.return_value = {"ok": True}
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": ""}), patch.object(zane, "_http_session", return_value=session):
            self.assertEqual(zane.github_api_request("GET", "git/ref/heads/main"), {"ok": True})
            self.assertNotIn("Authorization", session.request.call_args.kwargs["headers"])
            self.assertEqual(session.request.call_args.kwargs["timeout"], zane.NETWORK_TIMEOUT)
            with self.assertRaises(FileNotFoundError):
                zane.github_api_request("POST", "git/commits", {"message": "x"})
        self.assertEqual(session.request.call_count, 1)

    def test_session_does_not_replay_write_status_failures(self):
        session = zane.get_requests_session()
        self.addCleanup(session.close)
        retry = session.get_adapter("https://").max_retries
        self.assertEqual(set(retry.allowed_methods), {"GET", "HEAD"})
        self.assertTrue(retry.is_retry("GET", 503))
        self.assertFalse(retry.is_retry("POST", 503))
        self.assertFalse(retry.is_retry("PATCH", 503))

    def test_metadata_pins_configured_branch_without_recursive_tree(self):
        commit = "a" * 40
        entries = [{"path": path, "sha": key * 40, "type": "file"}
                   for key, path in zane.STANDARD_FILES.items()]
        def api(method, endpoint):
            if endpoint == "git/ref/heads/es/mobile":
                return {"object": {"sha": commit}}
            self.assertIn(f"ref={commit}", endpoint)
            self.assertNotIn("recursive", endpoint)
            parent = endpoint.split("?")[0][len("contents/"):]
            return [entry for entry in entries if entry["path"].rsplit("/", 1)[0] == parent]
        with patch.object(zane, "GITHUB_BRANCH", "es/mobile"), patch.object(zane, "github_api_request", side_effect=api) as request:
            actual = zane._fetch_standard_remote_shas()
        self.assertEqual(set(actual), {"cdb", "conf", "mappings"})
        self.assertEqual(request.call_count, 3)

    def test_missing_remote_inventory_is_not_treated_as_up_to_date(self):
        with patch.object(zane, "github_api_request", side_effect=[{"object": {"sha": "a" * 40}}, [], []]):
            with self.assertRaisesRegex(RuntimeError, "Faltan archivos"):
                zane._fetch_standard_remote_shas()

    def test_rush_metadata_checks_directory_without_base64_database(self):
        info = {"path": zane.RUSH_REPO_CDB_PATH, "sha": "a" * 40, "type": "file"}
        with patch.object(zane, "github_api_request", return_value=[info]) as request:
            self.assertEqual(zane._remote_file_metadata(zane.RUSH_REPO_CDB_PATH), info)
        self.assertEqual(request.call_args.args, ("GET", "contents/ES/Rush/Workspace?ref=main"))

    def test_remote_sha_only_swallows_404(self):
        response = requests.Response()
        response.status_code = 403
        with patch.object(zane, "github_api_request", side_effect=requests.HTTPError(response=response)):
            with self.assertRaises(requests.HTTPError):
                zane.get_remote_file_sha("ES/Workspace/test-release.cdb")
            response.status_code = 404
            self.assertIsNone(zane.get_remote_file_sha("ES/Workspace/test-release.cdb"))

    def test_verified_cache_avoids_repeated_downloads_after_failed_sync(self):
        content = b"verified published file"
        metadata = self.metadata(content, inline=True)
        self.assertEqual(zane.download_remote_file("ES/file", metadata=metadata), content)
        with patch.object(zane, "_http_session") as session:
            self.assertEqual(zane.download_remote_file("ES/file", metadata={"sha": metadata["sha"]}), content)
            session.assert_not_called()
        self.assertEqual(len(list((self.root / ".download-cache").iterdir())), 1)

    def test_incorrect_inline_sha_does_not_create_cache(self):
        metadata = self.metadata(b"correct", inline=True)
        metadata["content"] = base64.b64encode(b"truncated").decode()
        with self.assertRaisesRegex(RuntimeError, "SHA"):
            zane.download_remote_file("ES/file", metadata=metadata)
        self.assertFalse((self.root / ".download-cache").exists())

    def test_interrupted_stream_retries_the_same_blob_then_caches(self):
        content = b"complete after reconnection"
        session, response = MagicMock(), MagicMock()
        session.get.return_value.__enter__.return_value = response
        response.iter_content.side_effect = [requests.exceptions.ChunkedEncodingError("lost"), [content]]
        with patch.object(zane, "_http_session", return_value=session), patch.object(zane, "time") as clock, patch.object(zane, "get_github_token", return_value=None):
            self.assertEqual(zane.download_remote_file("ES/file", metadata=self.metadata(content)), content)
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(session.get.call_args_list[0], session.get.call_args_list[1])
        self.assertTrue(session.get.call_args.args[0].endswith("git/blobs/" + zane._blob_sha(content)))
        clock.sleep.assert_called_once_with(1)

    def test_repeated_incomplete_response_does_not_replace_verified_cache(self):
        original = b"previous published file"
        zane.download_remote_file("ES/file", metadata=self.metadata(original, inline=True))
        session, response = MagicMock(), MagicMock()
        session.get.return_value.__enter__.return_value = response
        response.iter_content.return_value = [b"incomplete"]
        with patch.object(zane, "_http_session", return_value=session), patch.object(zane.time, "sleep"), patch.object(zane, "get_github_token", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "3 intentos"):
                zane.download_remote_file("ES/file", metadata=self.metadata(b"new full file"))
        cache, = (self.root / ".download-cache").iterdir()
        self.assertEqual(cache.read_bytes(), original)

    def test_atomic_write_failure_removes_temp_and_preserves_target(self):
        target = self.root / "target"
        target.write_bytes(b"original")
        with patch.object(Path, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                zane._atomic_write(target, b"new")
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_standard_merge_updates_unchanged_fields_preserving_local_edits(self):
        local = self.root / "local.cdb"
        self.database(zane.STANDARD_BASELINE_CDB, [(1, "Nombre", "Anterior"), (2, "Retirada", "Viejo")])
        self.database(local, [(1, "Mi nombre", "Anterior"), (2, "Retirada", "Viejo")])
        source = self.database(self.root / "source.cdb", [(1, "Nombre", "Nuevo texto"), (3, "Nueva", "Nueva carta")], attack=200)
        zane._merge_cdb_into_local(local, source)
        datas, texts = zane._read_cdb_from_path(local)
        self.assertEqual(texts, {1: (1, "Mi nombre", "Nuevo texto"), 3: (3, "Nueva", "Nueva carta")})
        self.assertEqual(datas[1][1], 200)

    def test_first_merge_replaces_untranslated_fields_with_published_spanish(self):
        local = self.root / "local.cdb"
        self.database(local, [(1, "中文", ""), (2, "Mi nombre", "Mi efecto")])
        source = self.database(self.root / "source.cdb", [(1, "Español", "Texto"), (2, "Publicado", "Efecto")])
        zane._merge_cdb_into_local(local, source)
        _, texts = zane._read_cdb_from_path(local)
        self.assertEqual(texts[1], (1, "Español", "Texto"))
        self.assertEqual(texts[2], (2, "Mi nombre", "Mi efecto"))

    def test_cdb_conflict_cancel_preserves_file_and_leaves_no_temp(self):
        local = self.root / "local.cdb"
        self.database(zane.STANDARD_BASELINE_CDB, [(1, "Original", "Anterior")])
        before = self.database(local, [(1, "Mi nombre", "Mi efecto")])
        source = self.database(self.root / "source.cdb", [(1, "Remoto", "Remoto efecto")])
        files = set(self.root.iterdir())
        with patch.object(zane, "_show_conflicts"), patch.object(zane, "_ask_conflict_resolution", return_value="cancel"):
            with self.assertRaisesRegex(RuntimeError, "cancelada"):
                zane._merge_cdb_into_local(local, source)
        self.assertEqual(local.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), files)

    def test_cdb_accept_repository_resolves_all_fields_on_same_card(self):
        local = self.root / "local.cdb"
        self.database(zane.STANDARD_BASELINE_CDB, [(1, "Original", "Anterior")])
        self.database(local, [(1, "Mi nombre", "Mi efecto")])
        source = self.database(self.root / "source.cdb", [(1, "Remoto", "Remoto efecto")])
        with patch.object(zane, "_show_conflicts"), patch.object(zane, "_ask_conflict_resolution", return_value="repo"):
            zane._merge_cdb_into_local(local, source)
        self.assertEqual(zane._read_cdb_from_path(local)[1][1], (1, "Remoto", "Remoto efecto"))

    def test_invalid_database_preserves_local_and_cleans_temporary(self):
        local = self.root / "local.cdb"
        before = self.database(local, [(1, "Local", "Efecto")])
        with self.assertRaises(sqlite3.DatabaseError):
            zane._merge_cdb_into_local(local, b"not a database")
        self.assertEqual(local.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [local])

    def test_strings_adopt_published_spanish_and_remote_only_updates(self):
        local = self.root / "test-strings.conf"
        local.write_text("!setname 1 Anterior\n!setname 2 Mi nombre\n", encoding="utf-8")
        zane.sync_test_strings_conf(b"!setname 1 Nuevo\n!setname 2 Original\n!setname 3 Publicado\n", local,
                                   baseline_bytes=b"!setname 1 Anterior\n!setname 2 Original\n")
        self.assertEqual(local.read_text(), "!setname 1 Nuevo\n!setname 2 Mi nombre\n!setname 3 Publicado\n")

    def test_strings_conflict_cancel_preserves_local(self):
        local = self.root / "test-strings.conf"
        local.write_bytes(b"!setname 1 Local\n")
        with patch.object(zane, "_show_conflicts"), patch.object(zane, "_ask_conflict_resolution", return_value="cancel"):
            with self.assertRaisesRegex(RuntimeError, "cancelada"):
                zane.sync_test_strings_conf(b"!setname 1 Remoto\n", local,
                                           baseline_bytes=b"!setname 1 Original\n")
        self.assertEqual(local.read_bytes(), b"!setname 1 Local\n")

    def test_strings_explicit_keep_local_honors_blank_and_cjk_text(self):
        for local_value in ("", "中文"):
            local = self.root / "test-strings.conf"
            local.write_text(f"!setname 1 {local_value}\n", encoding="utf-8")
            with patch.object(zane, "_show_conflicts"), patch.object(zane, "_ask_conflict_resolution", return_value="local"):
                zane.sync_test_strings_conf(b"!setname 1 Remoto\n", local,
                                           baseline_bytes=b"!setname 1 Original\n")
            entries, _ = zane._collect_test_strings_entries(local.read_text(encoding="utf-8"))
            self.assertEqual(entries["!setname 1"]["name"], local_value)

    def test_missing_baseline_migration_downloads_exact_old_blob_once(self):
        previous_sha = "b" * 40
        with patch.object(zane, "download_remote_file", return_value=b"old published data") as download:
            zane._restore_standard_baselines({"cdb": previous_sha})
            zane._restore_standard_baselines({"cdb": previous_sha})
        download.assert_called_once_with(zane.STANDARD_FILES["cdb"], metadata={"sha": previous_sha})
        self.assertEqual(zane.STANDARD_BASELINE_CDB.read_bytes(), b"old published data")

    def test_baseline_migration_connection_error_leaves_state_untouched(self):
        with patch.object(zane, "download_remote_file", side_effect=requests.Timeout("offline")):
            with self.assertRaises(requests.Timeout):
                zane._restore_standard_baselines({"cdb": "b" * 40})
        self.assertFalse(zane.STANDARD_BASELINE_CDB.exists())

    def test_missing_baseline_triggers_sync_even_when_remote_sha_unchanged(self):
        stored = {key: "a" * 40 for key in zane.STANDARD_FILES}
        for path in (zane.RAW_DIR / "test-release.cdb", zane.RAW_DIR / "test-strings.conf", zane.MAPPINGS_CSV):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"local")
        with patch.object(zane, "_fetch_standard_remote_shas", return_value=stored), \
             patch.object(zane, "_load_stored_shas", return_value=stored), \
             patch("builtins.input", return_value="n") as prompt:
            zane.option1_sync_only()
        self.assertEqual(prompt.call_count, 1)
        self.assertIn("Descargar y aplicar", prompt.call_args.args[0])

    def test_mappings_apply_es_overrides_without_other_languages(self):
        rows = zane._parse_mappings_text("lang,id,name,str1\nes,1,ES,2\nall,1,Global,1\nfr,1,FR,3\nfr,2,Only FR,4\n")
        self.assertEqual(rows[1]["str1"], "2")
        self.assertNotIn(2, rows)

    def test_unchanged_github_files_report_import_status_instead_of_claiming_cn_is_current(self):
        stored = {key: "a" * 40 for key in zane.STANDARD_FILES}
        for path in (zane.RAW_DIR / "test-release.cdb", zane.RAW_DIR / "test-strings.conf",
                     zane.MAPPINGS_CSV, zane.STANDARD_BASELINE_CDB, zane.STANDARD_BASELINE_CONF):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"unchanged")
        with patch.object(zane, "_fetch_standard_remote_shas", return_value=stored), \
             patch.object(zane, "_load_stored_shas", return_value=stored), \
             patch.object(zane, "_show_standard_import_status") as status, \
             patch.object(zane, "download_remote_file") as download, \
             patch.object(zane, "log") as log, patch("builtins.input", return_value="n"):
            zane.option1_sync_only()
        status.assert_called_once()
        download.assert_not_called()
        self.assertTrue(any("no comprueba directamente" in item.args[0] for item in log.call_args_list))

    def test_standard_status_reports_stale_import_and_failure_using_only_read_access(self):
        runs = [{"status": "completed", "conclusion": "failure"},
                {"status": "completed", "conclusion": "success", "run_started_at": "2026-10-13T15:54:35Z"}]
        with patch.object(zane, "github_api_request", return_value={"workflow_runs": runs}) as api, \
             patch.object(zane, "GITHUB_BRANCH", "es/mobile"), \
             patch.object(zane.time, "time", return_value=2_000_000_000), patch.object(zane, "log") as log:
            zane._show_standard_import_status()
        api.assert_called_once_with("GET", "actions/workflows/es-workspace-sync.yml/runs?branch=es%2Fmobile&per_page=20")
        messages = "\n".join(call.args[0] for call in log.call_args_list)
        self.assertIn("no terminó correctamente", messages)
        self.assertIn("2026-10-13", messages)
        self.assertIn("más de 24 horas", messages)

    def test_standard_status_reports_active_import_without_blocking_sync(self):
        with patch.object(zane, "github_api_request", return_value={"workflow_runs": [{"status": "in_progress"}]}), \
             patch.object(zane, "log") as log:
            zane._show_standard_import_status()
        messages = "\n".join(call.args[0] for call in log.call_args_list)
        self.assertIn("pendiente o en curso", messages)
        self.assertIn("No se pudo confirmar", messages)

    def test_status_network_failure_is_unknown_not_current_and_does_not_abort_sync(self):
        with patch.object(zane, "github_api_request", side_effect=requests.Timeout("offline")), \
             patch.object(zane, "log") as log:
            zane._show_standard_import_status()
        messages = "\n".join(call.args[0] for call in log.call_args_list)
        self.assertIn("No se pudo verificar", messages)
        self.assertIn("es-workspace-sync.yml", messages)

    def test_mapping_failure_never_advances_standard_sha(self):
        shas = {key: key for key in zane.STANDARD_FILES}
        with patch.object(zane, "_fetch_standard_remote_shas", return_value=shas), \
             patch.object(zane, "download_remote_file", return_value=b"payload"), \
             patch.object(zane, "_merge_cdb_into_local"), patch.object(zane, "sync_test_strings_conf"), \
             patch.object(zane, "_fuse_mappings", side_effect=OSError("disk full")), \
             patch.object(zane, "_save_stored_shas") as save, patch("builtins.input", return_value="s"):
            self.assertFalse(zane.option1_sync_only())
        save.assert_not_called()
        self.assertFalse(zane.STANDARD_BASELINE_CDB.exists())

    def test_import_translation_keeps_source_txt_without_timestamp_backups(self):
        local = self.root / "local.cdb"
        self.database(local, [(1, "中文", "Efecto")])
        source = self.root / "untranslated_str.txt"
        source.write_text("1\tname\tEspañol\n", encoding="utf-8")
        self.assertEqual(zane.import_str_translations(local, source), 1)
        self.assertTrue(source.is_file())
        self.assertEqual(set(self.root.iterdir()), {source, local})

    def test_standard_menu_recovers_from_an_operation_failure(self):
        with patch.object(zane, "option1_sync_only", side_effect=RuntimeError("offline")) as sync, \
             patch("builtins.input", side_effect=["1", "5"]), patch("builtins.print"):
            zane.standard_menu()
        sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
