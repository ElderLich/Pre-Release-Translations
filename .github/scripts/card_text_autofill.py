#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fill prerelease card names/text from pack-numbered web sources."""

from __future__ import annotations

import html
import json
import re
import sqlite3
from contextlib import closing
import tempfile
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable


LogFn = Callable[[str], None]

YUGIPEDIA_API = "https://yugipedia.com/api.php"
OCG_LIST_URL = "https://ocg-card.com/list/{slug}/"
USER_AGENT = "PreReleaseTranslations-CardTextAutofill/1.0"

EN_PEND_SEP = "----------------------------------------"
JP_PEND_SCALE_RE = re.compile(r"^\s*(\d+)\s*$")
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]")
TITLE_DISAMBIGUATOR_RE = re.compile(r"\s+\((?:card|Rush Duel|anime|manga|video game|character|archetype|series)\)$", re.IGNORECASE)
YUGIPEDIA_LANG_FIELDS = {
    "en": ("", "text", "pendulum_effect"),
    "fr": ("fr_name", "fr_text", "fr_pendulum_effect"),
}
YUGIPEDIA_PEND_LABELS = {
    "en": ("Pendulum Scale", "Pendulum Effect", "Monster Effect"),
    "fr": ("Échelle Pendule", "Effet Pendule", "Effet de Monstre"),
}

EXTRA_SET_CODES_BY_ID = {
    100200289: "VJMP-JP289",
    100200290: "VJMP-JP290",
    100200291: "VJMP-JP291",
    100200292: "VJMP-JP292",
    100200293: "VJMP-JP293",
    100200294: "VJMP-JP294",
    100200295: "VJMP-JP295",
    100200296: "VJMP-JP296",
    100200297: "VJMP-JP297",
    100200298: "VJMP-JP298",
    100200299: "VJMP-JP299",
    100200300: "VJMP-JP300",
    100200301: "VJMP-JP301",
    100200302: "VJMP-JP302",
    100200303: "VJMP-JP303",
    100200304: "VJMP-JP304",
    100200305: "VJMP-JP305",
    100260001: "YO12-JP001",
    100262001: "26TP-JP101",
    100264002: "VP26-JP002",
    100264003: "VP26-JP003",
    100265002: "YOS3-JP002",
    # CH03's new cards use the JPP01-style printed numbers, not JP001.
    # Verified against https://yugipedia.com/wiki/CH03-JPP01 (and JPP02/JPP04).
    100274001: "CH03-JPP01",
    100274002: "CH03-JPP02",
    100274004: "CH03-JPP04",
    # Present in the upstream CDB before its pack list was updated.
    # https://yugipedia.com/wiki/IMPH-JP070
    101307070: "IMPH-JP070",
}

OFFICIAL_SLUGS_BY_SET_PREFIX = {
    "26TP": "26tp",
    "VJMP": "vj7",
    "VP26": "vp26",
    "YO12": "yo",
}


@dataclass(frozen=True)
class PackEntry:
    card_id: int
    pack_code: str
    source_name: str


@dataclass(frozen=True)
class CardText:
    name: str
    desc: str
    source: str


def log_default(message: str) -> None:
    print(message, flush=True)


REQUEST_CACHE: dict[str, str] = {}


def request_url(url: str) -> str:
    if url in REQUEST_CACHE:
        return REQUEST_CACHE[url]
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8", errors="replace")
        REQUEST_CACHE[url] = text
        return text


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def load_pack_entries_from_ypk(ypk_path: Path) -> dict[int, PackEntry]:
    entries: dict[int, PackEntry] = {}
    if not ypk_path.is_file():
        return entries
    with zipfile.ZipFile(ypk_path, "r") as archive:
        names = [name for name in archive.namelist() if name.lower().startswith("pack/") and name.lower().endswith(".ydk")]
        for name in sorted(names):
            pack_code = pack_code_from_filename(Path(name).name)
            with archive.open(name) as handle:
                text = handle.read().decode("utf-8-sig", errors="replace")
            add_pack_entries(entries, pack_code, name, text)
    return entries


