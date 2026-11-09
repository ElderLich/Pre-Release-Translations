"""Publication regressions for the LP02 and 27PR prompt blockers."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rush_prerelease as rush


class NovemberRushPromptTests(unittest.TestCase):
    def test_paletta_actions_translate_in_every_patcher_locale(self):
        patcher, _, _, _ = rush.prompt_tools()
        source = (120312001, "涂足的调色少女", "原文", "取出",
                  "是否从那之中选1只怪兽送去墓地？",
                  "是否选对方场上1只表侧表示怪兽改变属性·种族？",
                  "请选择要改变属性·种族的怪兽", *([""] * 12))
        for lang in ("en", "fr", "de", "es", "pt", "it", "jp", "kr"):
            with self.subTest(lang=lang):
                pack = patcher.build_translation_pack(lang)
                row = rush.apply_prompt_translations(source, source, lang, pack, {}, {})
                self.assertTrue(all(row[index] != source[index] for index in range(3, 7)))
                self.assertNotIn("{CARD}", " ".join(row[3:]))
                self.assertNotIn("改变", " ".join(row[3:]))
        english = rush.apply_prompt_translations(source, source, "en",
                                                 patcher.build_translation_pack("en"), {}, {})
        self.assertIn("Attribute and Type", english[5])
        self.assertIn("opponent's field", english[5])
        self.assertIn("Attribute and Type", english[6])

    def test_cosmostar_resolves_external_and_self_names(self):
        patcher, load, _, _ = rush.prompt_tools()
        source = (120311014, "夕极精 秋英宇宙星", "原文", "融合术召唤",
                  "是否选自己墓地1张「宇宙姬的嬉戏」加入手卡？",
                  "这个回合，不能把「夕极精 秋英宇宙星」的效果发动", *([""] * 13))
        row = (source[0], "Cosmostar", "Reviewed effect", *source[3:])
        mappings = load(rush.ROOT / "Shared/Mappings.csv", "en")
        result = rush.apply_prompt_translations(row, source, "en",
                    patcher.build_translation_pack("en"), mappings,
                    {120261058: (120261058, "Princess Cosmos' Jest")})
        self.assertIn("Princess Cosmos' Jest", result[4])
        self.assertIn("Cosmostar", result[5])
        self.assertNotIn("{CARD}", " ".join(result[1:]))
        self.assertEqual(rush.translated_row(result, source, "en", False), [])


if __name__ == "__main__":
    unittest.main()
