#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud AutoPR helper for JP/KR workspace sync and release builds."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from shared_builds import copy_if_changed, publish_release, validate_release_strings, write_deterministic_package
from shared_downloads import JSON_URL_DEFAULT, YPK_URL_DEFAULT, DownloadError, fetch_release_json, download_file as download_verified_file
from shared_mappings import load_locale_mappings_csv

ROOT = Path(__file__).resolve().parents[2]

CARD_TOKEN = "{CARD}"
CARD_ID_DELIM = "|"
SELF_TOKEN = "self"

STRINGS_ENTRY_RE = re.compile(r"^(?P<indent>\s*)!(?P<directive>[^\s#]+)\s+(?P<code>\S+)(?:\s+(?P<name>.*\S))?\s*$")
PLACEHOLDER_TOKEN_RE = re.compile(r"\(\s*\{CARD\}\s*\)|（\s*\{CARD\}\s*）|\{CARD\}")

JP_REPLACEMENT_MAP = {
    "/神": "/神",
    "/暗": "/闇",
    "/光": "/光",
    "/风": "/風",
    "/風": "/風",
    "/地": "/地",
    "/水": "/水",
    "/炎": "/炎",
    "[怪兽": "[モンスター",
    "[怪獸": "[モンスター",
    "[魔法": "[魔法",
    "[陷阱": "[罠",
    "幻想魔": "幻想魔",
    "兽战士": "獣戦士",
    "獸戰士": "獣戦士",
    "魔法师": "魔法使い",
    "魔法師": "魔法使い",
    "爬虫类": "爬虫類",
    "爬蟲類": "爬虫類",
    "念动力": "サイキック",
    "念動力": "サイキック",
    "幻神兽": "幻神獣",
    "幻神獸": "幻神獣",
    "创造神": "創造神",
    "創造神": "創造神",
    "电子界": "サイバース",
    "電子界": "サイバース",
    "鸟兽": "鳥獣",
    "鳥獸": "鳥獣",
    "战士": "戦士",
    "戰士": "戦士",
    "天使": "天使",
    "恶魔": "悪魔",
    "惡魔": "悪魔",
    "不死": "アンデット",
    "机械": "機械",
    "機械": "機械",
    "岩石": "岩石",
    "植物": "植物",
    "昆虫": "昆虫",
    "昆蟲": "昆虫",
    "恐龙": "恐竜",
    "恐龍": "恐竜",
    "海龙": "海竜",
    "海龍": "海竜",
    "幻龙": "幻竜",
    "幻龍": "幻竜",
    "水": "水",
    "炎": "炎",
    "雷": "雷",
    "龙": "ドラゴン",
    "龍": "ドラゴン",
    "兽": "獣",
    "獸": "獣",
    "鱼": "魚",
    "魚": "魚",
    "通常": "通常",
    "效果": "効果",
    "融合": "融合",
    "仪式": "儀式",
    "儀式": "儀式",
    "灵魂": "スピリット",
    "靈魂": "スピリット",
    "同盟": "ユニオン",
    "二重": "デュアル",
    "调整": "チューナー",
    "調整": "チューナー",
    "同调": "シンクロ",
    "同步": "シンクロ",
    "衍生物": "衍生物",
    "速攻": "速攻",
    "永续": "永続",
    "永續": "永続",
    "装备": "装備",
    "裝備": "装備",
    "场地": "場所",
    "場地": "場所",
    "反击": "反撃",
    "反擊": "反撃",
    "反转": "リバース",
    "反轉": "リバース",
    "卡通": "トゥーン",
    "超量": "エクシーズ",
    "灵摆": "ペンデュラム",
    "靈擺": "ペンデュラム",
    "特殊召唤": "特殊召喚",
    "特殊召喚": "特殊召喚",
    "连接": "リンク",
    "連接": "リンク",
}


