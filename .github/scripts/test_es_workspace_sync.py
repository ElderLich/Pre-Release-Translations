"""A new CN source must reach both ES workspace files without losing card translations."""
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import es_autopr as es


class SpanishWorkspaceSyncTests(unittest.TestCase):
    def test_new_cn_cards_and_string_keys_reach_workspace_while_spanish_text_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "ES/Workspace"
            work.mkdir(parents=True)

            def database(path, rows):
                with closing(sqlite3.connect(path)) as db:
                    db.execute("CREATE TABLE datas (id INTEGER PRIMARY KEY, atk INTEGER)")
                    db.execute("CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT, desc TEXT, str1 TEXT)")
                    db.executemany("INSERT INTO datas VALUES (?, ?)", [(row[0], row[1]) for row in rows])
                    db.executemany("INSERT INTO texts VALUES (?, ?, ?, ?)", [(row[0], *row[2:]) for row in rows])
                    db.commit()

            database(work / "test-release.cdb", [(1, 100, "Nombre español", "Efecto traducido", "Elegir"),
                                                  (2, 200, "Retirada", "Ya publicada", "")])
            (work / "test-strings.conf").write_text("!setname 0x1 Nombre español\n!setname 0x2 Retirada\n", encoding="utf-8")
            source = root / "source.cdb"
            database(source, [(1, 150, "中文", "中文效果", "提示"), (3, 300, "新卡", "新效果", "新提示")])
            package = root / "source.ypk"
            with zipfile.ZipFile(package, "w") as archive:
                archive.write(source, "test-release.cdb")
                archive.writestr("test-strings.conf", "!setname 0x1 中文\n!setname 0x3 新系列\n")
            cards = root / "cards.cdb"
            database(cards, [])

            def download(url, target):
                shutil.copyfile(package if url == es.YPK_URL else cards, target)

            with patch.object(es, "ES_DIR", root / "ES"), \
                 patch.object(es, "WORK_DIR", work), patch.object(es, "BASE_DIR", root / "ES/Base Files"), \
                 patch.object(es, "download_file", side_effect=download), \
                 patch.object(es, "run_prompt_patcher") as patcher:
                es.workspace_sync()
            patcher.assert_called_once()
            with closing(sqlite3.connect(work / "test-release.cdb")) as db:
                self.assertEqual(db.execute("SELECT id, name, desc FROM texts ORDER BY id").fetchall(),
                                 [(1, "Nombre español", "Efecto traducido"), (3, "新卡", "新效果")])
                self.assertEqual(db.execute("SELECT * FROM datas ORDER BY id").fetchall(), [(1, 150), (3, 300)])
                self.assertEqual(db.execute("SELECT str1 FROM texts WHERE id=1").fetchone()[0], "Elegir")
            self.assertEqual((work / "test-strings.conf").read_text(encoding="utf-8"),
                             "!setname 0x1 Nombre español\n!setname 0x3\n")
            # Missing new translations block release but must not block importing the workspace.
            with self.assertRaises(SystemExit):
                es.ensure_test_strings_ready(work / "test-strings.conf")

    def test_prompt_merge_keeps_manual_translations_imports_new_slots_and_clears_retired_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "source.cdb", Path(directory) / "target.cdb"
            for path, prompts in ((source, ("是否选卡？", "新提示", "新文本", "")),
                                  (target, ("¿Quieres elegir 1 monstruo de Nivel 10?", "", "旧文本", "Antiguo"))):
                with closing(sqlite3.connect(path)) as db:
                    db.execute("CREATE TABLE datas (id INTEGER PRIMARY KEY, atk INTEGER)")
                    db.execute("CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT, desc TEXT, str1 TEXT, str2 TEXT, str3 TEXT, str4 TEXT)")
                    db.execute("INSERT INTO datas VALUES (1, 100)")
                    db.execute("INSERT INTO texts VALUES (1, 'Nombre', 'Efecto', ?, ?, ?, ?)", prompts)
                    db.commit()
            es.merge_release_cdb(source, target)
            with closing(sqlite3.connect(target)) as db:
                self.assertEqual(db.execute("SELECT str1, str2, str3, str4 FROM texts").fetchone(),
                                 ("¿Quieres elegir 1 monstruo de Nivel 10?", "新提示", "新文本", ""))


if __name__ == "__main__":
    unittest.main()