def load_pack_entries_from_dir(pack_dir: Path) -> dict[int, PackEntry]:
    entries: dict[int, PackEntry] = {}
    if not pack_dir.is_dir():
        return entries
    for path in sorted(pack_dir.glob("*.ydk")):
        add_pack_entries(entries, pack_code_from_filename(path.name), path.name, path.read_text(encoding="utf-8-sig", errors="replace"))
    return entries


def add_pack_entries(entries: dict[int, PackEntry], pack_code: str, source_name: str, text: str) -> None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.isdigit():
            continue
        card_id = int(line)
        entries[card_id] = PackEntry(card_id=card_id, pack_code=pack_code, source_name=source_name)


def pack_code_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    if not stem:
        return ""
    return stem.split()[-1].upper()


def set_code_for_entry(entry: PackEntry, lang: str) -> str | None:
    if entry.card_id in EXTRA_SET_CODES_BY_ID:
        return EXTRA_SET_CODES_BY_ID[entry.card_id]
    if entry.pack_code.lower() == "etc":
        return None
    base, sep, suffix = entry.pack_code.partition("-")
    if not base:
        return None
    number = entry.card_id % 1000
    if number <= 0:
        return None
    if lang in {"en", "fr"}:
        prefix = entry.pack_code if sep else f"{base}-JP"
    elif lang == "jp":
        prefix = f"{base}-JP"
    else:
        return None
    return f"{prefix}{number:03d}"


def set_code_prefix(set_code: str) -> str:
    match = re.match(r"^([A-Z0-9]+)-", set_code or "")
    return match.group(1) if match else ""


def official_slug_for_set_code(set_code: str) -> str:
    prefix = set_code_prefix(set_code)
    return OFFICIAL_SLUGS_BY_SET_PREFIX.get(prefix, prefix.lower())


def pack_slug_for_entry(entry: PackEntry) -> str | None:
    set_code = set_code_for_entry(entry, "jp")
    if not set_code:
        return None
    return official_slug_for_set_code(set_code)


