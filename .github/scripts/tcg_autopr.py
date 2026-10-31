#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud AutoPR helper for DE/EN/FR/IT/PT/TH workspace sync and release builds."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from shared_builds import copy_if_changed, publish_release, validate_release_strings, write_deterministic_package
from shared_downloads import JSON_URL_DEFAULT, YPK_URL_DEFAULT, DownloadError, fetch_release_json, download_file as download_verified_file
from shared_mappings import load_locale_mappings_csv, save_locale_mappings_csv

ROOT = Path(__file__).resolve().parents[2]

CARDS_CDB_URL_TEMPLATE = "https://raw.githubusercontent.com/ElderLich/ygopro-database-elder/master/locales/{locale}/cards.cdb"


@dataclass(frozen=True)
class LangConfig:
    lang: str
    folder: str
    cards_locale: str
    tag_prefix: str
    commit_title: str
    new_cards_header: str
    no_new_cards: str
    replacement_map: dict[str, str]
    patch_lang: str
    pend_lang: str


LANG_CONFIGS = {
    'de': LangConfig(
        lang='de',
        folder='DE',
        cards_locale='de-DE',
        tag_prefix='DE-v',
        commit_title='Prerelease-Karten Update DE',
        new_cards_header='Neue Karten hinzugefügt:',
        no_new_cards='(keine neuen Karten)',
        replacement_map={'/神': '/Göttlich',
 '/暗': '/Finsternis',
 '/光': '/Licht',
 '/风': '/Wind',
 '/地': '/Erde',
 '/水': '/Wasser',
 '/炎': '/Feuer',
 '[怪兽': '[Monster',
 '[魔法': '[Zauber',
 '[陷阱': '[Falle',
 '幻想魔': 'Illusion',
 '魔法师': 'Hexer',
 '兽战士': 'Ungeheuer-Krieger',
 '爬虫类': 'Reptil',
 '念动力': 'Psi',
 '幻神兽': 'Göttliches Ungeheuer',
 '创造神': 'Schöpfergott',
 '电子界': 'Cyberse',
 '鸟兽': 'Geflügeltes Ungeheuer',
 '战士': 'Krieger',
 '天使': 'Fee',
 '恶魔': 'Unterweltler',
 '不死': 'Zombie',
 '机械': 'Maschine',
 '岩石': 'Fels',
 '植物': 'Pflanze',
 '昆虫': 'Insekt',
 '恐龙': 'Dinosaurier',
 '海龙': 'Seeschlange',
 '幻龙': 'Wyrm',
 '水': 'Aqua',
 '炎': 'Pyro',
 '雷': 'Donner',
 '龙': 'Drache',
 '兽': 'Ungeheuer',
 '鱼': 'Fisch',
 '通常': 'Normal',
 '效果': 'Effekt',
 '融合': 'Fusion',
 '仪式': 'Ritual',
 '灵魂': 'Spirit',
 '同盟': 'Union',
 '二重': 'Zwillings',
 '调整': 'Empfänger',
 '同调': 'Synchro',
 '衍生物': 'Spielmarke',
 '速攻': 'Schnell',
 '永续': 'Permanente',
 '装备': 'Ausrüstung',
 '场地': 'Spielfeld',
 '反击': 'Konterfallen',
 '反转': 'Flipp',
 '卡通': 'Toon',
 '超量': 'Xyz',
 '灵摆': 'Pendel',
 '特殊召唤': 'Spezialbeschwörung',
 '连接': 'Link'},
        patch_lang='de',
        pend_lang='de',
    ),
    'en': LangConfig(
        lang='en',
        folder='EN',
        cards_locale='en-US',
        tag_prefix='EN-v',
        commit_title='Prerelease Cards Update EN',
        new_cards_header='New cards added:',
        no_new_cards='(no new cards)',
        replacement_map={'/神': '/Divine',
 '/暗': '/Dark',
 '/光': '/Light',
 '/风': '/Wind',
 '/地': '/Earth',
 '/水': '/Water',
 '/炎': '/Fire',
 '[怪兽': '[Monster',
 '[魔法': '[Spell',
 '[陷阱': '[Trap',
 '幻想魔': 'Illusion',
 '魔法师': 'Spellcaster',
 '兽战士': 'Beast-Warrior',
 '爬虫类': 'Reptile',
 '念动力': 'Psychic',
 '幻神兽': 'Divine-Beast',
 '创造神': 'Creator God',
 '电子界': 'Cyberse',
 '鸟兽': 'Winged Beast',
 '战士': 'Warrior',
 '天使': 'Fairy',
 '恶魔': 'Fiend',
 '不死': 'Zombie',
 '机械': 'Machine',
 '岩石': 'Rock',
 '植物': 'Plant',
 '昆虫': 'Insect',
 '恐龙': 'Dinosaur',
 '海龙': 'Sea Serpent',
 '幻龙': 'Wyrm',
 '水': 'Aqua',
 '炎': 'Pyro',
 '雷': 'Thunder',
 '龙': 'Dragon',
 '兽': 'Beast',
 '鱼': 'Fish',
 '通常': 'Normal',
 '效果': 'Effect',
 '融合': 'Fusion',
 '仪式': 'Ritual',
 '灵魂': 'Spirit',
 '同盟': 'Union',
 '二重': 'Gemini',
 '调整': 'Tuner',
 '同调': 'Synchro',
 '衍生物': 'Token',
 '速攻': 'Quick-Play',
 '永续': 'Continuous',
 '装备': 'Equip',
 '场地': 'Field',
 '反击': 'Counter',
 '反转': 'Flip',
 '卡通': 'Toon',
 '超量': 'Xyz',
 '灵摆': 'Pendulum',
 '特殊召唤': 'Special Summon',
 '连接': 'Link'},
        patch_lang='en',
        pend_lang='en',
    ),
    'fr': LangConfig(
        lang='fr',
        folder='FR',
        cards_locale='fr-FR',
        tag_prefix='FR-v',
        commit_title='Cartes en avant-première FR',
        new_cards_header='Nouvelles cartes ajoutées:',
        no_new_cards='(aucune nouvelle carte)',
        replacement_map={'/神': '/Divin',
 '/暗': '/Ténébres',
 '/光': '/Lumiére',
 '/风': '/Vent',
 '/地': '/Terre',
 '/水': '/Eau',
 '/炎': '/Feu',
 '[怪兽': '[Monstre',
 '[魔法': '[Magie',
 '[陷阱': '[Piège',
 '幻想魔': 'Illusion',
 '魔法师': 'Magicien',
 '兽战士': 'Bête-Guerrier',
 '爬虫类': 'Reptile',
 '念动力': 'Psychique',
 '幻神兽': '\tBête Divine',
 '创造神': 'Dieu Créateur',
 '电子界': 'Cyberse',
 '鸟兽': 'Bête Ailée',
 '战士': 'Guerrier',
 '天使': 'Elfe',
 '恶魔': 'Démon',
 '不死': 'Zombie',
 '机械': 'Machine',
 '岩石': 'Rocher',
 '植物': 'Plante',
 '昆虫': 'Insecte',
 '恐龙': 'Dinosaure',
 '海龙': 'Serpent de Mer',
 '幻龙': 'Wyrm',
 '水': 'Aqua',
 '炎': 'Pyro',
 '雷': 'Tonnerre',
 '龙': 'Dragon',
 '兽': 'Bête',
 '鱼': 'Poisson',
 '通常': 'Normal',
 '效果': 'Effet',
 '融合': 'Fusion',
 '仪式': 'Rituel',
 '灵魂': 'Esprit',
 '同盟': 'Union',
 '二重': 'Gémeau',
 '调整': 'Syntoniseur',
 '同调': 'Synchro',
 '衍生物': 'Jeton',
 '速攻': 'Jeu-Rapide',
 '永续': 'Continu',
 '装备': 'Équipement',
 '场地': 'Terrain',
 '反击': 'Contre-Piège',
 '反转': 'Flip',
 '卡通': 'Toon',
 '超量': 'Xyz',
 '灵摆': 'Pendule',
 '特殊召唤': 'Invocation Spéciale',
 '连接': 'Lien'},
        patch_lang='fr',
        pend_lang='fr',
    ),
    'it': LangConfig(
        lang='it',
        folder='IT',
        cards_locale='it-IT',
        tag_prefix='IT-v',
        commit_title='Aggiornamento Pre-Rilascio IT',
        new_cards_header='Nuove carte aggiunte:',
        no_new_cards='(nessuna nuova carta)',
        replacement_map={'/神': '/Divino',
 '/暗': '/Oscurità',
 '/光': '/Luce',
 '/风': '/Vento',
 '/地': '/Terra',
 '/水': '/Acqua',
 '/炎': '/Fuoco',
 '[怪兽': '[Mostro',
 '[魔法': '[Magia',
 '[陷阱': '[Trappola',
 '幻想魔': 'Illusione',
 '魔法师': 'Incantatore',
 '兽战士': 'Guerriero-Bestia',
 '爬虫类': 'Rettile',
 '念动力': 'Psichico',
 '幻神兽': 'Bestia Divina',
 '创造神': 'Divinità Creatrice',
 '电子界': 'Cyberso',
 '鸟兽': 'Bestia Alata',
 '战士': 'Guerriero',
 '天使': 'Fata',
 '恶魔': 'Demone',
 '不死': 'Zombie',
 '机械': 'Macchina',
 '岩石': 'Roccia',
 '植物': 'Pianta',
 '昆虫': 'Insetto',
 '恐龙': 'Dinosauro',
 '海龙': 'Serpente Marino',
 '幻龙': 'Wyrm',
 '水': 'Acqua',
 '炎': 'Pyro',
 '雷': 'Tuono',
 '龙': 'Drago',
 '兽': 'Bestia',
 '鱼': 'Pesce',
 '通常': 'Normale',
 '效果': 'Effetto',
 '融合': 'Fusione',
 '仪式': 'Rituale',
 '灵魂': 'Spirit',
 '同盟': 'Union',
 '二重': 'Gemini',
 '调整': 'Tuner',
 '同调': 'Synchro',
 '衍生物': 'Token',
 '速攻': 'Rapida',
 '永续': 'Continua',
 '装备': 'Equipaggiamento',
 '场地': 'Terreno',
 '反击': 'Contro',
 '反转': 'Scoperta',
 '卡通': 'Toon',
 '超量': 'Xyz',
 '灵摆': 'Pendulum',
 '特殊召唤': 'Evocazione Speciale',
 '连接': 'Link'},
        patch_lang='it',
        pend_lang='it',
    ),
    'pt': LangConfig(
        lang='pt',
        folder='PT',
        cards_locale='pt-PT',
        tag_prefix='PT-v',
        commit_title='Atualização de Cartas Pré-Lançamento PT',
        new_cards_header='Novas cartas adicionadas:',
        no_new_cards='(sem cartas novas)',
        replacement_map={'/神': '/Divino',
 '/暗': '/Trevas',
 '/光': '/Luz',
 '/风': '/Vento',
 '/地': '/Terra',
 '/水': '/Água',
 '/炎': '/Fogo',
 '[怪兽': '[Monstro',
 '[魔法': '[Magia',
 '[陷阱': '[Armadilha',
 '幻想魔': 'Ilusão',
 '魔法师': 'Mago',
 '兽战士': 'Besta-Guerreira',
 '爬虫类': 'Réptil',
 '念动力': 'Psíquico',
 '幻神兽': 'Besta Divina',
 '创造神': 'Deus Criador',
 '电子界': 'Ciberso',
 '鸟兽': 'Besta Alada',
 '战士': 'Guerreiro',
 '天使': 'Fada',
 '恶魔': 'Demônio',
 '不死': 'Zumbi',
 '机械': 'Máquina',
 '岩石': 'Rocha',
 '植物': 'Planta',
 '昆虫': 'Inseto',
 '恐龙': 'Dinossauro',
 '海龙': 'Serpente Marinha',
 '幻龙': 'Wyrm',
 '水': 'Aqua',
 '炎': 'Piro',
 '雷': 'Trovão',
 '龙': 'Dragão',
 '兽': 'Besta',
 '鱼': 'Peixe',
 '通常': 'Normal',
 '效果': 'Efeito',
 '融合': 'Fusão',
 '仪式': 'Ritual',
 '灵魂': 'Espírito',
 '同盟': 'União',
 '二重': 'Gêmeos',
 '调整': 'Regulador',
 '同调': 'Sincro',
 '衍生物': 'Ficha',
 '速攻': 'Magia Rápida',
 '永续': 'Magia Contínuo',
 '装备': 'Magia de Equipamento',
 '场地': 'Magia de Campo',
 '反击': 'Armadilha de Resposta',
 '反转': 'Virar',
 '卡通': 'Toon',
 '超量': 'Xyz',
 '灵摆': 'Pêndulo',
 '特殊召唤': 'Invocação-Especial',
 '连接': 'Link'},
        patch_lang='pt',
        pend_lang='pt',
    ),
}

