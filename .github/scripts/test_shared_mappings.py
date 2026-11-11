from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import es_autopr
import ocg_autopr
import tcg_autopr


class StandardMappingTests(unittest.TestCase):
    def test_pending_workspace_reference_resolves_after_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, seed = root / "source.cdb", root / "seed.cdb"
            prompts = ", ".join(f"str{i} TEXT" for i in range(1, 17))
            with closing(sqlite3.connect(source)) as db:
                db.execute(f"CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT, desc TEXT, {prompts})")
                db.executemany("INSERT INTO texts (id,name,desc) VALUES (?,?,?)",
                               [(1, "原卡", "效果"), (2, "无垠黑暗", "效果")])
                db.commit()
            with closing(sqlite3.connect(seed)) as db:
                db.execute("CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT)")
                db.execute("INSERT INTO texts VALUES (3,'Blue-Eyes White Dragon')")
                db.commit()
            bindings = {1: {"str1": "self", "str2": "2", "str3": "3"}}
            for builder in (tcg_autopr, es_autopr, ocg_autopr):
                with self.subTest(builder=builder.__name__), patch.object(builder, "log"):
                    opening, closing_quote = ('「', '」') if builder is ocg_autopr else ('"', '"')
                    workspace = root / (builder.__name__ + ".cdb")
                    shutil.copyfile(source, workspace)
                    with closing(sqlite3.connect(workspace)) as db:
                        db.execute("UPDATE texts SET name='Reviewed', str1='{CARD}', str2='{CARD}', "
                                   "str3='{CARD}', str4='Translator wording' WHERE id=1")
                        db.commit()
                    builder.apply_mappings_to_cdb(workspace, bindings, seed, source)
                    with closing(sqlite3.connect(workspace)) as db:
                        self.assertEqual(db.execute("SELECT str1,str2,str3,str4 FROM texts WHERE id=1").fetchone(),
                                         (f'{opening}Reviewed{closing_quote}', '{CARD}',
                                          f'{opening}Blue-Eyes White Dragon{closing_quote}', 'Translator wording'))
                        db.execute("UPDATE texts SET name='Endless Darkness' WHERE id=2")
                        db.commit()
                    builder.apply_mappings_to_cdb(workspace, bindings, seed, source)
                    with closing(sqlite3.connect(workspace)) as db:
                        self.assertEqual(db.execute("SELECT str2 FROM texts WHERE id=1").fetchone()[0],
                                         f'{opening}Endless Darkness{closing_quote}')
