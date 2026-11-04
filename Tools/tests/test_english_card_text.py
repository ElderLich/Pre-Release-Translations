"""Keep reviewed Standard English formatting stable across source refreshes."""
import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".github/scripts"))
import card_text_autofill as source
import tcg_autopr as tcg


class EnglishCardTextTests(unittest.TestCase):
    SEARCH = 'During your Main Phase: You can add 1 "Magician Girl" monster from your Deck to your hand.'
    SUMMON = ('If your opponent Normal or Special Summons a monster(s): You can Special Summon 1 '
              '"Magician Girl" monster from your hand with a different Attribute from the monsters '
              'you control, then if you control "Dark Magician Girl", you can negate the effects '
              'of 1 face-up card on the field until the end of this turn.')

    def draft(self, circled=False):
        text = ("You can only the (1) effect from this card's name once per turn.\r\n"
                f"(1) {self.SEARCH}\r\n(2) {self.SUMMON}")
        if circled:
            text = text.replace("the (1)", "the ①").replace("(1) ", "①：").replace("(2) ", "②：")
        return text

    def test_source_refresh_and_repeated_release_keep_tcg_format(self):
        for circled in (False, True):
            with self.subTest(circled=circled), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "cards.cdb"
                with closing(sqlite3.connect(path)) as db:
                    db.execute("CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT, desc TEXT)")
                    db.execute("INSERT INTO texts VALUES (100200299, 'B.L.A.C.K. Parfait', 'Previous text')")
                    db.commit()
                card = source.CardText("B.L.A.C.K. Parfait", self.draft(circled), "reviewed draft")
                source.update_cdb_texts(path, {100200299: card}, lambda _: None, refresh_existing=True)
                tcg.configure("en")
                tcg.patch_en_desc(path)
                tcg.patch_en_desc(path)
                with closing(sqlite3.connect(path)) as db:
                    result = db.execute("SELECT desc FROM texts").fetchone()[0]
                expected = (self.SEARCH + ' You can only use this effect of "B.L.A.C.K. Parfait" '
                            'once per turn. ' + self.SUMMON)
                self.assertEqual(result, expected)

    def test_other_cards_and_changed_restrictions_are_preserved(self):
        draft = self.draft()
        self.assertEqual(tcg.format_reviewed_en_tcg_desc(123, draft), draft)
        changed = draft.replace("the (1) effect", "each effect")
        self.assertEqual(tcg.format_reviewed_en_tcg_desc(100200299, changed), changed)
        official = self.SEARCH + " " + self.SUMMON
        self.assertEqual(tcg.format_reviewed_en_tcg_desc(100200299, official), official)

    def test_updated_effect_body_is_not_replaced_by_an_old_translation(self):
        changed = self.draft().replace("until the end of this turn", "until the end of the next turn")
        result = tcg.format_reviewed_en_tcg_desc(100200299, changed)
        self.assertIn("until the end of the next turn", result)
        self.assertNotIn("(1)", result)


if __name__ == "__main__":
    unittest.main()
