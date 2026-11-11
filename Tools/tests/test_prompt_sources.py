import csv
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prompt_sources import export_prompt_sources


class PromptSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.inputs = {name: root / name for name in
                       ("standard_zh", "standard_en", "rush_zh", "rush_en")}
        for directory in self.inputs.values():
            directory.mkdir()
        self.output = root / "exports"
        self.columns = ["id", "name", "desc", *[f"str{i}" for i in range(1, 17)]]
        self.make_db("standard_zh", "cards.cdb", [(10, '名字,"甲"', "效果\r\n第二行", "取出")])
        self.make_db("standard_en", "cards.cdb", [(10, "Name", "", "", "Second slot")])
        self.make_db("rush_zh", "RD Standard.cdb", [(10, "旧名", "", "", "旧提示")])
        self.make_db("rush_zh", "RD Alternate.cdb", [(20, "异画", "", "选择")])
        self.make_db("rush_zh", "RD Patch.cdb", [(10, "新名", "", "盖放「卡」")])
        self.make_db("rush_en", "rush_cards.cdb",
                     [(10, "New", "", 'Set "Card"', "Retired option"), (30, "Other", "", "Draw")])

    def make_db(self, directory, name, rows):
        filename = self.inputs[directory] / name
        with closing(sqlite3.connect(filename)) as db:
            fields = ["id INTEGER PRIMARY KEY", *[f"{col} TEXT" for col in self.columns[1:]]]
            db.execute(f"CREATE TABLE texts ({','.join(fields)})")
            db.executemany(f"INSERT INTO texts VALUES ({','.join('?' for _ in fields)})",
                           [(*row, *([None] * (len(fields) - len(row)))) for row in rows])
            db.commit()

    def export(self):
        return export_prompt_sources(self.output, **self.inputs)

    def read_rows(self, filename):
        with (self.output / filename).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_text_snapshots_and_slot_evidence(self):
        counts = self.export()
        self.assertEqual(counts, {"texts.csv": 1, "texts_en.csv": 1, "standard_prompts.csv": 2,
                                  "rush_texts.csv": 2, "rush_texts_en.csv": 2, "rush_prompts.csv": 4})
        original, = self.read_rows("texts.csv")
        self.assertEqual(list(original), self.columns)
        self.assertEqual((original["id"], original["name"], original["desc"], original["str16"]),
                         ("10", '名字,"甲"', "效果\r\n第二行", ""))
        standard = self.read_rows("standard_prompts.csv")
        self.assertEqual([(r["slot"], r["status"]) for r in standard],
                         [("str1", "missing_en"), ("str2", "missing_zh")])
        rush = self.read_rows("rush_prompts.csv")
        self.assertEqual([(r["id"], r["slot"], r["status"]) for r in rush],
                         [("10", "str1", "paired"), ("10", "str2", "missing_zh"),
                          ("20", "str1", "missing_en"), ("30", "str1", "missing_zh")])
        self.assertEqual((rush[0]["zh_name"], rush[0]["zh_prompt"], rush[0]["en_prompt"],
                          rush[0]["catalog_key"]), ("新名", "盖放「卡」", 'Set "Card"', "盖放{CARD}"))
        self.assertEqual(rush[1]["zh_prompt"], "")
        self.assertEqual(rush[-1]["catalog_key"], "")
        snapshot = {name: (self.output / name).read_bytes() for name in counts}
        self.assertEqual(self.export(), counts)
        self.assertEqual(snapshot, {name: (self.output / name).read_bytes() for name in counts})

    def test_invalid_source_preserves_previous_exports(self):
        counts = self.export()
        before = {name: (self.output / name).read_bytes() for name in counts}
        with closing(sqlite3.connect(self.inputs["rush_en"] / "rush_cards.cdb")) as db:
            db.execute("ALTER TABLE texts DROP COLUMN str16")
            db.commit()
        with self.assertRaises((ValueError, sqlite3.Error)):
            self.export()
        self.assertEqual(before, {name: (self.output / name).read_bytes() for name in counts})

    def test_locked_report_rolls_back_other_exports(self):
        counts = self.export()
        before = {name: (self.output / name).read_bytes() for name in counts}
        with closing(sqlite3.connect(self.inputs["standard_zh"] / "cards.cdb")) as db:
            db.execute("UPDATE texts SET name = '更新'")
            db.commit()
        replace = Path.replace

        def locked(path, target):
            if target == self.output / "rush_prompts.csv":
                raise PermissionError("Report is open")
            return replace(path, target)

        with patch.object(Path, "replace", locked), self.assertRaises(PermissionError):
            self.export()
        self.assertEqual(before, {name: (self.output / name).read_bytes() for name in counts})

    def test_uncheckpointed_wal_is_included_without_changing_source(self):
        english = self.inputs["standard_en"] / "cards.cdb"
        shipped = english.parent / "snapshot"
        shipped.mkdir()
        with closing(sqlite3.connect(english)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            db.execute("UPDATE texts SET str1 = 'Committed in WAL'")
            db.commit()
            for path in (english, english.with_name("cards.cdb-wal")):
                shutil.copyfile(path, shipped / path.name)
        before = {p.name: p.read_bytes() for p in shipped.iterdir()}
        self.inputs["standard_en"] = shipped
        self.export()
        self.assertEqual(self.read_rows("texts_en.csv")[0]["str1"], "Committed in WAL")
        self.assertEqual(before, {p.name: p.read_bytes() for p in shipped.iterdir()})


if __name__ == "__main__":
    unittest.main()