KR_REPLACEMENT_MAP = {
    "/神": "/신",
    "/暗": "/어둠",
    "/光": "/빛",
    "/风": "/바람",
    "/風": "/바람",
    "/地": "/땅",
    "/水": "/물",
    "/炎": "/화염",
    "[怪兽": "[몬스터",
    "[怪獸": "[몬스터",
    "[魔法": "[마법",
    "[陷阱": "[함정",
    "幻想魔": "환상마족",
    "兽战士": "야수전사",
    "獸戰士": "야수전사",
    "魔法师": "마법사족",
    "魔法師": "마법사족",
    "爬虫类": "파충류족",
    "爬蟲類": "파충류족",
    "念动力": "사이킥족",
    "念動力": "사이킥족",
    "幻神兽": "환신야수족",
    "幻神獸": "환신야수족",
    "创造神": "창조신족",
    "創造神": "창조신족",
    "电子界": "사이버스족",
    "電子界": "사이버스족",
    "鸟兽": "비행야수족",
    "鳥獸": "비행야수족",
    "战士": "전사족",
    "戰士": "전사족",
    "天使": "천사족",
    "恶魔": "악마족",
    "惡魔": "악마족",
    "不死": "언데드족",
    "机械": "기계족",
    "機械": "기계족",
    "岩石": "암석족",
    "植物": "식물족",
    "昆虫": "곤충족",
    "昆蟲": "곤충족",
    "恐龙": "공룡족",
    "恐龍": "공룡족",
    "海龙": "해룡족",
    "海龍": "해룡족",
    "幻龙": "환룡족",
    "幻龍": "환룡족",
    "水": "물족",
    "炎": "화염족",
    "雷": "번개족",
    "龙": "드래곤족",
    "龍": "드래곤족",
    "兽": "야수족",
    "獸": "야수족",
    "鱼": "어류족",
    "魚": "어류족",
    "通常": "일반",
    "效果": "효과",
    "融合": "융합",
    "仪式": "의식",
    "儀式": "의식",
    "灵魂": "스피릿",
    "靈魂": "스피릿",
    "同盟": "유니온",
    "二重": "듀얼",
    "调整": "튜너",
    "調整": "튜너",
    "同调": "싱크로",
    "同步": "싱크로",
    "衍生物": "토큰",
    "速攻": "속공",
    "永续": "지속",
    "永續": "지속",
    "装备": "장착",
    "裝備": "장착",
    "场地": "필드",
    "場地": "필드",
    "反击": "카운터",
    "反擊": "카운터",
    "反转": "리버스",
    "反轉": "리버스",
    "卡通": "툰",
    "超量": "엑시즈",
    "灵摆": "펜듈럼",
    "靈擺": "펜듈럼",
    "特殊召唤": "특수 소환",
    "特殊召喚": "특수 소환",
    "连接": "링크",
    "連接": "링크",
}


@dataclass(frozen=True)
class LangConfig:
    lang: str
    folder: str
    patch_lang: str
    pend_lang: str
    cards_locale: str
    tag_prefix: str
    commit_summary: str
    new_cards_header: str
    no_new_cards: str
    replacement_map: dict[str, str]

    @property
    def env_prefix(self) -> str:
        return self.folder.replace("-", "_").upper()


LANG_CONFIGS = {
    "jp": LangConfig(
        lang="jp",
        folder="JP",
        patch_lang="ja",
        pend_lang="jp",
        cards_locale="ja-JP",
        tag_prefix="JP-v",
        commit_summary="Pre-Release 更新 JP",
        new_cards_header="追加された新規カード:",
        no_new_cards="(新規カードなし)",
        replacement_map=JP_REPLACEMENT_MAP,
    ),
    "kr": LangConfig(
        lang="kr",
        folder="KR",
        patch_lang="kr",
        pend_lang="kr",
        cards_locale="ko-KR",
        tag_prefix="KR-v",
        commit_summary="사전 출시 업데이트 KR",
        new_cards_header="추가된 신규 카드:",
        no_new_cards="(신규 카드 없음)",
        replacement_map=KR_REPLACEMENT_MAP,
    ),
}


CONFIG: LangConfig
OUTPUT_DIR: Path
BASE_DIR: Path
WORK_DIR: Path
TOOLS_DIR: Path
MAPPINGS_PATH: Path
YPK_URL: str
CARDS_CDB_URL: str
JSON_URL: str


def configure(lang: str) -> None:
    global CONFIG, OUTPUT_DIR, BASE_DIR, WORK_DIR, TOOLS_DIR, MAPPINGS_PATH, YPK_URL, CARDS_CDB_URL, JSON_URL
    CONFIG = LANG_CONFIGS[lang]

    def env_value(name: str, default: str | Path) -> str:
        return os.environ.get(f"{CONFIG.env_prefix}_AUTOPR_{name}", os.environ.get(f"OCG_AUTOPR_{name}", str(default)))

    OUTPUT_DIR = Path(env_value("OUTPUT_DIR", ROOT / CONFIG.folder)).resolve()
    BASE_DIR = Path(env_value("BASE_DIR", OUTPUT_DIR / "Base Files")).resolve()
    WORK_DIR = Path(env_value("WORK_DIR", OUTPUT_DIR / "Workspace")).resolve()
    TOOLS_DIR = Path(env_value("TOOLS_DIR", ROOT / "Tools")).resolve()
    MAPPINGS_PATH = Path(env_value("MAPPINGS_PATH", ROOT / "Shared" / "Mappings.csv")).resolve()
    YPK_URL = env_value("YPK_URL", YPK_URL_DEFAULT)
    CARDS_CDB_URL = env_value(
        "CARDS_CDB_URL",
        f"https://raw.githubusercontent.com/ElderLich/ygopro-database-elder/master/locales/{CONFIG.cards_locale}/cards.cdb",
    )
    JSON_URL = env_value("JSON_URL", JSON_URL_DEFAULT)


def log(message: str) -> None:
    print(f"[{CONFIG.lang}-autopr] {message}", flush=True)