LANG_CONFIGS["th"] = LangConfig(
    lang="th",
    folder="TH",
    cards_locale=LANG_CONFIGS["en"].cards_locale,
    tag_prefix="TH-v",
    commit_title="Prerelease Cards Update TH",
    new_cards_header=LANG_CONFIGS["en"].new_cards_header,
    no_new_cards=LANG_CONFIGS["en"].no_new_cards,
    replacement_map=LANG_CONFIGS["en"].replacement_map,
    patch_lang=LANG_CONFIGS["en"].patch_lang,
    pend_lang=LANG_CONFIGS["en"].pend_lang,
)

CONFIG: LangConfig
OUTPUT_DIR: Path
BASE_DIR: Path
WORK_DIR: Path
TOOLS_DIR: Path
MAPPINGS_PATH: Path
YPK_URL: str
CARDS_CDB_URL: str
JSON_URL: str
REPLACEMENT_MAP: dict[str, str]


def env_value(name: str, default: str) -> str:
    return os.environ.get(f"TCG_AUTOPR_{name}", os.environ.get(f"{CONFIG.lang.upper()}_AUTOPR_{name}", default))


def configure(lang: str) -> None:
    global CONFIG, OUTPUT_DIR, BASE_DIR, WORK_DIR, TOOLS_DIR, MAPPINGS_PATH, YPK_URL, CARDS_CDB_URL, JSON_URL, REPLACEMENT_MAP
    CONFIG = LANG_CONFIGS[lang]
    OUTPUT_DIR = Path(env_value("OUTPUT_DIR", str(ROOT / CONFIG.folder))).resolve()
    BASE_DIR = Path(env_value("BASE_DIR", str(OUTPUT_DIR / "Base Files"))).resolve()
    WORK_DIR = Path(env_value("WORK_DIR", str(OUTPUT_DIR / "Workspace"))).resolve()
    TOOLS_DIR = Path(env_value("TOOLS_DIR", str(ROOT / "Tools"))).resolve()
    MAPPINGS_PATH = Path(env_value("MAPPINGS_PATH", str(ROOT / "Shared" / "Mappings.csv"))).resolve()
    YPK_URL = env_value("YPK_URL", YPK_URL_DEFAULT)
    CARDS_CDB_URL = env_value("CARDS_CDB_URL", CARDS_CDB_URL_TEMPLATE.format(locale=CONFIG.cards_locale))
    JSON_URL = env_value("JSON_URL", JSON_URL_DEFAULT)
    REPLACEMENT_MAP = CONFIG.replacement_map

