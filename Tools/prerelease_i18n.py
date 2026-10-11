"""UI catalogs. Card translations and source logs keep their original language."""
import json
from pathlib import Path
import tkinter as tk

LANGUAGE_NAMES = {"EN": "English", "FR": "Français", "IT": "Italiano", "DE": "Deutsch",
                  "ES": "Español", "PT": "Português", "JP": "日本語", "KR": "한국어",
                  "TH": "ไทย", "ZH-CN": "简体中文", "ZH-TW": "繁體中文"}
CATALOGS = json.loads(Path(__file__).with_name("prerelease_messages.json").read_text(encoding="utf-8"))


def translate(key, locale="EN", **values):
    return CATALOGS.get(locale, {}).get(key, key).format(**values) if values else CATALOGS.get(locale, {}).get(key, key)


class LocalizedVar(tk.StringVar):
    def __init__(self, owner, value=""):
        self.owner, self.key, self.values = owner, value, {}
        super().__init__(owner.root, value=owner.tr(value))
        owner.localized_vars.append(self)

    def set(self, value):
        self.set_template(value)

    def set_template(self, key, **values):
        self.key, self.values = key, values
        self.render()

    def render(self):
        super().set(self.owner.tr(self.key, **self.values))