def fail(message: str) -> None:
    print(f"[{CONFIG.lang}-autopr] ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        fail(f"Missing {label}: {path}")
    return path


def request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": f"PreReleaseTranslations-{CONFIG.folder}-AutoPR/1.0"})


def download_file(url: str, target: Path) -> None:
    try:
        download_verified_file(url, target, user_agent=f"PreReleaseTranslations-{CONFIG.folder}-AutoPR/1.0", log_fn=log)
    except DownloadError as exc:
        fail(str(exc))


def fetch_json(url: str) -> list[dict]:
    try:
        return fetch_release_json(url, user_agent=f"PreReleaseTranslations-{CONFIG.folder}-AutoPR/1.0", log_fn=log)
    except DownloadError as exc:
        fail(str(exc))


def extract_member(zip_path: Path, member: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        try:
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
        except KeyError:
            fail(f"{member} was not found inside {zip_path}")


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def quoted_columns(columns: list[str]) -> str:
    return ", ".join(f'"{column}"' for column in columns)


def copy_base_files_from_ypk(ypk_path: Path) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    extract_member(ypk_path, "test-release.cdb", BASE_DIR / "test-release.cdb")
    extract_member(ypk_path, "test-strings.conf", BASE_DIR / "test-strings.conf")
    stale_update = BASE_DIR / "test-update.cdb"
    if stale_update.exists():
        stale_update.unlink()
        log(f"Removed unused Base Files/{stale_update.name}")


def merge_release_cdb(source_cdb: Path, target_cdb: Path) -> None:
    if not target_cdb.exists():
        target_cdb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_cdb, target_cdb)
        log(f"Initialized workspace CDB from {source_cdb}")
        return

    src = sqlite3.connect(str(source_cdb))
    tgt = sqlite3.connect(str(target_cdb))
    try:
        src.row_factory = sqlite3.Row
        tgt.row_factory = sqlite3.Row
        src_datas_cols = table_columns(src, "datas")
        src_texts_cols = table_columns(src, "texts")
        source_data_ids = {row[0] for row in src.execute("SELECT id FROM datas")}
        source_text_ids = {row[0] for row in src.execute("SELECT id FROM texts")}

        data_cols_no_id = [column for column in src_datas_cols if column != "id"]
        data_select = quoted_columns(src_datas_cols)
        data_insert = ",".join("?" for _ in src_datas_cols)
        data_update = ", ".join(f'"{column}"=?' for column in data_cols_no_id)
        for row in src.execute(f"SELECT {data_select} FROM datas ORDER BY id"):
            card_id = row["id"]
            if tgt.execute("SELECT 1 FROM datas WHERE id=?", (card_id,)).fetchone():
                if data_cols_no_id:
                    tgt.execute(f"UPDATE datas SET {data_update} WHERE id=?", ([row[column] for column in data_cols_no_id] + [card_id]))
            else:
                tgt.execute(f"INSERT INTO datas VALUES ({data_insert})", [row[column] for column in src_datas_cols])

        str_cols = [column for column in src_texts_cols if re.fullmatch(r"str\d+", column or "")]
        str_cols.sort(key=lambda column: int(column[3:]))
        text_select = quoted_columns(src_texts_cols)
        text_insert = ",".join("?" for _ in src_texts_cols)
        text_update = ", ".join(f'"{column}"=?' for column in str_cols)
        for row in src.execute(f"SELECT {text_select} FROM texts ORDER BY id"):
            card_id = row["id"]
            if tgt.execute("SELECT 1 FROM texts WHERE id=?", (card_id,)).fetchone():
                if str_cols:
                    tgt.execute(f"UPDATE texts SET {text_update} WHERE id=?", ([row[column] for column in str_cols] + [card_id]))
            else:
                tgt.execute(f"INSERT INTO texts VALUES ({text_insert})", [row[column] for column in src_texts_cols])

        stale_data = {row[0] for row in tgt.execute("SELECT id FROM datas")} - source_data_ids
        stale_text = {row[0] for row in tgt.execute("SELECT id FROM texts")} - source_text_ids
        if stale_data:
            tgt.executemany("DELETE FROM datas WHERE id=?", [(card_id,) for card_id in stale_data])
        if stale_text:
            tgt.executemany("DELETE FROM texts WHERE id=?", [(card_id,) for card_id in stale_text])

        tgt.commit()
        tgt.execute("VACUUM")
        log(f"Merged workspace CDB: datas={len(source_data_ids)}, texts={len(source_text_ids)}, removed_datas={len(stale_data)}, removed_texts={len(stale_text)}")
    finally:
        src.close()
        tgt.close()


def translate_test_update_cdb(target_path: Path, cards_cdb_path: Path) -> None:
    fields = ["name", "desc"] + [f"str{i}" for i in range(1, 17)]
    select_columns = ", ".join(fields)
    set_columns = ", ".join(f"{field}=?" for field in fields)
    src = sqlite3.connect(str(cards_cdb_path))
    tgt = sqlite3.connect(str(target_path))
    updated = 0
    missing = 0
    try:
        tgt.execute("BEGIN")
        ids = [row[0] for row in tgt.execute("SELECT id FROM texts").fetchall()]
        for card_id in ids:
            row = src.execute(f"SELECT {select_columns} FROM texts WHERE id=?", (card_id,)).fetchone()
            if row:
                tgt.execute(f"UPDATE texts SET {set_columns} WHERE id=?", (*row, card_id))
                updated += 1
            else:
                missing += 1
        tgt.commit()
        tgt.execute("VACUUM")
        log(f"Translated test-update.cdb: updated={updated}, missing_in_cards_cdb={missing}")
    finally:
        src.close()
        tgt.close()


def parse_string_entry(raw_line: str, lineno: int) -> dict | None:
    match = STRINGS_ENTRY_RE.match(raw_line)
    if not match:
        return None
    directive = match.group("directive")
    code = match.group("code")
    name = (match.group("name") or "").strip()
    return {"directive": directive, "code": code, "name": name, "key": f"!{directive.lower()} {code.lower()}", "lineno": lineno}


def collect_string_entries(text: str) -> tuple[dict[str, dict], list[tuple[str, int]]]:
    entries: dict[str, dict] = {}
    duplicates: list[tuple[str, int]] = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        entry = parse_string_entry(raw_line, lineno)
        if not entry:
            continue
        if entry["key"] in entries:
            duplicates.append((entry["key"], lineno))
        entries[entry["key"]] = entry
    return entries, duplicates


FULLWIDTH_MAP = {}
for i, fw in enumerate("０１２３４５６７８９"):
    FULLWIDTH_MAP[ord(str(i))] = ord(fw)
for c, fw in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"):
    FULLWIDTH_MAP[ord(c)] = ord(fw)
for c, fw in zip("abcdefghijklmnopqrstuvwxyz", "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"):
    FULLWIDTH_MAP[ord(c)] = ord(fw)
FULLWIDTH_MAP[ord("-")] = ord("－")
for ascii_char, fw_char in {
    ".": "．", ",": "，", ":": "：", ";": "；", "?": "？", "!": "！", "/": "／", "\\": "＼",
    "(": "（", ")": "）", "\"": "＂", "'": "＇", "&": "＆", "%": "％", "+": "＋", "=": "＝", "~": "～",
}.items():
    FULLWIDTH_MAP[ord(ascii_char)] = ord(fw_char)

ROMANS_RANGE = [
    "XXII", "XXI", "XX", "XIX", "XVIII", "XVII", "XVI", "XV", "XIV", "XIII",
    "XII", "XI", "X", "IX", "VIII", "VII", "VI", "V", "IV", "III", "II", "I",
]
ROMAN_PATTERN_JP = re.compile(r"\b(?:" + "|".join(ROMANS_RANGE) + r")\b", re.IGNORECASE)
JP_SCOPED_HALFWIDTH_TRANS = str.maketrans({"．": "."})
JP_SCOPED_PATTERN = re.compile(r"『[^』]*』|「[^」]*」")
JP_TITLE_SCOPED_PATTERN = re.compile(r"『[^』]*』")
JP_CDB_TEXT_COLUMNS = ["name", "desc"] + [f"str{i}" for i in range(1, 17)]


def to_fullwidth(value: str) -> str:
    return value.translate(FULLWIDTH_MAP)


def replace_roman_numerals_jp(text: str) -> str:
    def repl(match: re.Match) -> str:
        return "".join(to_fullwidth(char) for char in match.group(0).upper())

    return ROMAN_PATTERN_JP.sub(repl, text)


def scoped_halfwidth_jp(text: str) -> str:
    def repl(match: re.Match) -> str:
        segment = match.group(0)
        return f"{segment[0]}{segment[1:-1].translate(JP_SCOPED_HALFWIDTH_TRANS)}{segment[-1]}"

    return JP_SCOPED_PATTERN.sub(repl, text or "")


def scoped_title_halfwidth_jp(text: str) -> str:
    def repl(match: re.Match) -> str:
        segment = match.group(0)
        return f"{segment[0]}{segment[1:-1].translate(JP_SCOPED_HALFWIDTH_TRANS)}{segment[-1]}"

    return JP_TITLE_SCOPED_PATTERN.sub(repl, text or "")


def normalize_jp_cdb_value(column: str, value: object) -> object:
    if CONFIG.lang != "jp" or not isinstance(value, str) or not value:
        return value
    text = unicodedata.normalize("NFKC", value) if column == "name" else value
    text = replace_roman_numerals_jp(text)
    text = to_fullwidth(text)
    if column == "name":
        return scoped_halfwidth_jp(text)
    return scoped_title_halfwidth_jp(text)


def normalize_jp_cdb(cdb_path: Path) -> None:
    if CONFIG.lang != "jp":
        return
    conn = sqlite3.connect(str(cdb_path))
    changed_rows = 0
    changed_cells = 0
    try:
        available = set(table_columns(conn, "texts"))
        columns = [column for column in JP_CDB_TEXT_COLUMNS if column in available]
        if not columns:
            return
        select_columns = quoted_columns(["id"] + columns)
        set_clause = ", ".join(f'"{column}"=?' for column in columns)
        rows = conn.execute(f"SELECT {select_columns} FROM texts").fetchall()
        for row in rows:
            card_id = row[0]
            old_values = list(row[1:])
            new_values = [normalize_jp_cdb_value(column, value) for column, value in zip(columns, old_values)]
            changed_cells += sum(1 for old, new in zip(old_values, new_values) if old != new)
            if new_values != old_values:
                conn.execute(f"UPDATE texts SET {set_clause} WHERE id=?", (*new_values, card_id))
                changed_rows += 1
        conn.commit()
        if changed_rows:
            conn.execute("VACUUM")
        log(f"[JP] Normalized test-release.cdb full-width text: rows={changed_rows}, cells={changed_cells}")
    finally:
        conn.close()


def normalize_test_string_name(directive: str, code: str, name: str) -> str:
    _ = directive, code
    if CONFIG.lang != "jp":
        return (name or "").strip()
    text = unicodedata.normalize("NFKC", name or "")
    text = replace_roman_numerals_jp(text)
    text = to_fullwidth(text)
    text = scoped_halfwidth_jp(text)
    return text.strip()


def sync_test_strings(source_path: Path, target_path: Path) -> None:
    source_text = source_path.read_text(encoding="utf-8-sig")
    target_text = target_path.read_text(encoding="utf-8-sig") if target_path.exists() else ""
    target_entries, target_dups = collect_string_entries(target_text)
    source_entries, source_dups = collect_string_entries(source_text)
    output_lines: list[str] = []
    pending = 0
    new_blank = 0
    normalized = 0
    for lineno, raw_line in enumerate(source_text.splitlines(), start=1):
        entry = parse_string_entry(raw_line, lineno)
        if not entry:
            output_lines.append(raw_line)
            continue
        local_entry = target_entries.get(entry["key"])
        local_name = local_entry["name"] if local_entry else ""
        if local_name:
            fixed_name = normalize_test_string_name(entry["directive"], entry["code"], local_name)
            if fixed_name != local_name:
                normalized += 1
            local_name = fixed_name
        else:
            pending += 1
            if local_entry is None:
                new_blank += 1
        output_lines.append(f"!{entry['directive']} {entry['code']} {local_name}".rstrip())
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(output_lines) + ("\n" if output_lines else ""), encoding="utf-8", newline="\n")
    log(f"Synced test-strings.conf: managed={len(source_entries)}, pending={pending}, new_blank={new_blank}, normalized={normalized}, source_dups={len(source_dups)}, local_dups={len(target_dups)}")


