#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud AutoPR helper for ES workspace sync and release builds."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from shared_downloads import JSON_URL_DEFAULT, YPK_URL_DEFAULT, DownloadError, fetch_release_json, download_file as download_verified_file
from shared_mappings import load_locale_mappings_csv

ROOT = Path(__file__).resolve().parents[2]
ES_DIR = Path(os.environ.get("ES_AUTOPR_OUTPUT_DIR", ROOT / "ES")).resolve()
BASE_DIR = Path(os.environ.get("ES_AUTOPR_BASE_DIR", ES_DIR / "Base Files")).resolve()
WORK_DIR = Path(os.environ.get("ES_AUTOPR_WORK_DIR", ES_DIR / "Workspace")).resolve()
TOOLS_DIR = Path(os.environ.get("ES_AUTOPR_TOOLS_DIR", ROOT / "Tools")).resolve()
MAPPINGS_PATH = Path(os.environ.get("ES_AUTOPR_MAPPINGS_PATH", ROOT / "Shared" / "Mappings.csv")).resolve()

YPK_URL = os.environ.get(
    "ES_AUTOPR_YPK_URL",
    YPK_URL_DEFAULT,
)
CARDS_CDB_URL = os.environ.get(
    "ES_AUTOPR_CARDS_CDB_URL",
    "https://raw.githubusercontent.com/ElderLich/ygopro-database-elder/patch-1/locales/es-ES/cards.cdb",
)
JSON_URL = os.environ.get(
    "ES_AUTOPR_JSON_URL",
    JSON_URL_DEFAULT,
)

CARD_TOKEN = "{CARD}"
CARD_ID_DELIM = "|"
SELF_TOKEN = "self"

REPLACEMENT_MAP = {
    "/神": "/DIVINO", "/暗": "/OSCURIDAD", "/光": "/LUZ", "/风": "/VIENTO", "/風": "/VIENTO",
    "/地": "/TIERRA", "/水": "/AGUA", "/炎": "/FUEGO",
    "[怪兽": "[Monstruo", "[怪獸": "[Monstruo", "[魔法": "[Mágica", "[陷阱": "[Trampa",
    "幻想魔": "Ilusión", "魔法师": "Lanzador de Conjuros", "魔法師": "Lanzador de Conjuros",
    "兽战士": "Guerrero Bestia", "獸戰士": "Guerrero Bestia", "爬虫类": "Reptil", "爬蟲類": "Reptil",
    "念动力": "Psíquico", "念動力": "Psíquico", "幻神兽": "Bestia Divina", "幻神獸": "Bestia Divina",
    "创造神": "Dios Creador", "創造神": "Dios Creador", "电子界": "Ciberso", "電子界": "Ciberso",
    "鸟兽": "Bestia Alada", "鳥獸": "Bestia Alada", "战士": "Guerrero", "戰士": "Guerrero",
    "天使": "Hada", "恶魔": "Demonio", "惡魔": "Demonio", "不死": "Zombi",
    "机械": "Máquina", "機械": "Máquina", "岩石": "Roca", "植物": "Planta",
    "昆虫": "Insecto", "昆蟲": "Insecto", "恐龙": "Dinosaurio", "恐龍": "Dinosaurio",
    "海龙": "Serpiente Marina", "海龍": "Serpiente Marina", "幻龙": "Wyrm", "幻龍": "Wyrm",
    "水": "Aqua", "炎": "Piro", "雷": "Trueno", "龙": "Dragón", "龍": "Dragón",
    "兽": "Bestia", "獸": "Bestia", "鱼": "Pez", "魚": "Pez",
    "通常": "Normal", "效果": "Efecto", "融合": "Fusión", "仪式": "Ritual", "儀式": "Ritual",
    "灵魂": "Espíritu", "靈魂": "Espíritu", "同盟": "Unión", "二重": "Gemini",
    "调整": "Cantante", "調整": "Cantante", "同调": "Sincronía", "同步": "Sincronía",
    "衍生物": "Ficha", "速攻": "Juego Rápido", "永续": "Continua", "永續": "Continua",
    "装备": "Equipo", "裝備": "Equipo", "场地": "Campo", "場地": "Campo",
    "反击": "Contraefecto", "反擊": "Contraefecto", "反转": "VOLTEO", "反轉": "VOLTEO",
    "卡通": "Toon", "超量": "Xyz", "灵摆": "Péndulo", "靈擺": "Péndulo",
    "特殊召唤": "Invocación Especial", "特殊召喚": "Invocación Especial", "连接": "Enlace", "連接": "Enlace",
}

