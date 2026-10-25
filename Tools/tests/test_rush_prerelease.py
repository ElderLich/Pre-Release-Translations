import importlib.util
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("rush_prerelease", Path(__file__).resolve().parents[1] / "rush_prerelease.py")
rush = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rush)


class RushPrereleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root, self.source, self.translations = base / "repo", base / "source", base / "data/Data/locales"
        (self.source / "script").mkdir(parents=True)
        (self.source / "script/RDBase.lua").write_text("RD = {}", encoding="utf-8")
        (self.root / "Shared/Rush Artworks").mkdir(parents=True)
        self.set_cards([100, 101])
        for card_id in (100, 101):
            (self.root / f"Shared/Rush Artworks/{card_id}.jpg").write_bytes(b"\xff\xd8\xfffixture")
        self.seed("en-US", [100, 101])
        self.seed("de-DE", [100, 101])

    def set_cards(self, ids):
        self.datas = {i: (i, 4, 0, 0, 33, 1000, 1000, 4, 1, 1, 0) for i in ids}
        self.texts = {i: (i, f"原始卡名{i}", "这是原始效果文字", *([""] * 16)) for i in ids}
        self.write_source()
        for card_id in ids:
            (self.source / f"script/c{card_id}.lua").write_text("local s,id=GetID()", encoding="utf-8")

    def write_source(self):
        rush.write_changed(self.source / "RD Patch.cdb", rush.cdb_bytes(self.datas, self.texts))

    def seed(self, locale, ids):
        texts = {i: (i, f"Translated {i}", "Translated effect.", *([""] * 16)) for i in ids}
        datas = {i: self.datas[i] for i in ids}
        rush.write_changed(self.translations / locale / "rush_cards.cdb", rush.cdb_bytes(datas, texts))

    def sync(self, languages=None):
        return rush.synchronize(self.root, self.source, self.translations, languages or ["en"])

    def package(self, language="EN"):
        return self.root / language / "rush-pre-release.ypk"

    def workspace(self, language="EN"):
        return self.root / language / "Rush/Workspace/rush-pre-release.cdb"

    def edit_text(self, path, card_id, text):
        datas, texts = rush.read_cdb(path)
        row = list(texts[card_id]); row[2] = text; texts[card_id] = tuple(row)
        rush.write_changed(path, rush.cdb_bytes(datas, texts))

    def test_package_uses_host_ids_and_all_required_components(self):
        self.sync()
        with zipfile.ZipFile(self.package()) as archive:
            self.assertEqual(set(archive.namelist()), {"rush-pre-release.cdb", "script/RDBase.lua",
                "script/c100.lua", "script/c101.lua", "Picture/Rush_Arts/100.jpg", "Picture/Rush_Arts/101.jpg"})
            self.assertIsNone(archive.testzip())

    def test_source_removal_prunes_every_locale_database_script_and_artwork(self):
        self.sync(["en", "de"])
        self.set_cards([100])
        self.sync(["en", "de"])
        for locale in ("EN", "DE"):
            self.assertEqual(set(rush.read_cdb(self.workspace(locale))[0]), {100})
            with zipfile.ZipFile(self.package(locale)) as archive:
                self.assertNotIn("script/c101.lua", archive.namelist())
                self.assertNotIn("Picture/Rush_Arts/101.jpg", archive.namelist())
        self.assertTrue((self.root / "Shared/Rush Artworks/101.jpg").exists())

    def test_seed_does_not_resurrect_removed_source_prompt(self):
        source = (100, "原名", "原文", *([""] * 16))
        translated = (100, "Name", "Effect", *([""] * 16))
        seed = (100, "Seed name", "Seed effect", "Obsolete choice", *([""] * 15))
        self.assertEqual(rush.backfill_seed(translated, source, seed), translated)
        current = list(source); current[3] = "当前选择"
        self.assertEqual(rush.backfill_seed(translated, tuple(current), seed)[3], "Obsolete choice")

    def test_existing_translation_survives_upstream_gameplay_change(self):
        self.sync()
        self.edit_text(self.workspace(), 100, "Translator correction.")
        row = list(self.datas[100]); row[5] = 1800; self.datas[100] = tuple(row); self.write_source()
        self.sync()
        datas, texts = rush.read_cdb(self.workspace())
        self.assertEqual(datas[100][5], 1800)
        self.assertEqual(texts[100][2], "Translator correction.")

    def test_retired_explicit_id_excluded_but_new_alias_not_inferred_duplicate(self):
        rush.write_changed(self.root / "Shared/Rush/config.json", rush.json_bytes({"retired_redirects": {"101": 100}}))
        row = list(self.datas[100]); row[2] = 99; self.datas[100] = tuple(row); self.write_source()
        result = self.sync()
        self.assertEqual(result["packaged_cards"], 1)
        self.assertEqual(set(rush.read_cdb(self.workspace())[0]), {100})

    def test_missing_translation_preserves_only_pending_locale_release_files(self):
        self.sync(["en", "de"])
        before = {p: p.read_bytes() for p in (self.root / "DE").glob("rush-*")}
        self.set_cards([100, 101, 102]); self.seed("en-US", [100, 101, 102])
        result = self.sync(["en", "de"])
        self.assertEqual(result["pending_locales"], {"DE": 1})
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(set(rush.read_json(self.root / "EN/rush-pre-release.json")), {"100", "101", "102"})
        self.assertEqual(set(rush.read_cdb(self.workspace("DE"))[0]), {100, 101, 102})
        report = rush.read_json(self.root / "DE/Rush/report.json")
        self.assertEqual(report["pending_translations"][0]["id"], 102)

    def run_cli(self, *arguments):
        argv = ["rush_prerelease.py", "sync", "--repo-root", str(self.root),
                "--source-root", str(self.source), "--translations-root", str(self.translations),
                "--offline", *arguments]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(rush.sys, "argv", argv), redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                status = rush.main()
            except SystemExit as error:
                status = error.code
        return status, stdout.getvalue(), stderr.getvalue()

    def test_ci_publishes_spanish_independently_while_other_locales_remain_pending(self):
        self.seed("es-ES", [100, 101])
        self.sync(["en", "de", "es"])
        before = {p: p.read_bytes() for p in self.root.glob("*/rush-*") if p.parent.name != "ES"}
        self.edit_text(self.workspace("ES"), 100, "Spanish translator correction.")
        self.set_cards([100, 101, 102]); self.seed("es-ES", [100, 101, 102])
        summary = self.root / "summary.md"
        status, stdout, stderr = self.run_cli("--summary-file", str(summary))
        self.assertEqual(status, 0, stderr)
        result = json.loads(stdout[stdout.index("{"):])
        self.assertEqual(result["status"], "partial")
        self.assertIn("ES", result["updated_locales"])
        self.assertEqual(result["pending_locales"]["DE"], 1)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        published = rush.read_json(self.root / "ES/rush-pre-release.json")
        self.assertEqual(set(published), {"100", "101", "102"})
        self.assertEqual(published["100"]["desc"], "Spanish translator correction.")
        receipt = rush.read_json(self.root / "Shared/Rush/source.json")
        self.assertIn("ES", receipt["built_locales"])
        self.assertNotIn("DE", receipt["built_locales"])
        self.assertEqual(receipt["pending_locales"]["DE"], 1)
        self.assertIn("| ES | 0 | Published |", summary.read_text())
        self.assertIn("| DE | 1 | Pending; publication withheld |", summary.read_text())

    def test_cli_saves_pending_translations_without_reporting_build_failure(self):
        self.set_cards([100, 101, 102])
        status, stdout, stderr = self.run_cli("--lang", "en")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "pending_translations")
        self.assertFalse(self.package().exists())

    def test_ci_still_fails_for_actual_build_errors(self):
        self.sync(); before = self.package().read_bytes()
        (self.source / "script/c100.lua").unlink()
        summary = self.root / "summary.md"
        status, _, stderr = self.run_cli("--lang", "en", "--summary-file", str(summary))
        self.assertEqual(status, 1)
        self.assertIn("Missing effect script", stderr)
        self.assertEqual(before, self.package().read_bytes())
        self.assertFalse(summary.exists())

    def test_ci_reports_successful_complete_batch(self):
        summary = self.root / "summary.md"
        status, _, stderr = self.run_cli("--lang", "en", "--summary-file", str(summary))
        self.assertEqual(status, 0, stderr)
        self.assertTrue(self.package().exists())
        self.assertIn("| EN | 0 | Published |", summary.read_text())
        self.assertIn("| TH | 0 | Published |", summary.read_text())

    def test_unchanged_run_preserves_all_bytes_and_versions(self):
        self.sync(["en", "de"])
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.sync(["en", "de"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_thai_package_is_exactly_english(self):
        self.sync(["th"])
        self.assertEqual(self.package().read_bytes(), self.package("TH").read_bytes())

    def test_thai_waits_for_english_source_review_then_updates_with_english_only_build(self):
        self.sync(["en"])
        before = self.package().read_bytes()
        row = list(self.texts[100]); row[2] += "（修改）"; self.texts[100] = tuple(row); self.write_source()
        result = self.sync(["th"])
        self.assertEqual(result["pending_locales"], {"EN": 1, "TH": 1})
        self.assertEqual(before, self.package().read_bytes())
        self.assertEqual(before, self.package("TH").read_bytes())
        self.edit_text(self.workspace(), 100, "Reviewed English effect.")
        result = self.sync(["en"])
        self.assertEqual(result["pending_locales"], {})
        self.assertEqual(self.package().read_bytes(), self.package("TH").read_bytes())
        self.assertNotEqual(before, self.package().read_bytes())

    def test_changed_source_text_requires_translator_review(self):
        self.sync()
        row = list(self.texts[100]); row[2] += "（修改）"; self.texts[100] = tuple(row); self.write_source()
        self.assertEqual(self.sync()["pending_locales"], {"EN": 1, "TH": 1})
        self.assertIn("source_changed", rush.read_json(self.root / "EN/Rush/report.json")["pending_translations"][0]["fields"])
        self.edit_text(self.workspace(), 100, "Reviewed updated effect.")
        self.sync()

    def test_staggered_locale_reviews_publish_each_ready_locale_and_preserve_pending_baselines(self):
        self.sync(["en", "de"])
        old_german_report = rush.read_json(self.root / "DE/Rush/report.json")
        row = list(self.texts[100]); row[2] += "（修改）"; self.texts[100] = tuple(row); self.write_source()
        self.assertEqual(self.sync(["en", "de"])["pending_locales"], {"EN": 1, "DE": 1, "TH": 1})
        self.edit_text(self.workspace("EN"), 100, "Reviewed English effect.")
        self.assertEqual(self.sync(["en", "de"])["pending_locales"], {"DE": 1})
        report = rush.read_json(self.root / "DE/Rush/report.json")
        self.assertEqual(report["published_source_fields"], old_german_report["published_source_fields"])
        self.assertEqual(report["published_text_fields"], old_german_report["published_text_fields"])
        self.assertEqual(rush.read_json(self.root / "EN/rush-pre-release.json")["100"]["desc"], "Reviewed English effect.")
        self.assertEqual(self.sync(["en", "de"])["pending_locales"], {"DE": 1})
        self.edit_text(self.workspace("DE"), 100, "Reviewed German effect.")
        self.sync(["en", "de"])
        self.assertEqual(rush.read_json(self.root / "EN/Rush/report.json")["pending_translations"], [])
        self.assertEqual(rush.read_json(self.root / "DE/Rush/report.json")["pending_translations"], [])

    def test_later_translated_seed_fills_pending_card_and_preserves_human_edit(self):
        self.sync(["en", "de"])
        self.edit_text(self.workspace("DE"), 100, "Human correction.")
        self.set_cards([100, 101, 102]); self.seed("en-US", [100, 101, 102])
        self.assertEqual(self.sync(["en", "de"])["pending_locales"], {"DE": 1})
        self.seed("de-DE", [100, 101, 102])
        self.sync(["en", "de"])
        _, texts = rush.read_cdb(self.workspace("DE"))
        self.assertEqual(texts[100][2], "Human correction.")
        self.assertEqual(texts[102][2], "Translated effect.")

    def test_unrelated_seed_backfill_does_not_approve_changed_effect(self):
        self.sync()
        row = list(self.texts[100]); row[2] += "（修改）"; self.texts[100] = tuple(row); self.write_source()
        seed_path = self.translations / "en-US/rush_cards.cdb"
        datas, texts = rush.read_cdb(seed_path)
        row = list(texts[100]); row[3] = "A newly supplied prompt"; texts[100] = tuple(row)
        rush.write_changed(seed_path, rush.cdb_bytes(datas, texts))
        self.assertEqual(self.sync()["pending_locales"], {"EN": 1, "TH": 1})
        report = rush.read_json(self.root / "EN/Rush/report.json")
        self.assertIn("source_changed", report["pending_translations"][0]["fields"])

    def test_automatic_sources_fill_new_cards_then_remove_them_in_all_locales(self):
        languages = list(rush.LANGS)
        for lang, (_, locale) in rush.LANGS.items():
            if lang not in ("zh-cn", "th"):
                self.seed(locale, [100, 101])
        self.sync(languages)
        self.set_cards([100, 101, 102])
        # Other locales still arrive via their normal reviewed translated seeds.
        for lang, (_, locale) in rush.LANGS.items():
            if lang not in ("en", "fr", "jp", "th", "zh-cn"):
                self.seed(locale, [100, 101, 102])
        def web_fill(root, source, original, rows, lang, system_ids):
            if lang in ("en", "fr", "jp"):
                rows = dict(rows)
                rows[102] = (102, "Web translated name", "Web translated effect", *([""] * 16))
            return rows, {}
        with patch("rush_source_text.fetch_rows", side_effect=web_fill):
            rush.synchronize(self.root, self.source, self.translations, languages, fetch_source_text=True)
        for folder, _ in rush.LANGS.values():
            self.assertEqual(set(rush.read_cdb(self.workspace(folder))[0]), {100, 101, 102})
        self.set_cards([100])
        rush.synchronize(self.root, self.source, self.translations, languages, fetch_source_text=True)
        for folder, _ in rush.LANGS.values():
            self.assertEqual(set(rush.read_cdb(self.workspace(folder))[0]), {100})
            with zipfile.ZipFile(self.package(folder)) as archive:
                self.assertNotIn("script/c102.lua", archive.namelist())
        self.assertEqual(self.package("TH").read_bytes(), self.package("EN").read_bytes())

    def test_missing_yugipedia_keeps_new_chinese_entry_and_holds_localized_package(self):
        self.sync()
        published = self.package().read_bytes()
        self.set_cards([100, 101, 102])
        row = list(self.texts[102]); row[3] = '是否发动效果？'; self.texts[102] = tuple(row)
        self.write_source()
        rush.write_changed(self.root / 'Shared/Rush/source_mappings.json', rush.json_bytes({
            'cards': {'102': {'set_code': 'RD/KP27-JP060', 'evidence': 'Fixture printed source'}}}))
        with patch('rush_source_text.fetch_yugipedia_cards', return_value={}):
            result = rush.synchronize(self.root, self.source, self.translations, ['en', 'zh-cn'], fetch_source_text=True)
        self.assertEqual(result['pending_locales'], {'EN': 1, 'TH': 1})
        datas, texts = rush.read_cdb(self.workspace())
        self.assertEqual(datas[102], self.datas[102])
        self.assertEqual(texts[102][1:3], self.texts[102][1:3])
        self.assertIn(102, rush.read_json(self.root / 'Shared/Rush/source.json')['card_ids'])
        self.assertEqual(self.package().read_bytes(), published)
        self.assertEqual(rush.read_cdb(self.root / 'EN/Rush/Base Files/rush-pre-release.cdb')[1][102], self.texts[102])
        with zipfile.ZipFile(self.package('ZH-CN')) as archive:
            self.assertEqual(archive.read('script/c102.lua'), (self.source / 'script/c102.lua').read_bytes())

    def test_formatting_does_not_approve_changed_source_after_pending_run(self):
        self.sync()
        # Emulate a last-good package produced by the old inline formatter.
        inline = "【REQUIREMENT】 None. 【EFFECT】 Draw."
        self.edit_text(self.workspace(), 100, inline)
        report_path = self.root / "EN/Rush/report.json"
        report = rush.read_json(report_path)
        report["published_text_fields"]["100"]["desc"] = rush.digest(inline.encode("utf-8"))
        rush.write_changed(report_path, rush.json_bytes(report))
        row = list(self.texts[100]); row[2] += "（修改）"; self.texts[100] = tuple(row); self.write_source()
        for _ in range(2):
            self.assertEqual(self.sync()["pending_locales"], {"EN": 1, "TH": 1})
            self.assertEqual(rush.read_cdb(self.workspace())[1][100][2], inline)
            self.assertIn("source_changed", rush.read_json(report_path)["pending_translations"][0]["fields"])

    def test_mixed_pending_addition_still_retires_removed_rows_from_all_packages(self):
        languages = list(rush.LANGS)
        for lang, (_, locale) in rush.LANGS.items():
            if lang not in ("zh-cn", "th"):
                self.seed(locale, [100, 101])
        self.sync(languages)
        self.set_cards([100, 102])
        for lang, (_, locale) in rush.LANGS.items():
            if lang not in ("fr", "zh-cn", "th"):
                self.seed(locale, [100, 102])
        result = self.sync(languages)
        self.assertEqual(result["pending_locales"], {"FR": 1})
        self.assertEqual(result["retired_ids"], {"FR": [101]})
        for folder, _ in rush.LANGS.values():
            self.assertEqual(set(rush.read_cdb(self.workspace(folder))[0]), {100, 102})
            with zipfile.ZipFile(self.package(folder)) as archive:
                self.assertNotIn("script/c101.lua", archive.namelist())
                self.assertEqual("script/c102.lua" in archive.namelist(), folder != "FR")
                self.assertNotIn("Picture/Rush_Arts/101.jpg", archive.namelist())
            data = rush.read_json(self.root / folder / "rush-pre-release.json")
            self.assertEqual(set(data), {"100"} if folder == "FR" else {"100", "102"})
        before = {p: p.read_bytes() for p in self.root.glob("*/rush-*")}
        self.assertEqual(self.sync(languages)["pending_locales"], {"FR": 1})
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(self.package("TH").read_bytes(), self.package("EN").read_bytes())

    def test_retirement_keeps_old_script_dependency_for_retained_card(self):
        (self.source / "script/c100.lua").write_text('Duel.LoadScript("c101.lua")', encoding="utf-8")
        self.sync()
        self.set_cards([100, 102])
        self.assertEqual(self.sync()["retired_ids"], {"EN": [101], "TH": [101]})
        with zipfile.ZipFile(self.package()) as archive:
            self.assertEqual(archive.read("script/c100.lua"), b'Duel.LoadScript("c101.lua")')
            self.assertIn("script/c101.lua", archive.namelist())
            self.assertNotIn("script/c102.lua", archive.namelist())
        self.assertEqual(set(rush.read_json(self.root / "EN/rush-pre-release.json")), {"100"})

    def test_empty_patch_clears_the_old_package(self):
        self.sync()
        self.set_cards([])
        result = self.sync()
        self.assertEqual(result["packaged_cards"], 0)
        with zipfile.ZipFile(self.package()) as archive:
            self.assertNotIn("script/c100.lua", archive.namelist())
            self.assertNotIn("Picture/Rush_Arts/100.jpg", archive.namelist())

    def test_literal_script_dependencies_are_closed(self):
        (self.source / "script/c100.lua").write_text('Duel.LoadScript("c900.lua")\nc901.operation()', encoding="utf-8")
        (self.source / "script/c900.lua").write_text("-- dependency", encoding="utf-8")
        (self.source / "script/c901.lua").write_text("-- dependency", encoding="utf-8")
        self.sync()
        with zipfile.ZipFile(self.package()) as archive:
            self.assertIn("script/c900.lua", archive.namelist())
            self.assertIn("script/c901.lua", archive.namelist())
            self.assertNotIn("special.lua", archive.namelist())

    def test_missing_required_script_fails_without_replacing_packages(self):
        self.sync(); before = self.package().read_bytes()
        (self.source / "script/c100.lua").unlink()
        with self.assertRaisesRegex(ValueError, "Missing effect script"):
            self.sync()
        self.assertEqual(before, self.package().read_bytes())

    def test_invalid_later_locale_database_prevents_ready_locale_publication(self):
        self.sync(["en", "de"])
        before = {p: p.read_bytes() for p in self.root.glob("*/rush-*")}
        self.edit_text(self.workspace(), 100, "English correction.")
        self.workspace("DE").write_bytes(b"invalid database")
        with self.assertRaises(rush.sqlite3.DatabaseError):
            self.sync(["en", "de"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_invalid_version_prevents_any_package_replacement(self):
        self.sync(["en", "de"])
        self.edit_text(self.workspace(), 100, "English correction.")
        (self.root / "DE/rush-version.txt").write_text("invalid", encoding="utf-8")
        before = {p: p.read_bytes() for p in self.root.glob("*/rush-*")}
        with self.assertRaises(ValueError):
            self.sync(["en", "de"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_invalid_pending_retirement_archive_prevents_ready_locale_publication(self):
        self.sync(["en", "de"])
        self.set_cards([100, 102]); self.seed("en-US", [100, 102])
        self.package("DE").write_bytes(b"invalid archive")
        before = {p: p.read_bytes() for p in self.root.glob("*/rush-*")}
        with self.assertRaises(zipfile.BadZipFile):
            self.sync(["en", "de"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_duplicate_artwork_is_rejected(self):
        (self.root / "Shared/Rush Artworks/100.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        with self.assertRaisesRegex(ValueError, "More than one shared artwork"):
            self.sync()

    def test_dispatcher_fingerprint_covers_artwork_config_and_workspace(self):
        self.sync(); previous = rush.local_inputs_sha256(self.root)
        self.edit_text(self.workspace(), 100, "Another translation.")
        self.assertNotEqual(previous, rush.local_inputs_sha256(self.root))
        previous = rush.local_inputs_sha256(self.root)
        rush.write_changed(self.root / "Shared/Rush/config.json", b"{}")
        self.assertNotEqual(previous, rush.local_inputs_sha256(self.root))

    def test_japanese_translator_prompt_is_not_overwritten(self):
        raw = (100, "原名", "原始", "这个回合不能攻击", *([""] * 15))
        translated = (100, "日本語名", "効果", "このターンは攻撃できない。", *([""] * 15))
        patcher, _, _, _ = rush.prompt_tools()
        pack = patcher.build_translation_pack("jp")
        self.assertEqual(translated, rush.apply_prompt_translations(translated, raw, "jp", pack, {}, {}))

    def test_existing_prompt_patcher_and_shared_mapping_resolve_names(self):
        raw = (100, "原名", "原始", "这个回合不能表侧表示的特殊召唤「原名」", *([""] * 15))
        translated = (100, "Translated card", "Effect", raw[3], *([""] * 15))
        patcher, _, _, _ = rush.prompt_tools()
        result = rush.apply_prompt_translations(translated, raw, "en", patcher.build_translation_pack("en"),
                                               {100: {"str1": "self"}}, {100: translated})
        self.assertNotIn("{CARD}", result[3])
        self.assertIn('"Translated card"', result[3])
        self.assertFalse(rush.CJK.search(result[3]))
        self.assertEqual(translated[:3], result[:3])

    def test_unresolved_shared_mapping_blocks_release(self):
        row = list(self.texts[100]); row[3] = "这个回合不能表侧表示的特殊召唤「原名」"
        self.texts[100] = tuple(row); self.write_source()
        datas, texts = rush.read_cdb(self.translations / "en-US/rush_cards.cdb")
        row = list(texts[100]); row[3] = self.texts[100][3]; texts[100] = tuple(row)
        rush.write_changed(self.translations / "en-US/rush_cards.cdb", rush.cdb_bytes(datas, texts))
        self.assertEqual(self.sync()["pending_locales"], {"EN": 1, "TH": 1})
        self.assertFalse(self.package().exists())

    def test_standard_english_sync_preserves_shared_rush_mappings(self):
        rush.prompt_tools()
        import tcg_autopr
        from shared_mappings import save_locale_mappings_csv, load_locale_mappings_csv
        path = self.root / "Shared/Mappings.csv"
        save_locale_mappings_csv(path, {120309048: {"id": "120309048", "name": "Rush card", "str1": "self"},
                                      100268021: {"id": "100268021", "name": "Old standard card"}})
        tcg_autopr.CONFIG = tcg_autopr.LANG_CONFIGS["en"]
        tcg_autopr.save_mappings_csv(path, {101000001: {"id": "101000001", "name": "New standard card"}})
        rows = load_locale_mappings_csv(path)
        self.assertEqual(set(rows), {120309048, 101000001})
        self.assertEqual(rows[120309048]["str1"], "self")



    def test_korean_manual_translation_survives_gameplay_update_and_retirement(self):
        self.seed("ko-KR", [100, 101])
        self.sync(["kr"])
        self.edit_text(self.workspace("KR"), 100, "번역자가 수정한 효과.")
        self.set_cards([100, 102])
        row = list(self.datas[100]); row[5] = 1900; self.datas[100] = tuple(row); self.write_source()
        with patch("card_text_autofill.request_url", side_effect=AssertionError("Korean web fetch")) as request:
            result = rush.synchronize(self.root, self.source, self.translations, ["kr"], fetch_source_text=True)
            self.assertEqual(result["pending_locales"], {"KR": 1})
            request.assert_not_called()
        datas, texts = rush.read_cdb(self.workspace("KR"))
        self.assertEqual(set(datas), {100, 102})
        self.assertEqual(datas[100][5], 1900)
        self.assertEqual(texts[100][2], "번역자가 수정한 효과.")
        self.assertEqual(texts[102], self.texts[102])
        report = rush.read_json(self.root / "KR/Rush/source_text.json")
        self.assertEqual(report["mode"], "manual")
        with zipfile.ZipFile(self.package("KR")) as archive:
            self.assertNotIn("script/c101.lua", archive.namelist())
            self.assertNotIn("script/c102.lua", archive.namelist())
        # The translator can later supply a reviewed Korean seed, with no web fetch.
        self.seed("ko-KR", [100, 102])
        rush.synchronize(self.root, self.source, self.translations, ["kr"], fetch_source_text=True)
        self.assertEqual(rush.read_cdb(self.workspace("KR"))[1][100][2], "번역자가 수정한 효과.")


if __name__ == "__main__":
    unittest.main()