def ensure_test_strings_ready(path: Path) -> None:
    require_file(path, "workspace test-strings.conf")
    try:
        validate_release_strings(path)
    except ValueError as exc:
        fail(str(exc))


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def is_blank(value: object) -> bool:
    return not clean_cell(value)


def count_placeholders(text: object) -> int:
    return str(text or "").count(CARD_TOKEN)


def placeholder_marker(count: int) -> str:
    return CARD_ID_DELIM.join([CARD_TOKEN] * count) if count > 0 else ""


def parse_card_ids(cell: object) -> list[str]:
    text = clean_cell(cell)
    if not text or CARD_TOKEN in text:
        return []
    parts = [part.strip() for part in text.split(CARD_ID_DELIM)] if CARD_ID_DELIM in text else re.split(r"[,\s;]+", text)
    out = []
    for part in parts:
        if not part:
            continue
        if part.lower() == SELF_TOKEN:
            out.append(SELF_TOKEN)
        elif re.fullmatch(r"\d+", part):
            out.append(part)
    return out


def load_mappings_csv(path: Path) -> dict[int, dict[str, str]]:
    return load_locale_mappings_csv(path)


def save_mappings_csv(path: Path, rows: dict[int, dict[str, str]]) -> None:
    _ = path, rows


def scan_cdb_placeholders(cdb_path: Path) -> tuple[dict[int, dict], set[int]]:
    conn = sqlite3.connect(str(cdb_path))
    scan = {}
    present = set()
    try:
        cols = ["id", "name"] + [f"str{i}" for i in range(1, 17)]
        for row in conn.execute(f"SELECT {', '.join(cols)} FROM texts"):
            card_id = int(row[0])
            present.add(card_id)
            need = {}
            for index in range(1, 17):
                count = count_placeholders(row[1 + index])
                if count:
                    need[f"str{index}"] = count
            if need:
                scan[card_id] = {"id": card_id, "name": row[1] or "", "need": need}
    finally:
        conn.close()
    return scan, present