STRINGS_ENTRY_RE = re.compile(r"^(?P<indent>\s*)!(?P<directive>[^\s#]+)\s+(?P<code>\S+)(?:\s+(?P<name>.*\S))?\s*$")
PLACEHOLDER_TOKEN_RE = re.compile(r"\(\s*\{CARD\}\s*\)|（\s*\{CARD\}\s*）|\{CARD\}")


def log(message: str) -> None:
    print(f"[es-autopr] {message}", flush=True)


def fail(message: str) -> None:
    print(f"[es-autopr] ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        fail(f"Missing {label}: {path}")
    return path


def download_file(url: str, target: Path) -> None:
    try:
        download_verified_file(url, target, user_agent="PreReleaseTranslations-ES-AutoPR/1.0", log_fn=log)
    except DownloadError as exc:
        fail(str(exc))


def fetch_json(url: str) -> list[dict]:
    try:
        return fetch_release_json(url, user_agent="PreReleaseTranslations-ES-AutoPR/1.0", log_fn=log)
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


def copy_base_files_from_ypk(ypk_path: Path, cards_cdb: Path) -> None:
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


def sync_test_strings(source_path: Path, target_path: Path) -> None:
    source_text = source_path.read_text(encoding="utf-8-sig")
    target_text = target_path.read_text(encoding="utf-8-sig") if target_path.exists() else ""
    target_entries, target_dups = collect_string_entries(target_text)
    source_entries, source_dups = collect_string_entries(source_text)
    output_lines: list[str] = []
    pending = 0
    new_blank = 0
    for lineno, raw_line in enumerate(source_text.splitlines(), start=1):
        entry = parse_string_entry(raw_line, lineno)
        if not entry:
            output_lines.append(raw_line)
            continue
        local_entry = target_entries.get(entry["key"])
        local_name = local_entry["name"] if local_entry else ""
        if not local_name:
            pending += 1
            if local_entry is None:
                new_blank += 1
        output_lines.append(f"!{entry['directive']} {entry['code']} {local_name}".rstrip())
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(output_lines) + ("\n" if output_lines else ""), encoding="utf-8", newline="\n")
    log(f"Synced test-strings.conf: managed={len(source_entries)}, pending={pending}, new_blank={new_blank}, source_dups={len(source_dups)}, local_dups={len(target_dups)}")


def ensure_test_strings_ready(path: Path) -> None:
    text = require_file(path, "workspace test-strings.conf").read_text(encoding="utf-8-sig")
    seen = set()
    duplicate = []
    malformed = []
    missing = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entry = parse_string_entry(raw_line, lineno)
        if not entry:
            if stripped.startswith("!"):
                malformed.append(lineno)
            continue
        if entry["key"] in seen:
            duplicate.append((entry["key"], lineno))
        seen.add(entry["key"])
        if not entry["name"]:
            missing.append(entry["key"])
    if duplicate or malformed or missing:
        fail(f"test-strings.conf is not ready: duplicate={len(duplicate)}, malformed={len(malformed)}, missing_translation={len(missing)}")


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


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


def merge_mappings(existing: dict[int, dict], scan: dict[int, dict], present_ids: set[int]) -> dict[int, dict]:
    merged = {card_id: row for card_id, row in existing.items() if card_id in present_ids}
    for card_id, info in scan.items():
        row = merged.setdefault(card_id, {"id": str(card_id), "name": info.get("name", "")})
        row["name"] = info.get("name", row.get("name", ""))
        for index in range(1, 17):
            row.setdefault(f"str{index}", "")
        for column, count in info.get("need", {}).items():
            cell = clean_cell(row.get(column))
            if not cell or CARD_TOKEN in cell:
                row[column] = placeholder_marker(count)
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
        return f'"{name}"'

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

