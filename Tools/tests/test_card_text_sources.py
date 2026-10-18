import json
import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "Tools"), str(ROOT / ".github/scripts")]
import card_text_autofill as source
import rush_source_text as rush_source
import rush_prerelease as rush


class SourceTextTests(unittest.TestCase):
    def test_standard_printed_code_exceptions_override_pack_guess_and_cover_missing_entry(self):
        entries = {card_id: source.PackEntry(card_id, "CH03", "CH03.ydk")
                   for card_id in (100274001, 100274002, 100274004)}
        entries[101307050] = source.PackEntry(101307050, "IMPH", "IMPH.ydk")
        ids = set(entries) | {101307070}
        self.assertEqual(source.build_id_to_set_code(entries, ids, "fr"), {
            100274001: "CH03-JPP01", 100274002: "CH03-JPP02", 100274004: "CH03-JPP04",
            101307050: "IMPH-JP050", 101307070: "IMPH-JP070"})
        self.assertNotIn(101307070, source.build_id_to_set_code(entries, set(entries), "fr"))

    def test_upstream_ranges_and_short_promo_suffix_are_exact(self):
        text = "- Set (RD/CA01-JP): `120309XXX`\n- Promo (RD/S241-JP): `1203021XX`"
        result, issues = rush_source.identity_map(text, {120309029, 120302105, 120302205}, {})
        self.assertEqual(result, {120309029: "RD/CA01-JP029", 120302105: "RD/S241-JP005"})
        self.assertIn(120302205, issues)

    def test_conflicting_ranges_require_reviewed_override(self):
        text = "(RD/AA01-JP): `120309XXX`\n(RD/BB01-JP): `120309XXX`"
        result, issues = rush_source.identity_map(text, {120309029}, {})
        self.assertFalse(result)
        self.assertIn("Ambiguous", issues[120309029])
        result, issues = rush_source.identity_map(text, {120309029}, {
            "120309029": {"set_code": "RD/AA01-JP029", "evidence": "Reviewed printed card"}})
        self.assertEqual(result[120309029], "RD/AA01-JP029")
        self.assertFalse(issues)

    def test_override_without_evidence_is_not_trusted(self):
        result, issues = rush_source.identity_map("", {120309029}, {
            "120309029": {"set_code": "RD/AA01-JP029"}})
        self.assertFalse(result)
        self.assertIn("evidence", issues[120309029])
        result, issues = rush_source.identity_map("", {120309029}, {
            "120309029": {"set_code": 123, "evidence": "wrong type"}})
        self.assertFalse(result)
        self.assertIn(120309029, issues)

    def test_rush_fusion_and_continuous_condition_are_preserved(self):
        params = {"materials": '\"Dragon\" + 1 monster', "condition": "Always treated as Dragon.",
                  "requirement": "None", "text": "Protect your cards.", "effect_types": "Continuous"}
        card = source.format_yugipedia_card("Dragon (Rush Duel)", params, "en", rush=True)
        self.assertEqual(card.name, "Dragon")
        self.assertEqual(card.desc, '\"Dragon\" + 1 monster\nAlways treated as Dragon.\n[REQUIREMENT]\nNone\n[CONTINUOUS EFFECT]\nProtect your cards.')

    def test_choices_are_separate_rows_without_extra_blank_lines(self):
        card = source.format_yugipedia_card("Choice", {
            "requirement": "Send a card.", "text": "●Draw. ●Recover.", "effect_types": "Multi-Choice"}, "en", rush=True)
        self.assertIn("[MULTI-CHOICE EFFECT]\n●Draw.\n●Recover.", card.desc)
        self.assertNotIn("\n\n", card.desc)

    def test_french_requires_complete_french_materials_and_requirement(self):
        params = {"fr_name": "Carte", "fr_text": "Piochez.", "materials": "Dragon", "requirement": "Send."}
        self.assertIsNone(source.format_yugipedia_card("Card", params, "fr", rush=True))
        params.update(fr_materials="Dragon FR", fr_requirement="Envoyez.")
        card = source.format_yugipedia_card("Card", params, "fr", rush=True)
        self.assertEqual(card.desc, "Dragon FR\n[CONDITION]\nEnvoyez.\n[EFFET]\nPiochez.")

    def test_missing_french_is_not_replaced_by_english(self):
        self.assertIsNone(source.format_yugipedia_card("Card", {"text": "Draw."}, "fr", rush=True))

    def test_normal_french_pendulum_uses_localized_sections(self):
        card = source.format_yugipedia_card("Card", {"fr_name": "Carte", "fr_text": "Monstre.",
            "fr_pendulum_effect": "Pendule.", "pendulum_scale": "4"}, "fr")
        self.assertIn("Échelle Pendule = 4", card.desc)
        self.assertIn("[ Effet de Monstre ]\nMonstre.", card.desc)

    def test_maximum_stats_conditions_and_literal_side_name_survive(self):
        params = {"name": "Machine [L]", "maximum_atk": "3500", "condition": 'Summon with "Machine [R]".',
                  "effect_types": "Continuous", "text": "Prevent destruction."}
        card = source.format_yugipedia_card("Machine (L)", params, "en", rush=True)
        self.assertEqual(card.name, "Machine [L]")
        self.assertEqual(card.desc, 'Maximum ATK 3500\nSummon with "Machine [R]".\n[CONTINUOUS EFFECT]\nPrevent destruction.')

    def test_japanese_maximum_row_is_included(self):
        parser = source.OcgCardListParser()
        parser.feed('<td class="card-number-r">RD/MAX1-JP002</td><td class="e-mon">カード</td>'
                    '<td class="list-head">MAXIMUM ATK</td><td class="card-text" colspan="8">3500</td>'
                    '<td class="card-text" colspan="9">【永続効果】防ぐ。</td>')
        self.assertTrue(parser.cards["RD/MAX1-JP002"].desc.startswith("Maximum ATK 3500\n"))

    def test_incomplete_french_pendulum_is_not_imported(self):
        self.assertIsNone(source.format_yugipedia_card("Card", {"fr_name": "Carte", "fr_text": "Monstre.",
            "pendulum_effect": "English pendulum."}, "fr"))

    def test_card_brackets_are_not_reformatted_as_headers(self):
        text = '"Card [L]" + "Card [R]"\n[ REQUIREMENT ] Pay [the number of cards] LP.\n［EFFECT］ ●Draw. ●Recover.'
        value = source.encode_rush_description(text)
        self.assertIn('"Card [L]" + "Card [R]"', value)
        self.assertIn('[the number of cards]', value)
        self.assertIn('[REQUIREMENT]\t\nPay', value)
        self.assertIn('[EFFECT]\t\n●Draw.\t\n●Recover.', value)
        self.assertEqual(value, source.encode_rush_description(value))
        self.assertEqual(source.encode_rush_description("  "), "")
        self.assertEqual(source.encode_rush_description("[条件]なし", "jp"), "【条件】\t\nなし")

    def test_lettered_print_suffix_has_two_numeric_digits(self):
        result, issues = rush_source.identity_map("(RD/5THS-JPA): `120286XXX`", {120286001}, {})
        self.assertEqual(result[120286001], "RD/5THS-JPA01")
        self.assertFalse(issues)

    def test_unknown_templates_reject_incomplete_import(self):
        self.assertIsNone(source.format_yugipedia_card("Card", {"text": "{{unknown|some effect}}"}, "en"))

    def test_all_redirect_aliases_resolve_and_printed_codes_verified(self):
        data = {"query": {"redirects": [
            {"from": "RD/AA01-JP001", "to": "Dragon (Rush Duel)"},
            {"from": "RD/AA01-JP002", "to": "Dragon (Rush Duel)"},
            {"from": "RD/AA01-JP003", "to": "Dragon (Rush Duel)"}],
            "pages": [{"title": "Dragon (Rush Duel)", "revisions": [{"content":
              "{{CardTable2\n| text = Draw.\n| jp_sets = \nRD/AA01-JP001; Set; Rare\nRD/AA01-JP002; Set; Rare\n}}"}]}]}}
        with patch.object(source, "request_url", return_value=json.dumps(data)):
            cards = source.fetch_yugipedia_cards(["RD/AA01-JP001", "RD/AA01-JP002", "RD/AA01-JP003"], lambda _: None, rush=True)
        self.assertEqual(set(cards), {"RD/AA01-JP001", "RD/AA01-JP002"})

    def test_existing_title_fallback_still_verifies_printed_code(self):
        data = {"query": {"pages": [{"title": "Dragon", "revisions": [{"content":
            "{{CardTable2\n| text = Draw.\n| jp_sets = AA01-JP001; Set; Rare\n}}"}]}]}}
        with patch.object(source, "request_url", return_value=json.dumps(data)):
            good = source.fetch_yugipedia_by_existing_titles({1: "Dragon"}, [1], "en", lambda _: None, {1: "AA01-JP001"})
            wrong = source.fetch_yugipedia_by_existing_titles({2: "Dragon"}, [2], "en", lambda _: None, {2: "AA01-JP002"})
        self.assertIn(1, good)
        self.assertFalse(wrong)

    def test_ocg_ruby_badges_and_illustration_count_are_not_card_names(self):
        parser = source.OcgCardListParser()
        parser.feed('<td class="card-number-r">RD/AA01-JP001</td>'
                    '<td class="e-mon"><div class="card-ruby">よみ</div>カード（イラスト2種）<span class="limit-icon">LEGEND</span></td>'
                    '<td class="card-text" colspan="9">【条件】なし<br>【効果】引く。</td>')
        card = parser.cards["RD/AA01-JP001"]
        self.assertEqual(card.name, "カード")
        self.assertEqual(card.desc, "【条件】なし\n【効果】引く。")

    def test_manual_fields_preserved_but_missing_description_filled(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cards.cdb"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE texts(id integer, name text, desc text)")
                db.execute("INSERT INTO texts VALUES(1, 'Human name', '原文')")
                db.commit()
            source.update_cdb_texts(path, {1: source.CardText("Fetched name", "Fetched effect", "test")},
                                   lambda _: None, {1: ("原名", "原文")}, "fr")
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT name,desc FROM texts").fetchone(), ("Human name", "Fetched effect"))

    def test_japanese_human_edit_is_not_treated_as_chinese(self):
        self.assertFalse(source.needs_source_text("カードを引く。", "抽卡。", "jp"))

    def test_rush_source_fill_keeps_inventory_and_fields_owned_by_translator(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sets.md").write_text("(RD/AA01-JP): `120309XXX`", encoding="utf-8")
            raw = {120309001: (120309001, "原名", "原文", *([""] * 16))}
            rows = {120309001: (120309001, "Human name", "原文", *([""] * 16))}
            fetched = {"RD/AA01-JP001": source.CardText("Web name", "Web effect", "URL")}
            with patch.object(rush_source, "fetch_yugipedia_cards", return_value=fetched):
                result, report = rush_source.fetch_rows(root, root, raw, rows, "fr", set(), lambda _: None)
            self.assertEqual(set(result), set(raw))
            self.assertEqual(result[120309001][1:3], ("Human name", "Web effect"))
            self.assertEqual(report["matches"]["120309001"]["fields"], ["desc"])

    def test_missing_rush_source_stays_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = (120309001, "原名", "原文", *([""] * 16))
            with patch.object(rush_source, "fetch_yugipedia_cards", return_value={}):
                result, report = rush_source.fetch_rows(root, root, {row[0]: row}, {row[0]: row}, "fr", set(), lambda _: None)
            self.assertEqual(result[row[0]], row)
            self.assertIn("No upstream", report["unresolved"][0]["reason"])



    def test_korean_fetch_entry_points_never_query_the_web(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = (120309001, "번역자가 수정한 이름", "번역자가 수정한 효과", *([""] * 16))
            rows = {row[0]: row}
            with patch.object(source, "request_url", side_effect=AssertionError("Korean web fetch")) as request:
                self.assertEqual(source.fetch_yugipedia_cards(["RD/AA01-JP001"], lambda _: None, lang="kr", rush=True), {})
                self.assertEqual(source.fetch_yugipedia_cards(["AA01-JP001"], lambda _: None, lang="kr"), {})
                with patch.object(rush_source, "fetch_yugipedia_cards") as wiki, patch.object(rush_source, "fetch_ocg_cards") as ocg:
                    result, report = rush_source.fetch_rows(root, root, rows, rows, "kr", set(), lambda _: None)
                wiki.assert_not_called()
                ocg.assert_not_called()
                self.assertIs(result, rows)
                self.assertEqual(report["mode"], "manual")
                self.assertFalse(report["automatic_fetch"])
                # No input CDB/YPK is required: the manual mode exits before reading or changing card text.
                self.assertEqual(source.autofill_cdb_from_pack_sources(root / "cards.cdb", root / "upstream.ypk", "kr", lambda _: None), 0)
                self.assertFalse((root / "cards.cdb").exists())
                self.assertEqual(json.loads((root / "Reports/source_text.json").read_text())["mode"], "manual")
                request.assert_not_called()

    def test_standard_korean_reports_manual_mode_and_preserves_translator_text(self):
        import ocg_autopr
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "cards.cdb"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE texts(id integer, name text, desc text)")
                db.execute("INSERT INTO texts VALUES(1, ?, ?)", ("번역된 이름", "번역자가 수정한 효과"))
                db.commit()
            before = path.read_bytes()
            with patch.object(ocg_autopr, "CONFIG", ocg_autopr.LANG_CONFIGS["kr"], create=True), patch.object(source, "request_url") as request:
                ocg_autopr.autofill_source_text(path, root / "upstream.ypk")
            request.assert_not_called()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(json.loads((root / "Reports/source_text.json").read_text())["mode"], "manual")

    def test_french_report_distinguishes_missing_fields_from_network_failure(self):
        data = {"query": {"pages": [{"title": "AA01-JP001", "revisions": [{"content":
            "{{CardTable2\n| text = Draw.\n| jp_sets = AA01-JP001; Set; Rare\n}}"}]}]}}
        diagnostics = {}
        with patch.object(source, "request_url", return_value=json.dumps(data)):
            self.assertEqual(source.fetch_yugipedia_cards(["AA01-JP001"], lambda _: None, lang="fr", diagnostics=diagnostics), {})
        self.assertEqual(diagnostics["AA01-JP001"]["reason"], "Localized fields are missing")
        self.assertEqual(diagnostics["AA01-JP001"]["missing_fields"], ["fr_name", "fr_text"])
        with patch.object(source, "request_url", side_effect=OSError("offline")):
            source.fetch_yugipedia_cards(["AA01-JP001"], lambda _: None, lang="fr", diagnostics=diagnostics)
        self.assertEqual(diagnostics["AA01-JP001"]["reason"], "Source request failed")

    def test_normal_french_report_includes_unmapped_card_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "cards.cdb"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE texts(id integer, name text, desc text)")
                db.executemany("INSERT INTO texts VALUES(?, ?, ?)", [(1, "原名", "原文"), (2, "原名二", "原文二")])
                db.commit()
            data = {"query": {"pages": [{"title": "AA01-JP001", "revisions": [{"content":
                "{{CardTable2\n| text = Draw.\n| jp_sets = AA01-JP001; Set; Rare\n}}"}]}]}}
            with patch.object(source, "load_pack_entries_from_ypk", return_value={1: source.PackEntry(1, "AA01", "AA01.ydk")}), patch.object(source, "original_texts_from_ypk", return_value={}), patch.object(source, "request_url", return_value=json.dumps(data)):
                source.autofill_cdb_from_pack_sources(path, root / "unused.ypk", "fr", lambda _: None)
            report = json.loads((root / "Reports/source_text.json").read_text(encoding="utf-8"))
            self.assertEqual(report["candidates"], 1)
            self.assertEqual(report["matched"], 0)
            self.assertEqual(report["unmatched_ids"], [1, 2])
            self.assertEqual(report["unresolved"][0]["missing_fields"], ["fr_name", "fr_text"])
            self.assertEqual(report["unresolved"][1]["reason"], "No printed set code mapping")



    def test_title_fallback_keeps_verified_missing_translation_diagnostics(self):
        verified = {"reason": "Localized fields are missing", "missing_fields": ["fr_text"],
                    "source": "https://yugipedia.com/wiki/Verified_Card"}
        diagnostics = {"AA01-JP001": verified}
        missing = {"query": {"pages": [{"title": "Nom humain", "missing": True}]}}
        with patch.object(source, "request_url", return_value=json.dumps(missing)):
            source.fetch_yugipedia_by_existing_titles({1: "Nom humain"}, [1], "fr", lambda _: None,
                                                      {1: "AA01-JP001"}, diagnostics)
        self.assertEqual(diagnostics, {"AA01-JP001": verified})
        with patch.object(source, "request_url", side_effect=OSError("offline")):
            source.fetch_yugipedia_by_existing_titles({1: "Nom humain"}, [1], "fr", lambda _: None,
                                                      {1: "AA01-JP001"}, diagnostics)
        self.assertEqual(diagnostics, {"AA01-JP001": verified})
        found = {"query": {"pages": [{"title": "Nom humain", "revisions": [{"content":
            "{{CardTable2\n| fr_name = Nom humain\n| fr_text = Piochez.\n| jp_sets = AA01-JP001; Set; Rare\n}}"}]}]}}
        with patch.object(source, "request_url", return_value=json.dumps(found)):
            cards = source.fetch_yugipedia_by_existing_titles({1: "Nom humain"}, [1], "fr", lambda _: None,
                                                             {1: "AA01-JP001"}, diagnostics)
        self.assertIn(1, cards)
        self.assertEqual(diagnostics, {})


if __name__ == "__main__":
    unittest.main()