def merge_mappings(existing: dict[int, dict], scan: dict[int, dict], present_ids: set[int], report_limit: int = 25) -> dict[int, dict]:
    merged: dict[int, dict] = {}
    added_rows, removed_rows, added_slots, removed_slots, mismatches, copied_moves = [], [], [], [], [], []

    for card_id, row in existing.items():
        if card_id in present_ids:
            merged[card_id] = row
        else:
            removed_rows.append(card_id)

    for card_id, info in scan.items():
        name_now = info.get("name", "")
        need = info.get("need", {})
        if card_id not in merged:
            merged[card_id] = {"id": str(card_id), "name": name_now}
            for index in range(1, 17):
                merged[card_id][f"str{index}"] = ""
            added_rows.append(card_id)
        elif name_now:
            merged[card_id]["name"] = name_now

        row = merged[card_id]
        for index in range(1, 17):
            column = f"str{index}"
            needed_now = column in need
            had_before = not is_blank(row.get(column, ""))
            if needed_now and not had_before:
                added_slots.append((card_id, column))
            if not needed_now and had_before:
                removed_slots.append((card_id, column))

        for column, count in need.items():
            current = clean_cell(row.get(column, ""))
            ids = parse_card_ids(current)
            if ids:
                if len(ids) != count:
                    mismatches.append((card_id, column, count, len(ids), current))
                continue
            marker = placeholder_marker(count)
            if not current or (CARD_TOKEN in current and current != marker):
                row[column] = marker

        needed_cols = sorted(need)
        numeric_cols = [f"str{i}" for i in range(1, 17) if parse_card_ids(row.get(f"str{i}", ""))]
        if len(needed_cols) == 1 and len(numeric_cols) == 1:
            target = needed_cols[0]
            source = numeric_cols[0]
            target_value = clean_cell(row.get(target, ""))
            if target != source and (not target_value or CARD_TOKEN in target_value):
                row[target] = row[source]
                copied_moves.append((card_id, source, target))

    if added_rows:
        log(f"[Mappings] new rows: {len(added_rows)} sample={added_rows[:report_limit]}")
    if removed_rows:
        log(f"[Mappings] removed stale rows: {len(removed_rows)} sample={removed_rows[:report_limit]}")
    if added_slots:
        log(f"[Mappings] new placeholder slots: {len(added_slots)} sample={added_slots[:report_limit]}")
    if removed_slots:
        log(f"[Mappings] slots no longer present, kept in CSV: {len(removed_slots)} sample={removed_slots[:report_limit]}")
    if copied_moves:
        log(f"[Mappings] copied obvious moved ids: {len(copied_moves)} sample={copied_moves[:report_limit]}")
    if mismatches:
        log(f"[Mappings] placeholder count mismatches: {len(mismatches)}")
        for card_id, column, need_count, got_count, cell in mismatches[:report_limit]:
            log(f"  - id={card_id} {column}: need={need_count}, csv={got_count}: {cell!r}")
    return merged