# ---- PENDULUM LAYOUT PATCH (CN prerelease -> ES headers) ----
PEND_LANG_LABELS = {
    "es": {"pendulum_scale": "Escala de Péndulo", "pendulum_effect": "Efecto de Péndulo", "monster_effect": "Efecto de Monstruo"},
}
PEND_SEP_ES = "━━━━━━━━━━━━━━━━━━━━━━━"
_CN_PEND_SCALE_RE = re.compile(r"^\s*←\s*(\d+)\s*【(?:灵摆|靈擺)】\s*(\d+)\s*→\s*(?:\r?\n)?", re.UNICODE)
_CN_MONSTER_MARKERS = ("【怪兽效果】", "【怪獸效果】")


def normalize_newlines(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def format_pendulum_desc(desc: str, lang_key: str = "es") -> str | None:
    value = normalize_newlines(desc)
    match = _CN_PEND_SCALE_RE.match(value)
    if not match:
        return None

    left, right = match.group(1), match.group(2)
    rest = value[match.end():]
    marker_index = -1
    marker = None
    for candidate in _CN_MONSTER_MARKERS:
        marker_index = rest.find(candidate)
        if marker_index != -1:
            marker = candidate
            break
    if marker_index == -1 or marker is None:
        return None

    labels = PEND_LANG_LABELS.get(lang_key, PEND_LANG_LABELS["es"])
    pend_body = rest[:marker_index].strip("\n")
    mon_body = rest[marker_index + len(marker):].strip("\n")

    output = [f"←◊{left} Escala {right}◊→", f"【{labels['pendulum_effect']}】"]
    if pend_body:
        output.append(pend_body)
    output.append(PEND_SEP_ES)
    output.append(f"【{labels['monster_effect']}】")
    if mon_body:
        output.append(mon_body)
    return "\n".join(output).strip("\n")


def patch_pendulum_layout_desc(cdb_path: Path, log_fn=None, report_limit: int = 25) -> None:
    logger = log_fn or log
    conn = sqlite3.connect(str(cdb_path))
    changed = 0
    shown = 0
    try:
        rows = conn.execute("SELECT id, name, desc FROM texts WHERE desc LIKE '%【灵摆】%' OR desc LIKE '%【靈擺】%'").fetchall()
        for card_id, name, desc in rows:
            if not desc:
                continue
            new_desc = format_pendulum_desc(desc, "es")
            if not new_desc or new_desc == desc:
                continue
            conn.execute("UPDATE texts SET desc=? WHERE id=?", (new_desc, card_id))
            changed += 1
            if shown < report_limit:
                logger(f"[PEND] Diseño de Péndulo parcheado para id={card_id} name={name!r}")
                shown += 1
        conn.commit()
    finally:
        conn.close()
    logger(f"[PEND] Listo. Filas de desc de Péndulo parcheadas: {changed}")

def run_prompt_patcher(cdb_path: Path, cards_cdb: Path) -> None:
    prompt_patcher = require_file(TOOLS_DIR / "PromptPatcher.py", "Tools/PromptPatcher.py")
    sys.path.insert(0, str(prompt_patcher.parent))
    from PromptPatcher import export_remaining_cn, patch_cdb

    patch_cdb(str(cdb_path), lang="es", log_fn=log, report_limit=25)
    report_dir = WORK_DIR / "Reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    export_remaining_cn(str(cdb_path), str(report_dir / "remaining_cn.json"), log_fn=log)

    scan, present = scan_cdb_placeholders(cdb_path)
    mappings = merge_mappings(load_mappings_csv(MAPPINGS_PATH), scan, present)
    save_mappings_csv(MAPPINGS_PATH, mappings)
    apply_mappings_to_cdb(cdb_path, mappings, cards_cdb)
    patch_pendulum_layout_desc(cdb_path, log_fn=log, report_limit=25)


def apply_workspace_mappings_for_release(cdb_path: Path, cards_cdb: Path) -> None:
    mappings = load_mappings_csv(MAPPINGS_PATH)
    if not mappings:
        log("No workspace mappings to apply to release CDB")
        return
    apply_mappings_to_cdb(cdb_path, mappings, cards_cdb)


def replace_payloads_in_ypk(base_ypk: Path, output_ypk: Path, payloads: dict[str, Path]) -> None:
    need = set(payloads)
    temp_ypk = output_ypk.with_suffix(".tmp")
    with zipfile.ZipFile(base_ypk, "r") as zin, zipfile.ZipFile(temp_ypk, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename in need:
                continue
            zout.writestr(item, zin.read(item.filename))
        for name, source in sorted(payloads.items()):
            zout.write(source, arcname=name)
            log(f"Added {name} to ypk")
    temp_ypk.replace(output_ypk)


def extract_id_from_pic_url(pic_url: str) -> str | None:
    match = re.search(r"/(\d+)\.(?:jpg|jpeg|png)(?:\?|$)", pic_url or "", flags=re.IGNORECASE)
    return match.group(1) if match else None


def translate_overall_string(value: object) -> object:
    if not isinstance(value, str):
        return value
    out = value
    for source, target in REPLACEMENT_MAP.items():
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

def write_version() -> None:
    (ES_DIR / "version.txt").write_text(str(int(time.time())), encoding="utf-8", newline="\n")


def write_commit_body(new_cards: list[str]) -> None:
    body_path = ES_DIR / ".autopr-commit-body.txt"
    if new_cards:
        body = "Cartas nuevas añadidas:\n" + "\n".join(f"- {name}" for name in new_cards) + "\n"
    else:
        body = "Cartas nuevas añadidas:\n(no hay cartas nuevas)\n"
    body_path.write_text(body, encoding="utf-8", newline="\n")


def workspace_sync() -> None:
    ES_DIR.mkdir(parents=True, exist_ok=True)
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".es-sync-", dir=ES_DIR) as temp_name:
        work = Path(temp_name)
        ypk = work / "ygopro-super-pre.ypk"
        cards_cdb = work / "cards.cdb"
        download_file(YPK_URL, ypk)
        download_file(CARDS_CDB_URL, cards_cdb)
        copy_base_files_from_ypk(ypk, cards_cdb)
        merge_release_cdb(BASE_DIR / "test-release.cdb", WORK_DIR / "test-release.cdb")
        sync_test_strings(BASE_DIR / "test-strings.conf", WORK_DIR / "test-strings.conf")
        workspace_update = WORK_DIR / "test-update.cdb"
        if workspace_update.exists():
            workspace_update.unlink()
            log(f"Removed unused workspace {workspace_update.name}")
        run_prompt_patcher(WORK_DIR / "test-release.cdb", cards_cdb)
    log("ES workspace sync completed")

def release_build() -> None:
    workspace_release = require_file(WORK_DIR / "test-release.cdb", "workspace test-release.cdb")
    workspace_strings = require_file(WORK_DIR / "test-strings.conf", "workspace test-strings.conf")
    ensure_test_strings_ready(workspace_strings)

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    base_release = BASE_DIR / "test-release.cdb"
    base_strings = BASE_DIR / "test-strings.conf"
    shutil.copy2(workspace_release, base_release)
    shutil.copy2(workspace_strings, base_strings)
    log("Mirrored ES workspace files into Base Files")

    payloads = {
        "test-release.cdb": base_release,
        "test-strings.conf": base_strings,
    }
    old_names = load_names(ES_DIR / "test-release.json")
    with tempfile.TemporaryDirectory(prefix=".es-release-", dir=ES_DIR) as temp_name:
        work = Path(temp_name)
        ypk = work / "ygopro-super-pre.ypk"
        cards_cdb = work / "cards.cdb"
        test_update = work / "test-update.cdb"
        download_file(YPK_URL, ypk)
        download_file(CARDS_CDB_URL, cards_cdb)
        apply_workspace_mappings_for_release(base_release, cards_cdb)
        extract_member(ypk, "test-update.cdb", test_update)
        translate_test_update_cdb(test_update, cards_cdb)
        payloads["test-update.cdb"] = test_update
        replace_payloads_in_ypk(ypk, ES_DIR / "ygopro-super-pre.ypk", payloads)
        new_data = write_release_json(payloads["test-release.cdb"], ES_DIR / "test-release.json")
        write_version()
    new_names = {item.get("name") for item in new_data if isinstance(item, dict) and item.get("name")}
    write_commit_body(sorted(new_names - old_names))
    log("ES release build completed")

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["workspace-sync", "release-build"])
    args = parser.parse_args()
    if args.command == "workspace-sync":
        workspace_sync()
    elif args.command == "release-build":
        release_build()


if __name__ == "__main__":
    main()
