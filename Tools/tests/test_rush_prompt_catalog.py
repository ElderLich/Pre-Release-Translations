import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import PromptPatcher as prompts
import rush_prerelease as rush


class RushPromptCatalogTests(unittest.TestCase):
    def test_cost_and_alternate_wording_resolve_in_all_packs(self):
        cost = "把2张手卡送去墓地来发动"
        variant = "这个回合战斗破坏的怪兽回到持有者卡组下面"
        original = "这个回合，战斗破坏的怪兽不去墓地而回到持有者卡组下面"
        for lang in ("en", "de", "es", "fr", "it", "ja", "kr", "pt"):
            with self.subTest(lang=lang):
                pack = prompts.build_translation_pack(lang)
                for key in (cost, variant):
                    result = prompts.patch_prompt(key, pack)
                    self.assertNotEqual(result, key)
                    self.assertEqual(prompts.patch_prompt(result, pack), result)
                self.assertEqual(prompts.patch_prompt(variant, pack), prompts.patch_prompt(original, pack))
        english = prompts.build_translation_pack("en")
        self.assertIn("2 cards from your hand to the GY", prompts.patch_prompt(cost, english))
        self.assertIn("this card destroys by battle", prompts.patch_prompt(variant, english))

    def test_witch_set_prompt_uses_the_referenced_spell(self):
        source = (120309048, "身临神殿的黑魔女", "", "", "", "盖放「死之罪宝-濡血蝠」", *([""] * 13))
        localized = (source[0], "The Black Witch Facing the Temple", *source[2:])
        _, load, _, _ = rush.prompt_tools()
        names = {120309049: (120309049, "Sinful Spoils of Doom - Rciela")}
        row = rush.apply_prompt_translations(localized, source, "en",
                    prompts.build_translation_pack("en"), load(rush.ROOT / "Shared/Mappings.csv", "en"), names)
        self.assertIn("Sinful Spoils of Doom - Rciela", row[5])
        self.assertNotIn("{CARD}", row[5])


if __name__ == "__main__":
    unittest.main()