def replace_placeholders(template: str, names: list[str]) -> str:
    index = 0

    def repl(match: re.Match) -> str:
        nonlocal index
        if index >= len(names):
            return match.group(0)
        name = names[index]
        index += 1
        token = match.group(0)
        if token.startswith("("):
            return f"({name})"
        if token.startswith("（"):
            return f"（{name}）"
        return f"「{name}」"

    return PLACEHOLDER_TOKEN_RE.sub(repl, template)


def apply_mappings_to_cdb(cdb_path: Path, mappings: dict[int, dict], cards_cdb: Path) -> None:
    conn = sqlite3.connect(str(cdb_path))
    cards_conn = sqlite3.connect(str(cards_cdb))
    name_cache: dict[str, str | None] = {}
    updated = 0
    skipped = 0
    try:
        def get_name(card_id: str) -> str | None:
            if card_id in name_cache:
                return name_cache[card_id]
            row = cards_conn.execute("SELECT name FROM texts WHERE id=?", (int(card_id),)).fetchone()
            name_cache[card_id] = row[0] if row else None
            return name_cache[card_id]

        rows = conn.execute("SELECT id, name, " + ", ".join(f"str{i}" for i in range(1, 17)) + " FROM texts").fetchall()
        set_clause = ", ".join(f"str{i}=?" for i in range(1, 17))
        for row in rows:
            card_id = int(row[0])
            mapping = mappings.get(card_id)
            if not mapping:
                continue
            values = list(row[2:])
            changed = False
            self_name = row[1] or clean_cell(mapping.get("name"))
            for index in range(1, 17):
                current = str(values[index - 1] or "")
                if CARD_TOKEN not in current:
                    continue
                tokens = parse_card_ids(mapping.get(f"str{index}"))
                if len(tokens) != count_placeholders(current):
                    if tokens:
                        skipped += 1
                    continue
                names = []
                for token in tokens:
                    name = self_name if token.lower() == SELF_TOKEN else get_name(token)
                    if not name:
                        names = []
                        break
                    names.append(name)
                if not names:
                    skipped += 1
                    continue
                replaced = replace_placeholders(current, names)
                if replaced != current:
                    values[index - 1] = replaced
                    changed = True
            if changed:
                conn.execute(f"UPDATE texts SET {set_clause} WHERE id=?", (*values, card_id))
                updated += 1
        conn.commit()
        log(f"Applied mappings: updated_rows={updated}, skipped_cells={skipped}")
    finally:
        conn.close()
        cards_conn.close()


PEND_LANG_LABELS = {
    "jp": {"pendulum_scale": "Ｐスケール", "pendulum_effect": "Ｐ効果", "monster_effect": "モンスター効果"},
    "kr": {"pendulum_scale": "펜듈럼 스케일", "pendulum_effect": "펜듈럼 효과", "monster_effect": "몬스터 효과"},
}
CN_PEND_SCALE_RE = re.compile(r"^\s*←\s*(\d+)\s*【(?:灵摆|靈擺)】\s*(\d+)\s*→\s*(?:\r?\n)?", re.UNICODE)
CN_MONSTER_MARKERS = ("【怪兽效果】", "【怪獸效果】")
FW_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")


