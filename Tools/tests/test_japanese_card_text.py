import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / ".github/scripts"), str(ROOT / "Tools")]
import card_text_autofill as source
import ocg_autopr as ocg
import PromptPatcher as prompts


class JapaneseCardTextTests(unittest.TestCase):
    def test_report_recognizes_formatted_japanese_but_keeps_chinese_and_unbound_names(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            columns = ", ".join(f"str{i} TEXT" for i in range(1, 17))
            conn.execute(f"CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT, desc TEXT, {columns})")
            values = {
                1: "攻撃力が４０００になる",
                2: "「日本語の名前」の効果を適用して発動する。",
                3: "是否发动「日本語の名前」的效果？",
                4: "{CARD}の効果を適用して発動する。",
                5: "{ＣＡＲＤ}の効果を適用して発動する。",
            }
            conn.executemany("INSERT INTO texts(id,name,desc,str1) VALUES (?, 'カード', '', ?)", values.items())
            report = prompts.build_remaining_cn_report(conn, "JP/Workspace/test-release.cdb", Path("JP/Reports/remaining_cn.json"))
            self.assertEqual([card["id"] for card in report["cards"]], [3, 4, 5])
            self.assertEqual([row[0] for row in prompts.collect_cn_cells(conn, "jp")], [3, 4, 5])

    def test_jp_layout_does_not_protect_untranslated_source(self):
        ocg.configure("jp")
        original = "①：自己抽1张卡。\r\n②：攻击力上升500。"
        formatted = ocg.normalize_jp_cdb_value("desc", original)
        self.assertNotEqual(formatted, original)
        self.assertTrue(source.needs_source_text(formatted, original, "jp"))
        self.assertTrue(source.needs_source_text(formatted.replace("\r\n", "\n"), original, "jp"))

    def test_old_chinese_effect_can_be_repaired_without_overwriting_japanese(self):
        self.assertTrue(source.needs_source_text("这张卡的攻击力上升５００。", "这张卡的攻击力上升600。", "jp"))
        self.assertTrue(source.needs_source_text("這張卡的攻擊力上升５００。", "新效果", "jp"))
        for translated in ("このカードの攻撃力は５００アップする。", "融合", "無限泡影", "自分は１枚ドローする。"):
            with self.subTest(translated=translated):
                self.assertFalse(source.needs_source_text(translated, "这张卡的攻击力上升600。", "jp"))

    def test_japanese_name_and_gameplay_survive_effect_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cards.cdb"
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT, desc TEXT)")
                conn.execute("CREATE TABLE datas (id INTEGER PRIMARY KEY, atk INTEGER)")
                conn.execute("INSERT INTO texts VALUES (1, ?, ?)", ("手動のカード名", "①：自己抽１张卡。"))
                conn.execute("INSERT INTO datas VALUES (1, 2500)")
            changed = source.update_cdb_texts(path, {1: source.CardText("別の名前", "①：自分は１枚ドローする。", "verified")},
                                              lambda _: None, {1: ("卡名", "①：自己抽1张卡。")}, "jp")
            self.assertEqual(changed, 1)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT name, desc FROM texts").fetchone(), ("手動のカード名", "①：自分は１枚ドローする。"))
                self.assertEqual(conn.execute("SELECT * FROM datas").fetchall(), [(1, 2500)])

    def test_sync_resolves_self_prompt_after_japanese_name_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "JP" / "Workspace"
            work.mkdir(parents=True)
            upstream = root / "upstream.cdb"
            with closing(sqlite3.connect(upstream)) as conn, conn:
                columns = ", ".join(f"str{i} TEXT" for i in range(1, 17))
                conn.execute(f"CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT, desc TEXT, {columns})")
                conn.execute("CREATE TABLE datas (id INTEGER PRIMARY KEY, atk INTEGER)")
                conn.execute("INSERT INTO texts(id,name,desc,str1) VALUES (101307005, '原名', '这张卡的效果', '{CARD}の効果を適用して発動する。')")
                conn.execute("INSERT INTO datas VALUES (101307005, 1500)")
            package = root / "upstream.ypk"
            with zipfile.ZipFile(package, "w") as archive:
                archive.write(upstream, "test-release.cdb")
                archive.writestr("test-strings.conf", "")
                archive.writestr("pack/IMPH.ydk", "101307005\n")
            mappings = root / "Mappings.csv"
            mappings.write_text("id,name,str1\n101307005,Name,self\n", encoding="utf-8")

            def download_fixture(url, target):
                target.write_bytes((package if url == "fixture:package" else upstream).read_bytes())

            fetched = {"IMPH-JP005": source.CardText("日本語の名前", "自分は１枚ドローする。", "verified")}
            with patch.multiple(ocg, create=True, CONFIG=ocg.LANG_CONFIGS["jp"],
                                OUTPUT_DIR=root / "JP", BASE_DIR=root / "JP" / "Base Files", WORK_DIR=work,
                                TOOLS_DIR=ROOT / "Tools", MAPPINGS_PATH=mappings,
                                YPK_URL="fixture:package", CARDS_CDB_URL="fixture:cards"), \
                 patch.object(ocg, "download_file", side_effect=download_fixture), \
                 patch.object(source, "fetch_ocg_cards", return_value=fetched), \
                 patch.object(ocg, "log"):
                ocg.workspace_sync()
            with closing(sqlite3.connect(work / "test-release.cdb")) as conn:
                self.assertEqual(conn.execute("SELECT name, str1 FROM texts").fetchone(),
                                 ("日本語の名前", "「日本語の名前」の効果を適用して発動する。"))
            report = json.loads((work / "Reports" / "remaining_cn.json").read_text(encoding="utf-8"))
            self.assertTrue(all("{CARD}" not in item["string"] for item in report["unique_strings"]))


if __name__ == "__main__":
    unittest.main()