def normalize_lines(value: str) -> str:
    lines = [line.rstrip() for line in (value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def clean_wiki_text(value: str) -> str:
    text = value or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{Ruby\|([^|{}]+)\|[^{}]*\}\}", r"\1", text)
    text = re.sub(r"\[\[File:[^\]]+\]\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[[^|\]]+\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    # Unknown templates may contain effect text; leave them visible so the import
    # can reject the incomplete record instead of silently dropping mechanics.
    text = re.sub(r"\x27{2,5}", "", text)
    text = html.unescape(text)
    return normalize_lines(text)


def clean_yugipedia_title(title: str) -> str:
    return TITLE_DISAMBIGUATOR_RE.sub("", title or "").strip()


def parse_card_table_params(wikitext: str) -> dict[str, str]:
    match = re.search(r"\{\{CardTable2\s*(?P<body>.*?)\n\}\}", wikitext or "", flags=re.DOTALL)
    if not match:
        return {}
    params: dict[str, str] = {}
    current_key: str | None = None
    for raw_line in match.group("body").splitlines():
        if raw_line.startswith("|"):
            key, sep, value = raw_line[1:].partition("=")
            if not sep:
                current_key = None
                continue
            current_key = key.strip()
            params[current_key] = value.strip()
        elif current_key:
            params[current_key] += "\n" + raw_line.rstrip()
    return params


def format_yugipedia_card(title: str, params: dict[str, str], lang: str, rush: bool = False) -> CardText | None:
    if lang not in YUGIPEDIA_LANG_FIELDS:
        return None
    name_key, text_key, pendulum_key = YUGIPEDIA_LANG_FIELDS[lang]
    name = (clean_wiki_text(params.get("name", "")) or clean_yugipedia_title(title)) if lang == "en" else clean_wiki_text(params.get(name_key, ""))
    if not name:
        return None
    text = clean_wiki_text(params.get(text_key, ""))
    if rush:
        prefix = {"en": "", "fr": "fr_"}[lang]
        labels = {"en": ("REQUIREMENT", "EFFECT", "CONTINUOUS EFFECT", "MULTI-CHOICE EFFECT"),
                  "fr": ("CONDITION", "EFFET", "EFFET CONTINU", "EFFET MULTI-CHOIX")}[lang]
        output = []
        maximum = clean_wiki_text(params.get("maximum_atk", ""))
        if maximum:
            output.append("Maximum ATK " + maximum)
        for field in ("materials", "condition"):
            # Never combine a translated effect with English-only summoning costs.
            if params.get(field, "").strip() and not params.get(prefix + field, "").strip():
                return None
            value = clean_wiki_text(params.get(prefix + field, ""))
            if value:
                output.append(value)
        requirement = clean_wiki_text(params.get(prefix + "requirement", ""))
        if params.get("requirement", "").strip() and not requirement:
            return None
        kind = params.get("effect_types", "").lower()
        if requirement:
            output.extend(("【" + labels[0] + "】", requirement))
        if requirement or "continuous" in kind or "multi-choice" in kind:
            label = labels[2] if "continuous" in kind else labels[3] if "multi-choice" in kind else labels[1]
            output.append("【" + label + "】")
        output.append(text)
        desc = normalize_rush_layout("\n".join(output), lang)
        # A verified non-Effect Rush Ritual/Fusion can have no printed text.
        # Keep its known name (e.g. Sengenjin) without inventing flavor/effects.
        # An incomplete Effect Monster or translated cost still stays pending.
        non_effect = ("Non-Effect Monster" in params.get("misc", "").splitlines()
                      and "Effect" not in {part.strip() for part in params.get("types", "").split("/")}
                      and not any(params.get(field, "").strip()
                                  for field in ("materials", "condition", "requirement", "effect_types")))
        if (not text and not non_effect) or any(token in desc + name for token in ("{{", "}}")):
            return None
        return CardText(name=name, desc=desc, source=f"Yugipedia:{title}")
    pendulum = clean_wiki_text(params.get(pendulum_key, ""))
    if params.get("pendulum_effect", "").strip() and not pendulum:
        return None
    scale = clean_wiki_text(params.get("pendulum_scale", ""))
    if pendulum:
        scale_label, pendulum_label, monster_label = YUGIPEDIA_PEND_LABELS[lang]
        output = []
        if scale:
            output.append(f"{scale_label} = {scale}")
        output.append(f"[ {pendulum_label} ]")
        output.append(pendulum)
        output.append(EN_PEND_SEP)
        output.append(f"[ {monster_label} ]")
        if text:
            output.append(text)
        desc = normalize_lines("\n".join(output))
    else:
        desc = text
    if not desc or any(token in desc + name for token in ("{{", "}}")):
        return None
    return CardText(name=name, desc=desc, source=f"Yugipedia:{title}")


RUSH_HEADER_LABELS = {
    "REQUIREMENT", "EFFECT", "CONTINUOUS EFFECT", "MULTI-CHOICE EFFECT", "CONTINUOUS",
    "CONDITION", "EFFET", "EFFET CONTINU", "EFFET MULTI-CHOIX", "EFFET À CHOIX MULTIPLES",
    "BEDINGUNG", "EFFEKT", "DAUEREFFEKT", "PERMANENTER EFFEKT", "MEHRFACHAUSWAHL-EFFEKT",
    "REQUISITO", "EFFETTO", "EFFETTO CONTINUO", "EFFETTO A SCELTA", "EFFETTO SCELTA MULTIPLA",
    "EFEITO", "EFEITO CONTÍNUO", "EFEITO DE MÚLTIPLA ESCOLHA",
    "EFECTO", "EFECTO CONTINUO", "EFECTO DE OPCIÓN MÚLTIPLE", "EFECTO DE ELECCIÓN MÚLTIPLE",
    "条件", "効果", "永続効果", "選択効果", "조건", "효과", "지속 효과", "선택 효과",
    "效果", "永续效果", "选择效果", "條件", "永續效果", "選擇效果",
}
RUSH_HEADER_KEYS = {" ".join(unicodedata.normalize("NFKC", label).split()).casefold()
                    for label in RUSH_HEADER_LABELS}
BRACKET_MARKER = r"(?:\[[^\]\n]{1,40}\]|【[^】\n]{1,40}】|［[^］\n]{1,40}］)"



def normalize_rush_layout(value: str, lang: str = "en") -> str:
    """Match the source CDB builder: recognized headers/options get hard rows."""
    value = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    def section(match):
        label = " ".join(match.group(0)[1:-1].split())
        key = " ".join(unicodedata.normalize("NFKC", label).split()).casefold()
        if key not in RUSH_HEADER_KEYS:
            return match.group(0)
        heading = "【" + label + "】" if lang in {"jp", "ja", "kr", "ko", "zh-cn", "zh-tw"} else "[" + label + "]"
        return "\n" + heading + "\n"
    value = re.sub(BRACKET_MARKER, section, value)
    value = re.sub(r"[ \t\u3000]*([●•◆▪►])", r"\n\1", value)
    return "\n".join(line.strip() for line in value.split("\n") if line.strip())


def encode_rush_description(value: str, lang: str = "en") -> str:
    return normalize_rush_layout(value, lang).replace("\n", "\t\n")


def fetch_yugipedia_cards(set_codes: list[str], log: LogFn, lang: str = "en", rush: bool = False,
                          expected_codes: dict[str, str] | None = None,
                          diagnostics: dict[str, dict] | None = None) -> dict[str, CardText]:
    """Resolve every alias, then verify printed set numbers on the target card page."""
    cards: dict[str, CardText] = {}
    if lang not in YUGIPEDIA_LANG_FIELDS:
        return cards
    def issue(requested: str, reason: str, **details) -> None:
        if diagnostics is not None:
            key = (expected_codes or {}).get(requested, requested)
            diagnostics[key] = {"reason": reason, **details}

    unique_codes = sorted({code for code in set_codes if code})
    for batch in chunked(unique_codes, 40):
        query = {"action": "query", "titles": "|".join(batch), "redirects": "1",
                 "prop": "revisions", "rvprop": "content", "format": "json", "formatversion": "2"}
        try:
            data = json.loads(request_url(YUGIPEDIA_API + "?" + urllib.parse.urlencode(query)))
            if "error" in data:
                raise ValueError(data["error"].get("info", str(data["error"])))
        except Exception as exc:
            log(f"[SourceText] Yugipedia batch failed ({batch[0]}..): {exc}")
            for requested in batch:
                issue(requested, "Source request failed", detail=str(exc))
            continue
        result = data.get("query", {})
        aliases = {item["from"]: item["to"] for field in ("normalized", "redirects")
                   for item in result.get(field, [])}
        pages = {page.get("title"): page for page in result.get("pages", [])}
        for requested in batch:
            title, visited = requested, set()
            while title in aliases and title not in visited:
                visited.add(title)
                title = aliases[title]
            page = pages.get(title, {})
            revisions = page.get("revisions") or []
            if page.get("missing") or not revisions:
                issue(requested, "No source card page found")
                continue
            params = parse_card_table_params(revisions[0].get("content", ""))
            printed_codes = {line.split(";", 1)[0].strip().upper()
                             for key, value in params.items() if key.endswith("_sets")
                             for line in value.splitlines()}
            expected = (expected_codes or {}).get(requested, requested)
            source_url = "https://yugipedia.com/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
            if expected.upper() not in printed_codes:
                log(f"[SourceText] Unverified set code {requested} on {title}; skipped")
                issue(requested, "Printed set code does not match source page", source=source_url)
                continue
            card = format_yugipedia_card(title, params, lang, rush=rush)
            if card is None and lang == "fr":
                from codexygo_source import fetch_french
                card = fetch_french(title, params, rush, request_url, log)
                if card is not None:
                    source_url = card.source
            if card:
                cards[requested] = CardText(card.name, card.desc, source_url)
                if diagnostics is not None:
                    diagnostics.pop(expected, None)
            else:
                name_key, text_key, pendulum_key = YUGIPEDIA_LANG_FIELDS[lang]
                required = [text_key] + ([name_key] if name_key else [])
                if rush:
                    prefix = "fr_" if lang == "fr" else ""
                    required.extend(prefix + field for field in ("materials", "condition", "requirement")
                                    if params.get(field, "").strip())
                elif params.get("pendulum_effect", "").strip():
                    required.append(pendulum_key)
                missing = sorted({field for field in required if not clean_wiki_text(params.get(field, ""))})
                issue(requested, "Localized fields are missing" if missing else "Localized text is incomplete or unsupported",
                      source=source_url, missing_fields=missing)
    return cards


class OcgCardListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: dict[str, CardText] = {}
        self.current_code: str | None = None
        self.current_name = ""
        self.pending_code: str | None = None
        self.in_td = False
        self.td_class = ""
        self.td_colspan = ""
        self.capture_text: list[str] = []
        self.skip_depth = 0
        self.pending_scale: str | None = None
        self.current_scale: str | None = None
        self.current_pendulum: str | None = None
        self.current_maximum: str | None = None
        self.expect_maximum = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        if tag == "td":
            self.in_td = True
            self.td_class = attrs_dict.get("class", "")
            self.td_colspan = attrs_dict.get("colspan", "")
            self.capture_text = []
            if "card-number" in self.td_class:
                self.pending_code = None
            elif self.is_name_class(self.td_class):
                self.current_name = ""
            elif "card-text" in self.td_class or "card-p-scale" in self.td_class:
                pass
        elif self.in_td and self.is_name_class(self.td_class) and attrs_dict.get("class") in {"card-ruby", "limit-icon", "new-icon"}:
            self.skip_depth += 1
        elif self.skip_depth and tag not in {"br", "img", "hr", "input"}:
            self.skip_depth += 1
        elif self.in_td and tag == "br":
            self.capture_text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if tag != "td" or not self.in_td:
            return

        text = normalize_lines("".join(self.capture_text))
        if "card-number" in self.td_class:
            self.pending_code = text
            self.current_code = text
            self.current_name = ""
            self.current_pendulum = None
            self.pending_scale = None
            self.current_scale = None
            self.current_maximum = None
            self.expect_maximum = False
        elif "list-head" in self.td_class:
            self.expect_maximum = text == "MAXIMUM ATK"
        elif self.is_name_class(self.td_class):
            self.current_name = re.sub(r"(?:NEW|UP|（(?:イラスト[０-９0-9]+種|新イラスト)）)\s*$", "", text).strip()
        elif "card-p-scale" in self.td_class:
            self.pending_scale = text
        elif "card-text" in self.td_class and self.current_code and self.current_name:
            if self.expect_maximum and self.td_colspan == "8":
                self.current_maximum = text
                self.expect_maximum = False
            elif self.pending_scale or self.current_pendulum or self.td_colspan == "9":
                self.finish_text_cell(text)

        self.in_td = False
        self.td_class = ""
        self.td_colspan = ""
        self.capture_text = []

    def handle_data(self, data: str) -> None:
        if self.in_td and not self.skip_depth:
            self.capture_text.append(data)

    @staticmethod
    def is_name_class(class_name: str) -> bool:
        return any(token in class_name.split() for token in ("n-mon", "e-mon", "r-mon", "f-mon", "s-mon", "x-mon", "l-mon", "magic", "trap"))

    def finish_text_cell(self, text: str) -> None:
        if not text:
            return
        if self.pending_scale and JP_PEND_SCALE_RE.match(self.pending_scale):
            self.current_scale = self.pending_scale
            self.current_pendulum = text
            self.pending_scale = None
            return
        desc = text
        if self.current_pendulum:
            scale = self.current_scale or ""
            desc = "\n".join(
                [
                    f"【Ｐスケール：青{scale}／赤{scale}】" if scale else "【Ｐスケール】",
                    self.current_pendulum,
                    "【モンスター効果】",
                    text,
                ]
            )
            self.current_pendulum = None
            self.pending_scale = None
            self.current_scale = None
        if self.current_maximum:
            desc = "Maximum ATK " + self.current_maximum + "\n" + desc
        self.cards[self.current_code] = CardText(name=self.current_name, desc=normalize_lines(desc), source=f"ocg-card:{self.current_code}")


def fetch_ocg_cards(slugs: list[str], log: LogFn, rush: bool = False) -> dict[str, CardText]:
    cards: dict[str, CardText] = {}
    for slug in sorted({item for item in slugs if item}):
        url = ("https://ocg-card.com/list_r/{slug}/" if rush else OCG_LIST_URL).format(slug=urllib.parse.quote(slug.lower(), safe=""))
        try:
            page = request_url(url)
        except Exception as exc:
            log(f"[SourceText] OCG page failed ({slug}): {exc}")
            continue
        parser = OcgCardListParser()
        parser.feed(page)
        if parser.cards:
            for code, card in parser.cards.items():
                cards[code] = CardText(card.name, normalize_rush_layout(card.desc, "jp") if rush else card.desc, url)
            log(f"[SourceText] OCG {slug}: loaded={len(parser.cards)}")
        else:
            log(f"[SourceText] OCG {slug}: no card rows found")
    return cards


def current_cdb_names(cdb_path: Path) -> dict[int, str]:
    conn = sqlite3.connect(str(cdb_path))
    try:
        return {int(card_id): name or "" for card_id, name in conn.execute("SELECT id, name FROM texts")}
    finally:
        conn.close()


def fetch_yugipedia_by_existing_titles(names_by_id: dict[int, str], missing_ids: list[int], lang: str,
                                       log: LogFn, id_to_code: dict[int, str],
                                       diagnostics: dict[str, dict] | None = None) -> dict[int, CardText]:
    # A page may list its printed code before a redirect for that code exists.
    # Keep the old title fallback, but verify the exact code and reject duplicate
    # name candidates instead of silently selecting whichever row was last.
    grouped = {}
    for card_id in missing_ids:
        name = names_by_id.get(card_id, "").strip()
        if name and not CJK_RE.search(name):
            grouped.setdefault(name, []).append(card_id)
    title_to_id = {name: ids[0] for name, ids in grouped.items() if len(ids) == 1}
    if not title_to_id:
        return {}
    expected = {title: id_to_code[card_id] for title, card_id in title_to_id.items()}
    fallback_diagnostics = {}
    cards = fetch_yugipedia_cards(sorted(title_to_id), log, lang=lang, expected_codes=expected,
                                  diagnostics=fallback_diagnostics)
    if diagnostics is not None:
        verified_reasons = {"Localized fields are missing", "Localized text is incomplete or unsupported"}
        for code, issue in fallback_diagnostics.items():
            # A translated human name can fail to resolve even though the printed
            # set-code lookup already found and verified the correct card page.
            if diagnostics.get(code, {}).get("reason") not in verified_reasons:
                diagnostics[code] = issue
        for title in cards:
            diagnostics.pop(expected[title], None)
    return {card_id: cards[title] for title, card_id in title_to_id.items() if title in cards}


def needs_source_text(current: str, original: str, lang: str) -> bool:
    if not current.strip() or current == original:
        return True
    # Japanese legitimately contains Han characters. Only source-identical JP
    # fields are unreviewed; a custom Japanese translation must survive imports.
    return lang != "jp" and bool(re.search(r"[\u3400-\u9fff]", current))


def update_cdb_texts(cdb_path: Path, cards_by_id: dict[int, CardText], log: LogFn,
                     original: dict[int, tuple[str, str]] | None = None, lang: str = "en") -> int:
    if not cards_by_id:
        return 0
    conn = sqlite3.connect(str(cdb_path))
    changed = 0
    try:
        for card_id, card in cards_by_id.items():
            row = conn.execute("SELECT name, desc FROM texts WHERE id=?", (card_id,)).fetchone()
            if not row:
                continue
            raw = (original or {}).get(card_id, ("", ""))
            result = tuple(new if new and needs_source_text(old or "", raw[i] or "", lang) else old
                           for i, (old, new) in enumerate(zip(row, (card.name, card.desc))))
            if tuple(row) != result:
                conn.execute("UPDATE texts SET name=?, desc=? WHERE id=?", (*result, card_id))
                changed += 1
        conn.commit()
    finally:
        conn.close()
    log(f"[SourceText] Updated CDB rows={changed}; translator edits preserved")
    return changed


def original_texts_from_ypk(ypk_path: Path) -> dict[int, tuple[str, str]]:
    with zipfile.ZipFile(ypk_path) as archive, tempfile.TemporaryDirectory() as directory:
        names = [name for name in archive.namelist() if Path(name).name == "test-release.cdb"]
        if len(names) != 1:
            raise ValueError("Expected exactly one test-release.cdb in upstream package")
        path = Path(directory) / "cards.cdb"
        path.write_bytes(archive.read(names[0]))
        with closing(sqlite3.connect(path)) as conn:
            return {int(row[0]): tuple(row[1:]) for row in conn.execute("SELECT id,name,desc FROM texts")}


def build_id_to_set_code(entries: dict[int, PackEntry], ids: set[int], lang: str) -> dict[int, str]:
    id_to_code: dict[int, str] = {}
    for card_id in sorted(ids):
        entry = entries.get(card_id)
        if not entry:
            continue
        set_code = set_code_for_entry(entry, lang)
        if set_code:
            id_to_code[card_id] = set_code
    for card_id, set_code in EXTRA_SET_CODES_BY_ID.items():
        if card_id in ids:
            id_to_code.setdefault(card_id, set_code)
    return id_to_code


def manual_source_report(lang: str) -> dict:
    return {"language": lang, "mode": "manual", "automatic_fetch": False,
            "reason": "Korean card text is translator-managed; no web sources are queried.",
            "matches": {}}


def write_source_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def autofill_cdb_from_pack_sources(cdb_path: Path, ypk_path: Path, lang: str, log_fn: LogFn | None = None) -> int:
    log = log_fn or log_default
    if lang in {"kr", "ko"}:
        report = manual_source_report(lang)
        log(f"[SourceText] {report['reason']}")
        write_source_report(cdb_path.parent / "Reports/source_text.json", report)
        return 0
    if lang not in {"en", "fr", "jp"}:
        return 0
    entries = load_pack_entries_from_ypk(ypk_path)
    if not entries:
        log(f"[SourceText] No pack/*.ydk entries found in {ypk_path}")
        return 0
    names_by_id = current_cdb_names(cdb_path)
    original = original_texts_from_ypk(ypk_path)
    with closing(sqlite3.connect(cdb_path)) as conn:
        ids = {int(card_id) for card_id, name, desc in conn.execute("SELECT id,name,desc FROM texts")
               if any(needs_source_text(value or "", raw or "", lang)
                      for value, raw in zip((name, desc), original.get(int(card_id), ("", ""))))}
    id_to_code = build_id_to_set_code(entries, ids, lang)
    if not id_to_code:
        log("[SourceText] No set-code candidates matched the CDB")

    cards_by_id: dict[int, CardText] = {}
    diagnostics: dict[str, dict] = {}
    if lang in YUGIPEDIA_LANG_FIELDS:
        cards_by_code = fetch_yugipedia_cards(list(id_to_code.values()), log, lang=lang, diagnostics=diagnostics)
        for card_id, set_code in id_to_code.items():
            card = cards_by_code.get(set_code)
            if card:
                cards_by_id[card_id] = card
        missing_ids = [card_id for card_id in id_to_code if card_id not in cards_by_id]
        cards_by_id.update(fetch_yugipedia_by_existing_titles(names_by_id, missing_ids, lang, log, id_to_code, diagnostics))
    else:
        slugs = []
        for card_id in ids:
            entry = entries.get(card_id)
            if entry:
                slug = pack_slug_for_entry(entry)
                if slug:
                    slugs.append(slug)
        slugs.extend(official_slug_for_set_code(code) for code in id_to_code.values())
        cards_by_code = fetch_ocg_cards(slugs, log)
        for card_id, set_code in id_to_code.items():
            card = cards_by_code.get(set_code)
            if card:
                cards_by_id[card_id] = card

    missing = len(id_to_code) - len(cards_by_id)
    log(f"[SourceText] candidates={len(id_to_code)}, matched={len(cards_by_id)}, missing={missing}")
    report = {"language": lang, "candidates": len(id_to_code), "matched": len(cards_by_id),
              "unmatched_ids": sorted(ids - cards_by_id.keys()),
              "unresolved": [{"id": key, "name": names_by_id.get(key, ""), "set_code": id_to_code.get(key),
                              **diagnostics.get(id_to_code.get(key), {"reason": "No verified source match" if key in id_to_code else "No printed set code mapping"})}
                             for key in sorted(ids - cards_by_id.keys())],
              "matches": {str(key): {"set_code": id_to_code[key], "source": card.source}
                          for key, card in sorted(cards_by_id.items())}}
    report_path = cdb_path.parent / "Reports/source_text.json"
    write_source_report(report_path, report)
    return update_cdb_texts(cdb_path, cards_by_id, log, original, lang)