def normalize_newlines(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def format_pend_scale_line(left: str, right: str) -> str:
    if CONFIG.pend_lang == "jp":
        return f"【Ｐスケール：青{left.translate(FW_DIGITS)}／赤{right.translate(FW_DIGITS)}】"
    labels = PEND_LANG_LABELS[CONFIG.pend_lang]
    return f"【{labels['pendulum_scale']}：{left}／{right}】"


def format_pendulum_desc(desc: str) -> str | None:
    value = normalize_newlines(desc)
    match = CN_PEND_SCALE_RE.match(value)
    if not match:
        return None
    left, right = match.group(1), match.group(2)
    rest = value[match.end():]

    marker_index = -1
    marker = None
    for candidate in CN_MONSTER_MARKERS:
        marker_index = rest.find(candidate)
        if marker_index != -1:
            marker = candidate
            break
    if marker_index == -1 or marker is None:
        return None

    labels = PEND_LANG_LABELS[CONFIG.pend_lang]
    pend_body = rest[:marker_index].strip("\n")
    mon_body = rest[marker_index + len(marker):].strip("\n")

    output = [format_pend_scale_line(left, right)]
    if CONFIG.pend_lang != "jp":
        output.append(f"【{labels['pendulum_effect']}】")
    if pend_body:
        output.append(pend_body)
    output.append(f"【{labels['monster_effect']}】")
    if mon_body:
        output.append(mon_body)
    return "\n".join(output).strip("\n")


def patch_pendulum_layout_desc(cdb_path: Path, report_limit: int = 25) -> None:
    conn = sqlite3.connect(str(cdb_path))
    changed = 0
    shown = 0
    try:
        rows = conn.execute("SELECT id, name, desc FROM texts WHERE desc LIKE '%【灵摆】%' OR desc LIKE '%【靈擺】%'").fetchall()
        for card_id, name, desc in rows:
            if not desc:
                continue
            new_desc = format_pendulum_desc(desc)
            if not new_desc or new_desc == desc:
                continue
            conn.execute("UPDATE texts SET desc=? WHERE id=?", (new_desc, card_id))
            changed += 1
            if shown < report_limit:
                log(f"[PEND] patched pendulum layout for id={card_id} name={name!r}")
                shown += 1
        conn.commit()
    finally:
        conn.close()
    log(f"[PEND] completed: changed={changed}")


def patcher_log(message: str) -> None:
    text = str(message)
    # JP/KR text is CJK too, so PromptPatcher's remaining-CN report is noisy there.
    if CONFIG.lang in {"jp", "kr"} and (text.startswith("[CN-Report]") or text.startswith("  x")):
        return
    log(text)


def run_prompt_patcher(cdb_path: Path, cards_cdb: Path) -> None:
    prompt_patcher = require_file(TOOLS_DIR / "PromptPatcher.py", "Tools/PromptPatcher.py")
    sys.path.insert(0, str(prompt_patcher.parent))
    from PromptPatcher import export_remaining_cn, patch_cdb

    patch_cdb(str(cdb_path), lang=CONFIG.patch_lang, log_fn=patcher_log, report_limit=25)
    report_dir = WORK_DIR / "Reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    export_remaining_cn(str(cdb_path), str(report_dir / "remaining_cn.json"), log_fn=patcher_log)

    scan, present = scan_cdb_placeholders(cdb_path)
    mappings = merge_mappings(load_mappings_csv(MAPPINGS_PATH), scan, present)
    save_mappings_csv(MAPPINGS_PATH, mappings)
    apply_mappings_to_cdb(cdb_path, mappings, cards_cdb)
    patch_pendulum_layout_desc(cdb_path)
    normalize_jp_cdb(cdb_path)


def autofill_source_text(cdb_path: Path, ypk_path: Path) -> None:
    try:
        from card_text_autofill import autofill_cdb_from_pack_sources

        # KR writes a manual-only report and never requests external card text.
        autofill_cdb_from_pack_sources(cdb_path, ypk_path, CONFIG.lang, log_fn=log)
        if CONFIG.lang == "jp":
            normalize_jp_cdb(cdb_path)
    except Exception as exc:
        log(f"[SourceText] skipped after error: {exc}")


def apply_workspace_mappings_for_release(cdb_path: Path, cards_cdb: Path) -> None:
    mappings = load_mappings_csv(MAPPINGS_PATH)
    if not mappings:
        log("No workspace mappings to apply to release CDB")
        return
    apply_mappings_to_cdb(cdb_path, mappings, cards_cdb)


def replace_payloads_in_ypk(base_ypk: Path, output_ypk: Path, payloads: dict[str, Path]) -> None:
    write_deterministic_package(base_ypk, output_ypk, payloads)
    log(f"Wrote deterministic package {output_ypk}")


def extract_id_from_pic_url(pic_url: str) -> str | None:
    match = re.search(r"/(\d+)\.(?:jpg|jpeg|png)(?:\?|$)", pic_url or "", flags=re.IGNORECASE)
    return match.group(1) if match else None


def translate_overall_string(value: object) -> object:
    if not isinstance(value, str):
        return value
    out = value
    for source, target in CONFIG.replacement_map.items():
        out = out.replace(source, target)
    return out


def load_names(json_path: Path) -> set[str]:
    if not json_path.is_file():
        return set()
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {item.get("name") for item in data if isinstance(item, dict) and item.get("name")}


def write_release_json(cdb_path: Path, output_json: Path) -> list[dict]:
    data = fetch_json(JSON_URL)
    conn = sqlite3.connect(str(cdb_path))
    cursor = conn.cursor()
    updated = 0
    missing = 0
    try:
        for item in data:
            if not isinstance(item, dict):
                continue
            card_id = extract_id_from_pic_url(item.get("picUrl", ""))
            if card_id:
                cursor.execute("SELECT name, desc FROM texts WHERE id=?", (card_id,))
                row = cursor.fetchone()
                if row:
                    item["name"], item["desc"] = row
                    updated += 1
                else:
                    missing += 1
            item["overallString"] = translate_overall_string(item.get("overallString"))
    finally:
        conn.close()

    output_json.parent.mkdir(parents=True, exist_ok=True)
    with output_json.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    log(f"Wrote {output_json}: updated={updated}, missing={missing}")
    return data


def write_commit_body(new_cards: list[str]) -> None:
    body_path = OUTPUT_DIR / ".autopr-commit-body.txt"
    if new_cards:
        body = CONFIG.new_cards_header + "\n" + "\n".join(f"- {name}" for name in new_cards) + "\n"
    else:
        body = CONFIG.new_cards_header + "\n" + CONFIG.no_new_cards + "\n"
    body_path.write_text(body, encoding="utf-8", newline="\n")


def workspace_sync() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{CONFIG.lang}-sync-", dir=OUTPUT_DIR) as temp_name:
        work = Path(temp_name)
        ypk = work / "ygopro-super-pre.ypk"
        cards_cdb = work / "cards.cdb"
        download_file(YPK_URL, ypk)
        download_file(CARDS_CDB_URL, cards_cdb)
        copy_base_files_from_ypk(ypk)
        merge_release_cdb(BASE_DIR / "test-release.cdb", WORK_DIR / "test-release.cdb")
        sync_test_strings(BASE_DIR / "test-strings.conf", WORK_DIR / "test-strings.conf")
        workspace_update = WORK_DIR / "test-update.cdb"
        if workspace_update.exists():
            workspace_update.unlink()
            log(f"Removed unused workspace {workspace_update.name}")
        run_prompt_patcher(WORK_DIR / "test-release.cdb", cards_cdb)
        autofill_source_text(WORK_DIR / "test-release.cdb", ypk)
    log(f"{CONFIG.folder} workspace sync completed")


def release_build() -> None:
    workspace_release = require_file(WORK_DIR / "test-release.cdb", "workspace test-release.cdb")
    workspace_strings = require_file(WORK_DIR / "test-strings.conf", "workspace test-strings.conf")
    ensure_test_strings_ready(workspace_strings)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    old_names = load_names(OUTPUT_DIR / "test-release.json")
    with tempfile.TemporaryDirectory(prefix=f".{CONFIG.lang}-release-", dir=OUTPUT_DIR) as temp_name:
        work = Path(temp_name)
        ypk = work / "ygopro-super-pre.ypk"
        cards_cdb = work / "cards.cdb"
        release_cdb = work / "test-release.cdb"
        test_update = work / "test-update.cdb"
        shutil.copy2(workspace_release, release_cdb)
        download_file(YPK_URL, ypk)
        download_file(CARDS_CDB_URL, cards_cdb)
        apply_workspace_mappings_for_release(release_cdb, cards_cdb)
        normalize_jp_cdb(release_cdb)
        extract_member(ypk, "test-update.cdb", test_update)
        translate_test_update_cdb(test_update, cards_cdb)
        payloads = {
            "test-release.cdb": release_cdb,
            "test-strings.conf": workspace_strings,
            "test-update.cdb": test_update,
        }
        candidate_ypk = work / "release.ypk"
        candidate_json = work / "test-release.json"
        replace_payloads_in_ypk(ypk, candidate_ypk, payloads)
        new_data = write_release_json(release_cdb, candidate_json)
        changed = publish_release(candidate_ypk, candidate_json, OUTPUT_DIR)
        copy_if_changed(release_cdb, BASE_DIR / "test-release.cdb")
        copy_if_changed(workspace_strings, BASE_DIR / "test-strings.conf")
        log("Published changed release content" if changed else "Release content unchanged; kept package, JSON and version")
    new_names = {item.get("name") for item in new_data if isinstance(item, dict) and item.get("name")}
    write_commit_body(sorted(new_names - old_names))
    log(f"{CONFIG.folder} release build completed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", choices=sorted(LANG_CONFIGS), required=True)
    parser.add_argument("command", choices=["workspace-sync", "release-build", "check-release"])
    args = parser.parse_args()
    configure(args.lang)
    if args.command == "workspace-sync":
        workspace_sync()
    elif args.command == "release-build":
        release_build()
    elif args.command == "check-release":
        ensure_test_strings_ready(WORK_DIR / "test-strings.conf")


if __name__ == "__main__":
    main()
