import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
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

    def test_missing_translation_preserves_all_last_good_release_files(self):
        self.sync(["en", "de"])
        before = {p: p.read_bytes() for p in self.root.glob("*/rush-*")}
        self.set_cards([100, 101, 102]); self.seed("en-US", [100, 101, 102])
        with self.assertRaisesRegex(ValueError, "DE: 1 cards"):
            self.sync(["en", "de"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(set(rush.read_cdb(self.workspace("DE"))[0]), {100, 101, 102})
        report = rush.read_json(self.root / "DE/Rush/report.json")
        self.assertEqual(report["pending_translations"][0]["id"], 102)

    def test_unchanged_run_preserves_all_bytes_and_versions(self):
        self.sync(["en", "de"])
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.sync(["en", "de"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_thai_package_is_exactly_english(self):
        self.sync(["th"])
        self.assertEqual(self.package().read_bytes(), self.package("TH").read_bytes())

    def test_changed_source_text_requires_translator_review(self):
        self.sync()
        row = list(self.texts[100]); row[2] += "（修改）"; self.texts[100] = tuple(row); self.write_source()
        with self.assertRaisesRegex(ValueError, "need translation/review"):
            self.sync()
        self.assertIn("source_changed", rush.read_json(self.root / "EN/Rush/report.json")["pending_translations"][0]["fields"])
        self.edit_text(self.workspace(), 100, "Reviewed updated effect.")
        self.sync()

    def test_staggered_locale_reviews_survive_failed_batches(self):
        self.sync(["en", "de"])
        row = list(self.texts[100]); row[2] += "（修改）"; self.texts[100] = tuple(row); self.write_source()
        with self.assertRaisesRegex(ValueError, "EN: 1 cards.*DE: 1 cards"):
            self.sync(["en", "de"])
        self.edit_text(self.workspace("EN"), 100, "Reviewed English effect.")
        with self.assertRaisesRegex(ValueError, "DE: 1 cards"):
            self.sync(["en", "de"])
        self.edit_text(self.workspace("DE"), 100, "Reviewed German effect.")
        self.sync(["en", "de"])
        self.assertEqual(rush.read_json(self.root / "EN/Rush/report.json")["pending_translations"], [])
        self.assertEqual(rush.read_json(self.root / "DE/Rush/report.json")["pending_translations"], [])

    def test_later_translated_seed_fills_pending_card_and_preserves_human_edit(self):
        self.sync(["en", "de"])
        self.edit_text(self.workspace("DE"), 100, "Human correction.")
        self.set_cards([100, 101, 102]); self.seed("en-US", [100, 101, 102])
        with self.assertRaisesRegex(ValueError, "DE: 1 cards"):
            self.sync(["en", "de"])
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
        with self.assertRaisesRegex(ValueError, "need translation/review"):
            self.sync()
        report = rush.read_json(self.root / "EN/Rush/report.json")
        self.assertIn("source_changed", report["pending_translations"][0]["fields"])

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
        with self.assertRaisesRegex(ValueError, "need translation/review"):
            self.sync()
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


if __name__ == "__main__":
    unittest.main()