CARD_TOKEN = "{CARD}"
CARD_ID_DELIM = "|"
SELF_TOKEN = "self"

STRINGS_ENTRY_RE = re.compile(r"^(?P<indent>\s*)!(?P<directive>[^\s#]+)\s+(?P<code>\S+)(?:\s+(?P<name>.*\S))?\s*$")
PLACEHOLDER_TOKEN_RE = re.compile(r"\(\s*\{CARD\}\s*\)|（\s*\{CARD\}\s*）|\{CARD\}")


def log(message: str) -> None:
    prefix = CONFIG.lang if "CONFIG" in globals() else "tcg"
    print(f"[{prefix}-autopr] {message}", flush=True)


def fail(message: str) -> None:
    prefix = CONFIG.lang if "CONFIG" in globals() else "tcg"
    print(f"[{prefix}-autopr] ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        fail(f"Missing {label}: {path}")
    return path


def download_file(url: str, target: Path) -> None:
    try:
        download_verified_file(url, target, user_agent=f"PreReleaseTranslations-{CONFIG.lang.upper()}-AutoPR/1.0", log_fn=log)
    except DownloadError as exc:
        fail(str(exc))


def fetch_json(url: str) -> list[dict]:
    try:
        return fetch_release_json(url, user_agent=f"PreReleaseTranslations-{CONFIG.lang.upper()}-AutoPR/1.0", log_fn=log)
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
    if CONFIG.lang == "en":
        # The standard workspace does not own Rush IDs in the shared mappings.
        # Its stale-card cleanup must not erase the Rush translator bindings.
        rush_rows = {card_id: row for card_id, row in load_locale_mappings_csv(path).items()
                     if 120000000 <= card_id < 130000000}
        save_locale_mappings_csv(path, {**rush_rows, **rows})


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

# ---- PENDULUM LAYOUT PATCH (CN prerelease -> locale headers) ----
PEND_LANG_LABELS = {
    "en": {"pendulum_scale": "Pendulum Scale", "pendulum_effect": "Pendulum Effect", "monster_effect": "Monster Effect"},
    "de": {"pendulum_scale": "Pendelbereich", "pendulum_effect": "Pendeleffekt", "monster_effect": "Monstereffekt"},
    "it": {"pendulum_scale": "Valore Pendulum", "pendulum_effect": "Effetto Pendulum", "monster_effect": "Effetto di un Mostro"},
    "fr": {"pendulum_scale": "Échelle Pendule", "pendulum_effect": "Effet Pendule", "monster_effect": "Effet de Monstre"},
    "pt": {"pendulum_scale": "Escala de Pêndulo", "pendulum_effect": "Efeito de Pêndulo", "monster_effect": "Efeito de Monstro"},
}
PEND_TCG_LANGS = {"en", "de", "it", "fr", "pt"}
PEND_SEP_TCG = "----------------------------------------"
_CN_PEND_SCALE_RE = re.compile(r"^\s*←\s*(\d+)\s*【(?:灵摆|靈擺)】\s*(\d+)\s*→\s*(?:\r?\n)?", re.UNICODE)
_CN_MONSTER_MARKERS = ("【怪兽效果】", "【怪獸效果】")


def normalize_newlines(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def format_pendulum_desc(desc: str, lang_key: str | None = None) -> str | None:
    lang_key = lang_key or CONFIG.pend_lang
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

    labels = PEND_LANG_LABELS.get(lang_key, PEND_LANG_LABELS["en"])
    pend_body = rest[:marker_index].strip("\n")
    mon_body = rest[marker_index + len(marker):].strip("\n")
    scale_value = left if left == right else f"{left}/{right}"

    output = [
        f"{labels['pendulum_scale']} = {scale_value}",
        f"[ {labels['pendulum_effect']} ]",
    ]
    if pend_body:
        output.append(pend_body)
    output.append(PEND_SEP_TCG)
    output.append(f"[ {labels['monster_effect']} ]")
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
            new_desc = format_pendulum_desc(desc, CONFIG.pend_lang)
            if not new_desc or new_desc == desc:
                continue
            conn.execute("UPDATE texts SET desc=? WHERE id=?", (new_desc, card_id))
            changed += 1
            if shown < report_limit:
                logger(f"[PEND] patched pendulum layout for id={card_id} name={name!r}")
                shown += 1
        conn.commit()
    finally:
        conn.close()
    logger(f"[PEND] completed: changed={changed}")


EN_DESC_REPLACEMENTS = [
    ("“", '"'), ("”", '"'),
    (" (1) ", " ① "), (" (2) ", " ② "), (" (3) ", " ③ "), (" (4) ", " ④ "),
    ("(1) ", "①："), ("(2) ", "②："), ("(3) ", "③："), ("(4) ", "④："),
    ("1st", "①"), ("2nd", "②"), ("3rd", "③"),
    ("(1)st", "①"), ("(2)nd", "②"), ("(3)rd", "③"),
    ("’", "'"),
]


def patch_en_desc(cdb_path: Path) -> None:
    if CONFIG.patch_lang != "en":
        return
    conn = sqlite3.connect(str(cdb_path))
    changed = 0
    try:
        rows = conn.execute("SELECT id, desc FROM texts WHERE desc IS NOT NULL AND desc != ''").fetchall()
        for card_id, desc in rows:
            new_desc = desc
            for source, target in EN_DESC_REPLACEMENTS:
                new_desc = new_desc.replace(source, target)
            if new_desc != desc:
                conn.execute("UPDATE texts SET desc=? WHERE id=?", (new_desc, card_id))
                changed += 1
        conn.commit()
    finally:
        conn.close()
    log(f"[EN] DESC punctuation/numbering patch changed rows={changed}")


def run_prompt_patcher(cdb_path: Path, cards_cdb: Path) -> None:
    prompt_patcher = require_file(TOOLS_DIR / "PromptPatcher.py", "Tools/PromptPatcher.py")
    sys.path.insert(0, str(prompt_patcher.parent))
    from PromptPatcher import export_remaining_cn, patch_cdb

    patch_cdb(str(cdb_path), lang=CONFIG.patch_lang, log_fn=log, report_limit=25)
    report_dir = WORK_DIR / "Reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    export_remaining_cn(str(cdb_path), str(report_dir / "remaining_cn.json"), log_fn=log)

    scan, present = scan_cdb_placeholders(cdb_path)
    mappings = merge_mappings(load_mappings_csv(MAPPINGS_PATH), scan, present)
    save_mappings_csv(MAPPINGS_PATH, mappings)
    apply_mappings_to_cdb(cdb_path, mappings, cards_cdb)
    patch_pendulum_layout_desc(cdb_path, log_fn=log, report_limit=25)
    patch_en_desc(cdb_path)


def autofill_source_text(cdb_path: Path, ypk_path: Path) -> None:
    if CONFIG.patch_lang not in {"en", "fr"}:
        return
    try:
        from card_text_autofill import autofill_cdb_from_pack_sources

        autofill_cdb_from_pack_sources(cdb_path, ypk_path, CONFIG.patch_lang, log_fn=log,
                                      refresh_existing=CONFIG.patch_lang == "en")
        patch_en_desc(cdb_path)
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
        copy_base_files_from_ypk(ypk, cards_cdb)
        merge_release_cdb(BASE_DIR / "test-release.cdb", WORK_DIR / "test-release.cdb")
        sync_test_strings(BASE_DIR / "test-strings.conf", WORK_DIR / "test-strings.conf")
        workspace_update = WORK_DIR / "test-update.cdb"
        if workspace_update.exists():
            workspace_update.unlink()
            log(f"Removed unused workspace {workspace_update.name}")
        # Self-name prompt mappings must see names fetched during this sync.
        autofill_source_text(WORK_DIR / "test-release.cdb", ypk)
        run_prompt_patcher(WORK_DIR / "test-release.cdb", cards_cdb)
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
        patch_en_desc(release_cdb)
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
