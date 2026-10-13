#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, shutil, subprocess, requests, time, json, zipfile, sqlite3
import logging, csv, hashlib, base64, sys, tempfile
from pathlib import Path
from PromptPatcher import patch_cdb, export_remaining_cn, lang_from_cards_cdb
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def get_requests_session():
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "PUT", "POST", "DELETE"]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def check_internet():
    try:
        requests.get("https://8.8.8.8", timeout=5)
        return True
    except:
        return False


def check_disk_space(path, required_mb=500):
    total, used, free = shutil.disk_usage(path)
    free_mb = free / (1024 * 1024)
    if free_mb < required_mb:
        log(f"Espacio insuficiente: {free_mb:.0f} MB libre, se requieren {required_mb} MB.")
        return False
    return True


def check_github_token():
    token = get_github_token()
    url_user = "https://api.github.com/user"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(url_user, headers=headers, timeout=10)
        if r.status_code != 200:
            log(f"Token inválido: {r.status_code} - {r.text}")
            return False
        log("Token de GitHub válido.")
        url_rate = "https://api.github.com/rate_limit"
        r2 = requests.get(url_rate, headers=headers, timeout=10)
        if r2.status_code == 200:
            scopes = r2.headers.get('X-OAuth-Scopes', '')
            log(f"Permisos del token: {scopes}")
        return True
    except Exception as e:
        log(f"Excepción al verificar token: {e}")
        return False


GITHUB_TOKEN_FILE = Path.home() / ".github_token"
GITHUB_OWNER = "ElderLich"
GITHUB_REPO = "Pre-Release-Translations"
GITHUB_BRANCH = "main"


# ---- CONFIG USUARIO ----
BASE_DIR    = Path("/storage/emulated/0/pre_updates/Prelanzadas/AutoPR")
RAW_DIR     = BASE_DIR / "raw2"
ES_DIR      = BASE_DIR / "ES"
REPO_DIR    = Path("/data/data/com.termux/files/home/Pre-Release-Translations")
REPO_SUBDIR = "ES"

CARDS_CDB   = "/storage/emulated/0/pre_updates/Prelanzadas/cards.cdb"

# Rutas remotas Standard (todas bajo ES/ o Shared/)
STANDARD_FILES = {
    "cdb":      f"{REPO_SUBDIR}/Workspace/test-release.cdb",
    "conf":     f"{REPO_SUBDIR}/Workspace/test-strings.conf",
    "mappings": "Shared/Mappings.csv",
}
STANDARD_REMOTE_SHA_FILE = BASE_DIR / ".last_standard_remote_sha"

TAG_PREFIX = "ES-v"
SUMMARY = "Actualización Pre-Release ES"


# ---- CONFIG RUSH ----
RUSH_DIR           = BASE_DIR / "Rush"
RUSH_RAW_DIR       = RUSH_DIR / "raw2"
RUSH_BASE_DIR      = RUSH_DIR / "Base Files"
RUSH_REPORTS_DIR   = RUSH_DIR / "Reports"
RUSH_MAPPINGS_CSV  = RUSH_DIR / "Mappings.csv"
RUSH_CDB_NAME      = "rush-pre-release.cdb"
RUSH_REPO_CDB_PATH = f"{REPO_SUBDIR}/Rush/Workspace/{RUSH_CDB_NAME}"
RUSH_REMOTE_SHA_FILE = RUSH_DIR / ".last_rush_remote_sha"
RUSH_REMOTE_REPORT_PATH = f"{REPO_SUBDIR}/Rush/report.json"

for _d in (RUSH_RAW_DIR, RUSH_BASE_DIR, RUSH_REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ---- LOGGER ----
from logging.handlers import RotatingFileHandler
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
    handlers=[
        RotatingFileHandler(
            BASE_DIR / "autopr.log",
            maxBytes=1_000_000,   # 1 MB
            backupCount=3,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("AUTOPR").info


# ---- Cargar configuración externa (si existe) ----
CONFIG_FILE = BASE_DIR / "config.json"
if CONFIG_FILE.exists():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
            GITHUB_OWNER = cfg.get("github_owner", GITHUB_OWNER)
            GITHUB_REPO = cfg.get("github_repo", GITHUB_REPO)
            GITHUB_BRANCH = cfg.get("branch", GITHUB_BRANCH)
            TAG_PREFIX = cfg.get("tag_prefix", TAG_PREFIX)
            SUMMARY = cfg.get("summary", SUMMARY)
        log("Configuración externa cargada.")
    except Exception as e:
        log(f"Error al cargar config.json: {e}")


# ---- test-strings.conf: parseo y sincronización ----
_STRINGS_ENTRY_RE = re.compile(
    r"^(?P<indent>\s*)!(?P<directive>[^\s#]+)\s+(?P<code>\S+)(?:\s+(?P<name>.*\S))?\s*$"
)
_STRINGS_TEXT = {
    "synced": "[Strings] test-strings.conf sincronizado ->",
    "managed": "[Strings] Entradas gestionadas en la fuente CN:",
    "new_blank": "[Strings] Nuevas entradas vacías que necesitan traducción:",
    "removed": "[Strings] Entradas eliminadas que ya no existen en CN:",
    "normalized": "[Strings] Nombres conservados normalizados:",
    "dup_local": "[Strings] Aviso: se detectaron claves locales duplicadas:",
    "dup_cn": "[Strings] Aviso: se detectaron claves CN duplicadas:",
    "pending": "[Strings] Entradas que todavía no tienen traducción:",
    "all_done": "[Strings] Todas las entradas gestionadas tienen traducción actualmente.",
    "dup_release": "[Strings] Las entradas duplicadas deben corregirse antes del release:",
    "malformed_release": "[Strings] Las líneas mal formadas deben corregirse antes del release:",
    "missing_release": "[Strings] Los nombres traducidos faltantes deben completarse antes del release:",
    "release_ok": "[Strings] La validación de release de test-strings.conf fue correcta.",
    "missing_file": "No se encontró test-strings.conf:",
    "not_ready": "test-strings.conf no está listo para el release"
}
_STRINGS_SHOWING = 'mostrando hasta'
_STRINGS_LINE_WORD = 'linea'


def _strings_sample(items, report_limit):
    return ", ".join(items[:report_limit])


def _strings_sample_suffix(items, report_limit):
    if not items:
        return ""
    return f" ({_STRINGS_SHOWING} {report_limit}) -> {_strings_sample(items, report_limit)}"


def _parse_test_strings_entry(raw_line, lineno):
    match = _STRINGS_ENTRY_RE.match(raw_line)
    if not match:
        return None
    directive = match.group("directive")
    code = match.group("code")
    name = (match.group("name") or "").strip()
    return {
        "directive": directive,
        "code": code,
        "name": name,
        "key": f"!{directive.lower()} {code.lower()}",
        "lineno": lineno,
        "raw_line": raw_line,
    }


def _collect_test_strings_entries(text):
    entries = {}
    duplicates = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        entry = _parse_test_strings_entry(raw_line, lineno)
        if entry is None:
            continue
        if entry["key"] in entries:
            duplicates.append((entry["key"], lineno))
        entries[entry["key"]] = entry
    return entries, duplicates


def _format_test_strings_entry(directive, code, name):
    name = (name or "").strip()
    if name:
        return f"!{directive} {code} {name}"
    return f"!{directive} {code}"


def sync_test_strings_conf(source_bytes, local_path, *, log_fn=None, report_limit=25, normalize_name=None):
    """Fusiona el test-strings.conf upstream (bytes) con el local.
    Preserva nombres traducidos; añade entradas nuevas en blanco; elimina retiradas."""
    if log_fn is None:
        log_fn = log

    source_text = source_bytes.decode("utf-8-sig")
    local_path = Path(local_path)
    local_text = local_path.read_text(encoding="utf-8-sig") if local_path.exists() else ""

    local_entries, duplicate_local = _collect_test_strings_entries(local_text)
    source_entries, duplicate_source = _collect_test_strings_entries(source_text)

    output_lines = []
    new_blank_keys = []
    pending_keys = []
    normalized_keys = []

    for lineno, raw_line in enumerate(source_text.splitlines(), start=1):
        entry = _parse_test_strings_entry(raw_line, lineno)
        if entry is None:
            output_lines.append(raw_line)
            continue

        local_entry = local_entries.get(entry["key"])
        local_name = local_entry["name"] if local_entry else ""

        if local_name and normalize_name is not None:
            normalized_name = normalize_name(entry["directive"], entry["code"], local_name)
            if normalized_name != local_name:
                normalized_keys.append(entry["key"])
            local_name = normalized_name

        if not local_name:
            pending_keys.append(entry["key"])
            if local_entry is None:
                new_blank_keys.append(entry["key"])

        output_lines.append(_format_test_strings_entry(entry["directive"], entry["code"], local_name))

    removed_keys = sorted(set(local_entries) - set(source_entries))

    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(output_lines))
        if output_lines:
            f.write("\n")

    log_fn(f"{_STRINGS_TEXT['synced']} {local_path}")
    log_fn(f"{_STRINGS_TEXT['managed']} {len(source_entries)}")
    if new_blank_keys:
        log_fn(f"{_STRINGS_TEXT['new_blank']} {len(new_blank_keys)}{_strings_sample_suffix(new_blank_keys, report_limit)}")
    if removed_keys:
        log_fn(f"{_STRINGS_TEXT['removed']} {len(removed_keys)}{_strings_sample_suffix(removed_keys, report_limit)}")
    if normalized_keys:
        log_fn(f"{_STRINGS_TEXT['normalized']} {len(normalized_keys)}{_strings_sample_suffix(normalized_keys, report_limit)}")
    if duplicate_local:
        dup_keys = [key for key, _ in duplicate_local]
        log_fn(f"{_STRINGS_TEXT['dup_local']} {len(duplicate_local)}{_strings_sample_suffix(dup_keys, report_limit)}")
    if duplicate_source:
        dup_keys = [key for key, _ in duplicate_source]
        log_fn(f"{_STRINGS_TEXT['dup_cn']} {len(duplicate_source)}{_strings_sample_suffix(dup_keys, report_limit)}")
    if pending_keys:
        log_fn(f"{_STRINGS_TEXT['pending']} {len(pending_keys)}{_strings_sample_suffix(pending_keys, report_limit)}")
    else:
        log_fn(_STRINGS_TEXT['all_done'])

    return {
        "path": str(local_path),
        "total_source_entries": len(source_entries),
        "new_blank_keys": new_blank_keys,
        "pending_keys": pending_keys,
        "removed_keys": removed_keys,
        "normalized_keys": normalized_keys,
        "duplicate_local": duplicate_local,
        "duplicate_source": duplicate_source,
    }


def ensure_test_strings_ready_for_release(path, *, log_fn=None, report_limit=25):
    if log_fn is None:
        log_fn = log
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{_STRINGS_TEXT['missing_file']} {path}")

    text = path.read_text(encoding="utf-8-sig")
    duplicates = []
    missing = []
    malformed = []
    seen = set()

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entry = _parse_test_strings_entry(raw_line, lineno)
        if entry is None:
            if stripped.startswith("!"):
                malformed.append((lineno, raw_line))
            continue
        if entry["key"] in seen:
            duplicates.append((entry["key"], lineno))
        seen.add(entry["key"])
        if not entry["name"]:
            missing.append(entry)

    if duplicates:
        dup_keys = [key for key, _ in duplicates]
        log_fn(f"{_STRINGS_TEXT['dup_release']} {len(duplicates)}{_strings_sample_suffix(dup_keys, report_limit)}")
    if malformed:
        malformed_lines = [f"{_STRINGS_LINE_WORD} {lineno}: {line.strip()}" for lineno, line in malformed[:report_limit]]
        log_fn(f"{_STRINGS_TEXT['malformed_release']} {len(malformed)}{_strings_sample_suffix(malformed_lines, report_limit)}")
    if missing:
        missing_keys = [entry["key"] for entry in missing]
        log_fn(f"{_STRINGS_TEXT['missing_release']} {len(missing)}{_strings_sample_suffix(missing_keys, report_limit)}")

    if duplicates or malformed or missing:
        raise RuntimeError(_STRINGS_TEXT['not_ready'])
    log_fn(_STRINGS_TEXT['release_ok'])


# ---- {CARD} MAPPINGS ----
SCRIPT_DIR     = Path(__file__).resolve().parent
MAPPINGS_CSV   = SCRIPT_DIR / "Mappings.csv"
CARD_TOKEN     = "{CARD}"
CARD_ID_DELIM  = "|"
SELF_TOKEN     = "self"

_PLACEHOLDER_TOKEN_RE = re.compile(r'\(\s*\{CARD\}\s*\)|（\s*\{CARD\}\s*）|\{CARD\}')


def _normalize_nl(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def _clean_cell(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() == "nan":
        return ""
    return s


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _placeholder_marker(n: int) -> str:
    if n <= 0:
        return ""
    if n == 1:
        return CARD_TOKEN
    return CARD_ID_DELIM.join([CARD_TOKEN] * n)


def _count_placeholders_in_text(s: str) -> int:
    if not s:
        return 0
    return str(s).count(CARD_TOKEN)


def _parse_card_ids(cell: str):
    s = _clean_cell(cell)
    if not s:
        return []
    if CARD_TOKEN in s:
        return []
    parts = [p.strip() for p in s.split(CARD_ID_DELIM)] if CARD_ID_DELIM in s else re.split(r"[,\s;]+", s.strip())
    out = []
    for p in parts:
        if not p:
            continue
        if p.lower() == SELF_TOKEN:
            out.append(SELF_TOKEN)
        elif re.fullmatch(r"\d+", p):
            out.append(p)
    return out


def _is_ocg_cards_cdb(cards_cdb_path: str) -> bool:
    s = str(cards_cdb_path).replace("\\", "/").lower()
    return ("ja-jp" in s) or ("ko-kr" in s)


def _replace_placeholders(template: str, names, quote_open="\"", quote_close="\"") -> str:
    idx = 0
    def repl(m):
        nonlocal idx
        if idx >= len(names):
            return m.group(0)
        nm = names[idx]
        idx += 1
        tok = m.group(0)
        if tok.startswith("("):
            return f"({nm})"
        if tok.startswith("（"):
            return f"（{nm}）"
        return f"{quote_open}{nm}{quote_close}"
    return _PLACEHOLDER_TOKEN_RE.sub(repl, str(template))


def _parse_mappings_text(text: str) -> dict:
    rows = {}
    reader = csv.DictReader(text.splitlines())
    for row in reader:
        try:
            cid = int(str(row.get("id", "")).strip())
        except Exception:
            continue
        out = {"id": cid, "name": _clean_cell(row.get("name", ""))}
        for i in range(1, 17):
            out[f"str{i}"] = _clean_cell(row.get(f"str{i}", ""))
        rows[cid] = out
    return rows


def load_mappings_csv(csv_path: Path):
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return {}
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except Exception:
        return {}
    return _parse_mappings_text(text)


def save_mappings_csv(csv_path: Path, rows_by_id: dict):
    fieldnames = ["id", "name"] + [f"str{i}" for i in range(1, 17)]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for cid in sorted(rows_by_id.keys()):
            r = rows_by_id[cid]
            out = {k: "" for k in fieldnames}
            out["id"] = cid
            out["name"] = r.get("name", "")
            for i in range(1, 17):
                out[f"str{i}"] = _clean_cell(r.get(f"str{i}", ""))
            w.writerow(out)


def _fuse_mappings(remote_rows: dict, local_path: Path) -> dict:
    """Fusiona Mappings remoto con local. Local gana en str no vacíos.
    Añade filas nuevas del remoto. Conserva filas solo locales."""
    local_path = Path(local_path)
    local_rows = load_mappings_csv(local_path)
    fused = {}
    for cid in set(local_rows) | set(remote_rows):
        lr = local_rows.get(cid)
        rr = remote_rows.get(cid)
        if lr and rr:
            merged = {"id": cid, "name": lr.get("name") or rr.get("name", "")}
            for i in range(1, 17):
                col = f"str{i}"
                lv = _clean_cell(lr.get(col, ""))
                rv = _clean_cell(rr.get(col, ""))
                merged[col] = lv if lv else rv
            fused[cid] = merged
        elif lr:
            fused[cid] = lr
        else:
            fused[cid] = rr
    save_mappings_csv(local_path, fused)
    new_ids = set(remote_rows) - set(local_rows)
    log(f"   Mappings fusionado: {len(fused)} filas ({len(new_ids)} nuevas del remoto)")
    return fused


def scan_cdb_placeholders(cdb_path: Path):
    conn = sqlite3.connect(str(cdb_path))
    cur = conn.cursor()
    cols = ["id", "name"] + [f"str{i}" for i in range(1, 17)]
    cur.execute(f"SELECT {', '.join(cols)} FROM texts")
    scan = {}
    present_ids = set()
    for row in cur.fetchall():
        cid = int(row[0])
        present_ids.add(cid)
        name = row[1] or ""
        need = {}
        for i in range(1, 17):
            n = _count_placeholders_in_text(row[1+i])
            if n > 0:
                need[f"str{i}"] = n
        if need:
            scan[cid] = {"id": cid, "name": name, "need": need}
    conn.close()
    return scan, present_ids


def merge_mappings(existing_rows: dict, scan_rows: dict, present_ids=None, log_fn=None, report_limit=25):
    if log_fn is None:
        log_fn = log

    added_rows, removed_rows, added_slots, removed_slots, mismatches, copied_moves = [], [], [], [], [], []
    if present_ids is None:
        merged = dict(existing_rows)
    else:
        merged = {}
        for cid, row in existing_rows.items():
            if cid in present_ids:
                merged[cid] = row
            else:
                removed_rows.append(cid)

    for cid, info in scan_rows.items():
        name_now = info.get("name", "")
        need = info.get("need", {})

        if cid not in merged:
            merged[cid] = {"id": cid, "name": name_now}
            for i in range(1, 17):
                merged[cid][f"str{i}"] = ""
            added_rows.append(cid)
        else:
            if name_now:
                merged[cid]["name"] = name_now

        row = merged[cid]

        for i in range(1, 17):
            col = f"str{i}"
            needed_now = col in need
            had_before = not _is_blank(_clean_cell(row.get(col, "")))
            if needed_now and not had_before:
                added_slots.append((cid, col))
            if (not needed_now) and had_before:
                removed_slots.append((cid, col))

        for col, n in need.items():
            cur_cell = _clean_cell(row.get(col, ""))
            ids = _parse_card_ids(cur_cell)
            if ids:
                if len(ids) != n:
                    mismatches.append((cid, col, n, len(ids), cur_cell))
                continue
            marker = _placeholder_marker(n)
            if not cur_cell or (CARD_TOKEN in cur_cell and cur_cell != marker):
                row[col] = marker

        needed_cols = sorted(list(need.keys()))
        numeric_cols = []
        for i in range(1, 17):
            col = f"str{i}"
            if _parse_card_ids(_clean_cell(row.get(col, ""))):
                numeric_cols.append(col)

        if len(needed_cols) == 1 and len(numeric_cols) == 1:
            target = needed_cols[0]
            src = numeric_cols[0]
            if target != src and (_is_blank(_clean_cell(row.get(target, ""))) or CARD_TOKEN in _clean_cell(row.get(target, ""))):
                row[target] = row[src]
                copied_moves.append((cid, src, target))

    if added_rows:
        log_fn(f"[Mappings] Nuevas filas: {len(added_rows)} (muestra {added_rows[:report_limit]})")
    if removed_rows:
        log_fn(f"[Mappings] Filas eliminadas: {len(removed_rows)} (muestra {removed_rows[:report_limit]})")
    if added_slots:
        log_fn(f"[Mappings] Nuevos slots: {len(added_slots)} (muestra {added_slots[:report_limit]})")
    if removed_slots:
        log_fn(f"[Mappings] Slots ausentes (se conservan): {len(removed_slots)}")
    if copied_moves:
        log_fn(f"[Mappings] IDs copiados a nueva columna: {len(copied_moves)}")
    if mismatches:
        log_fn(f"[Mappings] WARNING discrepancias: {len(mismatches)}")
        for cid, col, need_n, got_n, cell in mismatches[:report_limit]:
            log_fn(f"  - id={cid} {col}: necesita {need_n}, CSV tiene {got_n}: '{cell}'")

    return merged


def apply_mappings_to_cdb(cdb_path: Path, rows_by_id: dict, cards_cdb_path: str, log_fn=None, report_limit=25):
    if log_fn is None:
        log_fn = log

    conn = sqlite3.connect(str(cdb_path))
    cur = conn.cursor()

    conn_cards = sqlite3.connect(str(cards_cdb_path))
    cur_cards = conn_cards.cursor()
    name_cache = {}

    is_ocg = _is_ocg_cards_cdb(cards_cdb_path)
    q_open, q_close = ("「", "」") if is_ocg else ("\"", "\"")

    def get_name(card_id_str: str):
        if card_id_str in name_cache:
            return name_cache[card_id_str]
        cur_cards.execute("SELECT name FROM texts WHERE id=?", (int(card_id_str),))
        r = cur_cards.fetchone()
        nm = r[0] if r and r[0] is not None else None
        name_cache[card_id_str] = nm
        return nm

    cols = ["id", "name"] + [f"str{i}" for i in range(1, 17)]
    cur.execute(f"SELECT {', '.join(cols)} FROM texts")
    rows = cur.fetchall()

    set_clause = ", ".join([f"str{i}=?" for i in range(1, 17)])
    upd_sql = f"UPDATE texts SET {set_clause} WHERE id=?"

    updated_rows = 0
    skipped_cells = 0
    sample = []

    log(f"[Mappings] Procesando {len(rows)} cartas. CSV tiene {len(rows_by_id)} entradas.")

    for row in rows:
        cid = int(row[0])
        self_name = row[1] or ""
        mapping = rows_by_id.get(cid)
        if not mapping:
            continue

        new_vals = list(row[2:])
        changed = False

        if not self_name:
            self_name = _clean_cell(mapping.get("name", ""))

        for i in range(1, 17):
            s = new_vals[i-1]
            if not s or CARD_TOKEN not in str(s):
                continue

            need_n = _count_placeholders_in_text(str(s))
            cell = _clean_cell(mapping.get(f"str{i}", ""))
            tokens = _parse_card_ids(cell)

            if not tokens or len(tokens) != need_n:
                if tokens:
                    skipped_cells += 1
                continue

            names = []
            ok = True
            for tok in tokens:
                if tok.lower() == SELF_TOKEN:
                    nm = self_name
                else:
                    nm = get_name(tok)
                if not nm:
                    ok = False
                    break
                names.append(nm)

            if not ok:
                skipped_cells += 1
                continue

            replaced = _replace_placeholders(str(s), names, quote_open=q_open, quote_close=q_close)
            if replaced != str(s):
                new_vals[i-1] = replaced
                changed = True

        if changed:
            cur.execute(upd_sql, (*new_vals, cid))
            updated_rows += 1
            if len(sample) < report_limit:
                sample.append(cid)

    conn.commit()
    conn.close()
    conn_cards.close()
    log_fn(f"[Mappings] Aplicado: updated_rows={updated_rows}, skipped_cells={skipped_cells}, sample={sample}")

# =========================================================
# ======  EXPORT/IMPORT DE TEXTOS SIN TRADUCIR (CJK)  =====
# =========================================================
_CJK_RE = re.compile(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]')

def _has_cjk(text):
    if not text:
        return False
    return bool(_CJK_RE.search(str(text)))


def _collect_cjk_fields(cdb_path, fields):
    conn = sqlite3.connect(str(cdb_path))
    cur = conn.cursor()
    cur.execute(f"SELECT id, {', '.join(fields)} FROM texts")
    items = []
    for row in cur.fetchall():
        cid = row[0]
        for i, val in enumerate(row[1:]):
            if _has_cjk(val):
                items.append((cid, fields[i], val))
    conn.close()
    return items


def export_untranslated_str(cdb_path, output_path, *, log_fn=None, include_desc=False):
    """Exporta a TXT los campos con caracteres CJK.
    include_desc=True → incluye también 'name' y 'desc' (útil para Standard).
    include_desc=False → solo str1..str16."""
    if log_fn is None:
        log_fn = log
    cdb_path = Path(cdb_path)
    output_path = Path(output_path)

    if not cdb_path.exists():
        log_fn(f"❌ No existe el CDB: {cdb_path}")
        return 0

    fields = (["name", "desc"] if include_desc else []) + [f"str{i}" for i in range(1, 17)]
    items = _collect_cjk_fields(cdb_path, fields)

    if not items:
        log_fn("✅ No hay campos con texto en chino pendientes de traducción.")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# AutoPR - Textos sin traducir (contienen caracteres CJK)\n")
        f.write(f"# Generado: {ts}\n")
        f.write(f"# CDB:      {cdb_path}\n")
        f.write(f"# Total:    {len(items)} campos pendientes\n")
        f.write("#\n")
        f.write("# INSTRUCCIONES:\n")
        f.write("#   1. Edita SOLO el texto después del segundo TAB.\n")
        f.write("#   2. NO cambies el ID ni el nombre del campo (str1, str2, ...).\n")
        f.write("#   3. No añadas TABs dentro de tu traducción (usa espacios).\n")
        f.write("#   4. Puedes eliminar líneas para saltar esas traducciones.\n")
        f.write("#   5. Las líneas que comienzan con # son comentarios.\n")
        f.write("#   6. Al terminar, usa 'Importar traducciones (desde TXT)'.\n")
        f.write("#\n")
        f.write("# Formato: ID<TAB>campo<TAB>texto_original\n")
        f.write("#\n")
        for cid, field, text in items:
            one_line = (str(text)
                        .replace("\r\n", " ").replace("\r", " ")
                        .replace("\n", " ").replace("\t", " "))
            f.write(f"{cid}\t{field}\t{one_line}\n")

    log_fn(f"📄 Exportado: {output_path}")
    log_fn(f"   Total: {len(items)} campos con CJK")
    counts = {}
    for _, fld, _ in items:
        counts[fld] = counts.get(fld, 0) + 1
    log_fn("   Desglose: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return len(items)


def import_str_translations(cdb_path, input_path, *, log_fn=None):
    """Importa traducciones desde el TXT. Solo aplica filas con cambios reales."""
    if log_fn is None:
        log_fn = log
    cdb_path = Path(cdb_path)
    input_path = Path(input_path)

    if not cdb_path.exists():
        log_fn(f"❌ No existe el CDB: {cdb_path}")
        return 0
    if not input_path.exists():
        log_fn(f"❌ No existe el TXT: {input_path}")
        log_fn("   Primero exporta con 'Exportar str sin traducir'.")
        return 0

    updates = []
    errors = []
    with open(input_path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip("\n").rstrip("\r")
            if not line or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t", 2)
            if len(parts) < 3:
                errors.append((lineno, "faltan columnas (esperado ID<TAB>campo<TAB>texto)"))
                continue
            try:
                cid = int(parts[0].strip())
            except ValueError:
                errors.append((lineno, f"ID inválido: {parts[0]!r}"))
                continue
            field = parts[1].strip().lower()
            if not re.fullmatch(r"(name|desc|str(1[0-6]|[1-9]))", field):
                errors.append((lineno, f"campo inválido: {parts[1].strip()!r}"))
                continue
            new_text = parts[2]
            if not new_text:
                errors.append((lineno, "traducción vacía (ignorada)"))
                continue
            updates.append((cid, field, new_text, lineno))

    if errors:
        log_fn(f"⚠️  {len(errors)} línea(s) con problemas (ignoradas):")
        for lineno, msg in errors[:10]:
            log_fn(f"   L{lineno}: {msg}")
        if len(errors) > 10:
            log_fn(f"   ... y {len(errors) - 10} más")

    if not updates:
        log_fn("No hay traducciones nuevas para importar.")
        return 0

    conn = sqlite3.connect(str(cdb_path))
    cur = conn.cursor()
    applied = 0
    skipped_unchanged = 0
    skipped_missing = 0
    skipped_cjk = 0
    for cid, field, new_text, lineno in updates:
        cur.execute(f"SELECT {field} FROM texts WHERE id=?", (cid,))
        row = cur.fetchone()
        if row is None:
            skipped_missing += 1
            continue
        current = row[0] or ""
        if current == new_text:
            skipped_unchanged += 1
            continue
        if _has_cjk(new_text):
            skipped_cjk += 1
            continue
        cur.execute(f"UPDATE texts SET {field}=? WHERE id=?", (new_text, cid))
        applied += 1
    conn.commit()
    conn.close()

    log_fn("✅ Importación completada:")
    log_fn(f"   Aplicados:        {applied}")
    log_fn(f"   Sin cambios:      {skipped_unchanged}")
    if skipped_missing:
        log_fn(f"   ID no encontrado: {skipped_missing}")
    if skipped_cjk:
        log_fn(f"   Aún con CJK:      {skipped_cjk} (probablemente sin traducir)")

    # Backup del TXT solo si se aplicó algo y aún existe
    if applied > 0 and input_path.exists():
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = input_path.with_name(f"{input_path.stem}.applied_{ts}{input_path.suffix}")
        try:
            input_path.rename(backup)
            log_fn(f"   TXT respaldado: {backup.name}")
        except Exception as e:
            log_fn(f"   ⚠️  No se pudo respaldar el TXT: {e}")

    return applied

# ---- PENDULUM LAYOUT PATCH ----
PEND_LANG_LABELS = {
    "es": {"pendulum_scale":"Escala de Péndulo","pendulum_effect":"Efecto de Péndulo","monster_effect":"Efecto de Monstruo"},
}
PEND_TCG_LANGS = {"en","de","it","fr","pt"}
PEND_SEP_ES    = "━━━━━━━━━━━━━━━━━━━━━━━"

_CN_PEND_SCALE_RE = re.compile(r"^\s*←\s*(\d+)\s*【(?:灵摆|靈擺)】\s*(\d+)\s*→\s*(?:\r?\n)?", re.UNICODE)
_CN_MONSTER_MARKERS = ("【怪兽效果】", "【怪獸效果】")


def _normalize_pend_lang(lang_key: str, cards_cdb_path: str) -> str:
    return "es"


def _fmt_pend_scale_line(lang_key, left, right):
    return f"←◊{left} Escala {right}◊→"


def _fmt_pend_headers(lang_key):
    labels = PEND_LANG_LABELS["es"]
    return f"【{labels['pendulum_effect']}】", PEND_SEP_ES, f"【{labels['monster_effect']}】"


def _format_pendulum_desc(desc, lang_key):
    s = _normalize_nl(desc)
    m = _CN_PEND_SCALE_RE.match(s)
    if not m:
        return None
    left, right = m.group(1), m.group(2)
    rest = s[m.end():]
    idx = -1
    marker = None
    for mk in _CN_MONSTER_MARKERS:
        idx = rest.find(mk)
        if idx != -1:
            marker = mk
            break
    if idx == -1:
        return None
    pend_body = rest[:idx].strip("\n")
    mon_body  = rest[idx + len(marker):].strip("\n")
    scale_line = _fmt_pend_scale_line(lang_key, left, right)
    pend_head, sep, mon_head = _fmt_pend_headers(lang_key)
    out = [scale_line, pend_head]
    if pend_body:
        out.append(pend_body)
    if sep:
        out.append(sep)
    out.append(mon_head)
    if mon_body:
        out.append(mon_body)
    return "\n".join(out).strip("\n")


def patch_pendulum_layout_desc(cdb_path: Path, lang_key: str, log_fn=None, report_limit=25):
    if log_fn is None:
        log_fn = log
    conn = sqlite3.connect(str(cdb_path))
    cur = conn.cursor()
    cur.execute("SELECT id, name, desc FROM texts WHERE desc LIKE '%【灵摆】%' OR desc LIKE '%【靈擺】%'")
    rows = cur.fetchall()
    changed = 0
    shown = 0
    for cid, name, desc in rows:
        if not desc:
            continue
        new_desc = _format_pendulum_desc(desc, lang_key)
        if not new_desc or new_desc == desc:
            continue
        cur.execute("UPDATE texts SET desc=? WHERE id=?", (new_desc, cid))
        changed += 1
        if shown < report_limit:
            log_fn(f"[PEND] Diseño de Péndulo parcheado para id={cid} name={name!r}")
            shown += 1
    conn.commit()
    conn.close()
    log_fn(f"[PEND] Listo. Filas de desc de Péndulo parcheadas: {changed}")


# =========================================================
# ==============  GITHUB API (usado por Standard y Rush) ==
# =========================================================
def get_github_token():
    if not GITHUB_TOKEN_FILE.exists():
        raise FileNotFoundError(f"No se encuentra el token en {GITHUB_TOKEN_FILE}")
    return GITHUB_TOKEN_FILE.read_text().strip()


_session = None


def github_api_request(method, endpoint, data=None):
    global _session
    if _session is None:
        _session = get_requests_session()
    token = get_github_token()
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    if data:
        headers["Content-Type"] = "application/json"
    response = _session.request(method, url, headers=headers, json=data)
    if response.status_code not in [200, 201, 204]:
        raise Exception(f"API error {response.status_code}: {response.text}")
    return response.json() if response.content else None


def get_remote_file_sha(path):
    try:
        result = github_api_request("GET", f"contents/{path}")
        return result.get("sha")
    except Exception:
        return None


def download_remote_file(path):
    """Descarga un archivo del repo (bytes). Soporta archivos > 1MB vía download_url."""
    result = github_api_request("GET", f"contents/{path}")
    content_b64 = result.get("content", "")
    if content_b64:
        return base64.b64decode(content_b64)
    # Archivos grandes (>1MB): usar download_url
    dl_url = result.get("download_url")
    if dl_url:
        headers = {"Authorization": f"Bearer {get_github_token()}"}
        r = requests.get(dl_url, headers=headers, timeout=120)
        r.raise_for_status()
        return r.content
    raise RuntimeError(f"Respuesta sin contenido descargable: {path}")


def upload_file_via_api(local_path, remote_path, commit_message):
    with open(local_path, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")
    sha = get_remote_file_sha(remote_path)
    data = {
        "message": commit_message,
        "content": content,
        "branch": GITHUB_BRANCH
    }
    if sha:
        data["sha"] = sha
    github_api_request("PUT", f"contents/{remote_path}", data)
    log(f"Archivo {remote_path} subido/actualizado correctamente.")


def get_remote_json(path):
    try:
        result = github_api_request("GET", f"contents/{path}")
        content = base64.b64decode(result["content"]).decode("utf-8")
        return json.loads(content)
    except Exception as e:
        log(f"No se pudo obtener {path}: {e}")
        return None


def create_tag(tag_name, commit_sha):
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/refs/tags/{tag_name}"
    headers = {"Authorization": f"Bearer {get_github_token()}", "Accept": "application/vnd.github.v3+json"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        log(f"El tag {tag_name} ya existe. No se creará uno nuevo.")
        return
    elif response.status_code != 404:
        log(f"Error al verificar tag: {response.status_code}")
        return
    data = {"ref": f"refs/tags/{tag_name}", "sha": commit_sha}
    github_api_request("POST", "git/refs", data)
    log(f"Tag {tag_name} creado en GitHub.")


def next_tag():
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/tags"
    headers = {"Authorization": f"Bearer {get_github_token()}", "Accept": "application/vnd.github.v3+json"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        tags = response.json()
        max_num = 0
        for tag_info in tags:
            tag_name = tag_info['name']
            if tag_name.startswith(TAG_PREFIX):
                try:
                    num = int(tag_name[len(TAG_PREFIX):])
                    if num > max_num:
                        max_num = num
                except ValueError:
                    pass
        return f"{TAG_PREFIX}{max_num + 1}"
    except Exception as e:
        log(f"Error al obtener tags de GitHub: {e}")
        while True:
            try:
                user_input = input("Introduce el número de versión (ej. 5): ").strip()
                if user_input.isdigit():
                    return f"{TAG_PREFIX}{user_input}"
                log("Número inválido. Introduce solo dígitos.")
            except KeyboardInterrupt:
                raise
            except Exception:
                pass


# =========================================================
# ====================  RUSH  =============================
# =========================================================
def _schema_from_cdb(path):
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    datas = [r[1] for r in cur.execute("PRAGMA table_info(datas)")]
    texts = [r[1] for r in cur.execute("PRAGMA table_info(texts)")]
    conn.close()
    return datas, texts


def _read_cdb_from_path(path, optional=False):
    path = Path(path)
    if optional and not path.is_file():
        return {}, {}
    if not path.is_file():
        raise FileNotFoundError(f"CDB no encontrado: {path}")
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    datas = {row[0]: tuple(row) for row in cur.execute("SELECT * FROM datas")}
    texts = {row[0]: tuple(row) for row in cur.execute("SELECT * FROM texts")}
    conn.close()
    return datas, texts


def _write_cdb_from_dicts(path, datas, texts, schema_datas, schema_texts):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(str(tmp))
    cur = conn.cursor()
    cur.execute(f"CREATE TABLE datas ({', '.join(schema_datas)})")
    cur.execute(f"CREATE TABLE texts ({', '.join(schema_texts)})")
    if datas:
        cur.executemany(
            f"INSERT INTO datas VALUES ({','.join(['?'] * len(schema_datas))})",
            list(datas.values())
        )
    if texts:
        cur.executemany(
            f"INSERT INTO texts VALUES ({','.join(['?'] * len(schema_texts))})",
            list(texts.values())
        )
    conn.commit()
    conn.close()
    tmp.replace(path)


def _rush_merge(datas, incoming, edited, before, schema_texts, conflict_decisions=None):
    """Merge Rush con detección de conflictos y decisiones opcionales por campo.
    conflict_decisions: dict {id: {field_name: 'local'|'repo'}} o None.
    Si es None → por defecto gana el valor local (comportamiento previo)."""
    result = {}
    conflicts = []
    text_field_names = schema_texts[1:]

    for card_id, remote in incoming.items():
        row = list(remote)
        if card_id in edited:
            original = before.get(card_id)
            for index in range(1, len(schema_texts)):
                local_value = edited[card_id][index]
                changed_here = (original is None) or (local_value != original[index])
                if not changed_here:
                    continue
                changed_there = (original is not None) and (remote[index] != original[index])
                field_name = text_field_names[index - 1]

                if changed_there and remote[index] != local_value:
                    conflicts.append({
                        "id": card_id,
                        "field": field_name,
                        "previous": original[index],
                        "local": local_value,
                        "published": remote[index],
                    })
                    # Aplicar decisión si existe
                    if conflict_decisions is not None:
                        dec = conflict_decisions.get(card_id, {}).get(field_name, "local")
                        row[index] = remote[index] if dec == "repo" else local_value
                    else:
                        row[index] = local_value  # default: local gana
                else:
                    row[index] = local_value
        result[card_id] = tuple(row)
    return result, conflicts


def _show_conflicts(conflicts, limit=20):
    """Muestra los conflictos de forma legible en pantalla."""
    total = len(conflicts)
    print()
    print("=" * 70)
    print(f"⚠️  {total} conflicto(s) detectado(s)")
    print("=" * 70)
    for i, c in enumerate(conflicts[:limit], 1):
        print()
        print(f"[{i}/{total}] ID={c['id']}  campo={c['field']}")
        print(f"   Antes:  {c['previous']!r}")
        print(f"   Local:  {c['local']!r}")
        print(f"   Repo:   {c['published']!r}")
    if total > limit:
        print()
        print(f"   ... y {total - limit} más (ver Rush/Reports/sync.json para el detalle completo)")
        print()
    try:
        input("Presiona Enter para continuar...")
    except EOFError:
        pass

def _ask_conflict_resolution():
    """Pregunta cómo resolver los conflictos en bloque.
    Devuelve: 'interactive' | 'local' | 'repo' | 'cancel'."""
    print("¿Cómo resolver los conflictos?")
    print("  1 = Uno por uno (interactivo)")
    print("  2 = Mantener MI versión LOCAL en todos")
    print("  3 = Aceptar la versión del REPO en todos")
    print("  4 = Cancelar (decido después, edito manual)")
    while True:
        ans = input("Opción: ").strip()
        if ans == "1": return "interactive"
        if ans == "2": return "local"
        if ans == "3": return "repo"
        if ans == "4": return "cancel"
        print("Opción inválida.")


def _resolve_one_by_one(conflicts):
    """Resuelve conflictos uno por uno. Devuelve dict o None si se cancela."""
    decisions = {}
    for i, c in enumerate(conflicts, 1):
        print()
        print(f"── Conflicto {i}/{len(conflicts)} ──")
        print(f"  ID:     {c['id']}")
        print(f"  Campo:  {c['field']}")
        print(f"  Antes:  {c['previous']!r}")
        print(f"  Local:  {c['local']!r}")
        print(f"  Repo:   {c['published']!r}")
        while True:
            ans = input("  [L]ocal / [R]epo / [T]odos los siguientes = local / [D]todos los siguientes = repo / [C]ancelar: ").strip().lower()
            if ans in ("l", "local"):
                decisions.setdefault(c["id"], {})[c["field"]] = "local"
                break
            elif ans in ("r", "repo"):
                decisions.setdefault(c["id"], {})[c["field"]] = "repo"
                break
            elif ans in ("t", "todos"):
                for cc in conflicts[i-1:]:
                    decisions.setdefault(cc["id"], {})[cc["field"]] = "local"
                return decisions
            elif ans in ("d",):
                for cc in conflicts[i-1:]:
                    decisions.setdefault(cc["id"], {})[cc["field"]] = "repo"
                return decisions
            elif ans in ("c", "cancelar"):
                return None
            else:
                print("  Opción inválida.")
    return decisions


def _open_folder(path):
    path = Path(path)
    if not path.is_dir():
        log(f"❌ No existe la carpeta: {path}")
        return
    for cmd in (["termux-open", str(path)], ["xdg-open", str(path)], ["open", str(path)]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue
    log(f"No se pudo abrir la carpeta automáticamente. Ruta: {path}")

def _apply_rush_mappings(cdb_path):
    try:
        scan, present_ids = scan_cdb_placeholders(cdb_path)
        existing = load_mappings_csv(RUSH_MAPPINGS_CSV)
        merged = merge_mappings(existing, scan, present_ids=present_ids, log_fn=log, report_limit=25)
        save_mappings_csv(RUSH_MAPPINGS_CSV, merged)
        apply_mappings_to_cdb(cdb_path, merged, str(cdb_path), log_fn=log, report_limit=25)
        log("Aplicando reemplazo directo de {CARD} desde Rush/Mappings.csv...")
        reemplazar_card_desde_csv(cdb_path, RUSH_MAPPINGS_CSV, cdb_path)
    except Exception as e:
        log(f"⚠️  Error aplicando Mappings Rush: {e}")

def rush_sync(keep_local=False):
    """Sincroniza Rush con GitHub. Detecta conflictos y te deja decidir
    interactivamente cómo resolverlos. Unifica verificación + descarga."""
    log("=" * 60)
    log("RUSH: Sincronizando desde GitHub")
    log("=" * 60)

    try:
        if not check_github_token():
            log("❌ Token de GitHub inválido.")
            return False
    except Exception as e:
        log(f"❌ Error verificando token: {e}")
        return False

    # 1. Consultar metadata remota
    try:
        info = github_api_request("GET", f"contents/{RUSH_REPO_CDB_PATH}")
    except Exception as e:
        log(f"❌ No se pudo consultar el CDB Rush publicado: {e}")
        return False

    remote_sha = info.get("sha", "")
    remote_size = info.get("size", 0)

    stored_sha = None
    if RUSH_REMOTE_SHA_FILE.exists():
        stored_sha = RUSH_REMOTE_SHA_FILE.read_text(encoding="utf-8").strip() or None

    first_time = stored_sha is None

    if first_time:
        log("ℹ️  Primera sincronización: no hay baseline local.")
        log(f"   CDB remoto: {remote_size} bytes, sha={remote_sha[:12]}")
        log("   Se descargará para establecer la base.")
    elif remote_sha == stored_sha:
        log("✅ Sin novedades: el CDB Rush publicado no ha cambiado.")
        log(f"   sha={remote_sha[:12]} ({remote_size} bytes)")
        if not keep_local:
            respuesta = input("¿Forzar descarga y merge de todos modos? (s/n): ").strip().lower()
            if respuesta != "s":
                log("Operación cancelada.")
                return False
        log("Forzando sincronización.")
    else:
        log("🔔 ¡Hay novedades en el repositorio!")
        log(f"   Antes: {stored_sha[:12]}")
        log(f"   Ahora: {remote_sha[:12]} ({remote_size} bytes)")
        try:
            report = get_remote_json(RUSH_REMOTE_REPORT_PATH)
        except Exception:
            report = None
        if report and isinstance(report, dict):
            added   = report.get("added_ids")   or []
            removed = report.get("removed_ids") or []
            active  = report.get("active_cards")
            if active is not None:
                log(f"   Cartas activas: {active}")
            if added:
                log(f"   ➕ Nuevas: {len(added)} (muestra: {added[:10]})")
            if removed:
                log(f"   ➖ Retiradas: {len(removed)} (muestra: {removed[:10]})")
        if not keep_local:
            respuesta = input("¿Descargar y aplicar ahora? (s/n): ").strip().lower()
            if respuesta != "s":
                log("Operación cancelada.")
                return False

    # 2. Descargar
    log(f"Descargando {RUSH_REPO_CDB_PATH}...")
    try:
        published_bytes = download_remote_file(RUSH_REPO_CDB_PATH)
    except Exception as e:
        log(f"❌ Error descargando: {e}")
        return False
    log(f"   Descargado: {len(published_bytes)} bytes")

    with tempfile.NamedTemporaryFile(suffix=".cdb", delete=False) as tmp:
        tmp.write(published_bytes)
        pub_tmp = Path(tmp.name)

    try:
        schema_datas, schema_texts = _schema_from_cdb(pub_tmp)
        datas, incoming = _read_cdb_from_path(pub_tmp)

        local_path = RUSH_RAW_DIR / RUSH_CDB_NAME
        baseline_path = RUSH_BASE_DIR / RUSH_CDB_NAME

        if local_path.is_file():
            _, local_schema_t = _schema_from_cdb(local_path)
            if local_schema_t != schema_texts:
                log("⚠️  El CDB local tiene un schema diferente al publicado.")
                log(f"   Local:     {local_schema_t}")
                log(f"   Publicado: {schema_texts}")
                return False

        old_datas, edited = _read_cdb_from_path(local_path, optional=True)
        _, before = _read_cdb_from_path(baseline_path, optional=True)
        adopted = not local_path.is_file()

        # 3. Primer merge: detectar conflictos
        result, conflicts = _rush_merge(datas, incoming, edited, before, schema_texts)

        # 4. Resolver conflictos si los hay
        if conflicts and not keep_local:
            _show_conflicts(conflicts)

            RUSH_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            report_path = RUSH_REPORTS_DIR / "sync.json"
            report_path.write_text(
                json.dumps({
                    "active_cards": len(datas),
                    "added_ids": sorted(set(datas) - set(old_datas)),
                    "removed_ids": sorted(set(old_datas) - set(datas)),
                    "conflicts": conflicts,
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8"
            )
            log(f"📄 Reporte guardado: {report_path}")

            choice = _ask_conflict_resolution()

            if choice == "cancel":
                log("❌ Cancelado. No se modificó tu archivo local.")
                return False
            elif choice == "local":
                log("✅ Usando tu versión LOCAL para todos los conflictos.")
                # result ya tiene local por defecto
            elif choice == "repo":
                log("✅ Usando la versión del REPO para todos los conflictos.")
                decisions = {c["id"]: {c["field"]: "repo"} for c in conflicts}
                result, _ = _rush_merge(datas, incoming, edited, before,
                                        schema_texts, conflict_decisions=decisions)
            elif choice == "interactive":
                decisions = _resolve_one_by_one(conflicts)
                if decisions is None:
                    log("❌ Cancelado durante la resolución interactiva.")
                    return False
                result, _ = _rush_merge(datas, incoming, edited, before,
                                        schema_texts, conflict_decisions=decisions)
        elif conflicts and keep_local:
            log(f"⚠️  {len(conflicts)} conflicto(s) resueltos usando tu versión LOCAL (modo keep_local).")

        # 5. Reporte final
        RUSH_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        added_ids   = sorted(set(datas) - set(old_datas))
        removed_ids = sorted(set(old_datas) - set(datas))
        report = {
            "active_cards": len(datas),
            "added_ids": added_ids,
            "removed_ids": removed_ids,
            "first_time": adopted,
            "conflicts_resolved": len(conflicts) if not keep_local else 0,
        }
        report_path = RUSH_REPORTS_DIR / "sync.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")

        log(f"📄 Reporte final: {report_path}")
        log(f"   Cartas activas: {report['active_cards']}")
        if added_ids:
            log(f"   ➕ Nuevas: {len(added_ids)} (muestra: {added_ids[:10]})")
        if removed_ids:
            log(f"   ➖ Retiradas: {len(removed_ids)} (muestra: {removed_ids[:10]})")
        if conflicts and not keep_local:
            log(f"   ✅ Conflictos resueltos: {len(conflicts)}")
        if adopted:
            log("   ℹ️  Primera descarga: se adoptó el CDB publicado como base.")

        # 6. Escribir
        _write_cdb_from_dicts(local_path, datas, result, schema_datas, schema_texts)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_bytes(published_bytes)
        if remote_sha:
            RUSH_REMOTE_SHA_FILE.write_text(remote_sha + "\n", encoding="utf-8")

        log(f"✅ Rush sincronizado → {local_path}")
        _apply_rush_mappings(local_path)
        return True
    finally:
        try:
            pub_tmp.unlink()
        except Exception:
            pass
def _rush_diff(local_path, remote_path):
    """Compara el CDB local con el publicado en GitHub.
    Devuelve dict con las diferencias, o None si falla la descarga/lectura."""
    try:
        published_bytes = download_remote_file(remote_path)
    except Exception as e:
        log(f"   ⚠️  No se pudo descargar el publicado: {e}")
        return None

    with tempfile.NamedTemporaryFile(suffix=".cdb", delete=False) as tmp:
        tmp.write(published_bytes)
        pub_tmp = Path(tmp.name)

    try:
        _, remote = _read_cdb_from_path(pub_tmp)
    except Exception as e:
        log(f"   ⚠️  No se pudo leer el publicado: {e}")
        return None
    finally:
        try:
            pub_tmp.unlink()
        except Exception:
            pass

    _, local = _read_cdb_from_path(local_path)

    new_ids = []
    removed_ids = []
    name_changes = []
    desc_changes = []
    str_changes = []

    for cid, lrow in local.items():
        rrow = remote.get(cid)
        if rrow is None:
            new_ids.append(cid)
            continue
        if lrow[1] != rrow[1]:
            name_changes.append(cid)
        if lrow[2] != rrow[2]:
            desc_changes.append(cid)
        for i in range(3, len(lrow)):
            if lrow[i] != rrow[i]:
                str_changes.append((cid, f"str{i-2}"))
                break

    for cid in remote:
        if cid not in local:
            removed_ids.append(cid)

    return {
        "new_ids": new_ids,
        "removed_ids": removed_ids,
        "name_changes": name_changes,
        "desc_changes": desc_changes,
        "str_changes": str_changes,
        "local_count": len(local),
        "remote_count": len(remote),
    }

def rush_upload():
    log("=" * 60)
    log("RUSH: Subiendo traducciones a GitHub")
    log("=" * 60)
    local_path = RUSH_RAW_DIR / RUSH_CDB_NAME
    if not local_path.is_file():
        log(f"❌ No existe tu CDB Rush local: {local_path}")
        return False
    try:
        if not check_github_token():
            log("❌ Token de GitHub inválido.")
            return False
    except Exception as e:
        log(f"❌ Error verificando token: {e}")
        return False

    size = local_path.stat().st_size
    log(f"📦 Archivo a subir: {RUSH_REPO_CDB_PATH}")
    log(f"   Tamaño: {size:,} bytes")

    log("Comparando con el publicado en GitHub...")
    diff = _rush_diff(local_path, RUSH_REPO_CDB_PATH)

    if diff is None:
        confirm = input("No se pudo comparar. ¿Subir de todos modos? (s/n): ").strip().lower()
        if confirm != "s":
            log("Operación cancelada.")
            return False
    else:
        total = len(diff["name_changes"]) + len(diff["desc_changes"]) + len(diff["str_changes"])
        log("   📊 Cambios vs publicado:")
        log(f"      name:  {len(diff['name_changes'])}")
        log(f"      desc:  {len(diff['desc_changes'])}")
        log(f"      str:   {len(diff['str_changes'])}")
        if diff["new_ids"]:
            log(f"      cartas locales no publicadas: {len(diff['new_ids'])}")
        if diff["removed_ids"]:
            log(f"      cartas publicadas no locales: {len(diff['removed_ids'])}")

        if total == 0 and not diff["new_ids"]:
            log("   ℹ️  No hay cambios respecto al publicado.")
            confirm = input("¿Subir de todos modos? (s/n): ").strip().lower()
            if confirm != "s":
                log("Operación cancelada.")
                return False
        elif total > 50:
            log(f"   ⚠️  Muchos campos modificados ({total}). Verifica antes de continuar.")
            confirm = input(f"¿Confirmar subida con {total} cambios? (s/n): ").strip().lower()
            if confirm != "s":
                log("Operación cancelada.")
                return False
        else:
            confirm = input("¿Confirmar subida a GitHub? (s/n): ").strip().lower()
            if confirm != "s":
                log("Operación cancelada.")
                return False

    try:
        upload_file_via_api(local_path, RUSH_REPO_CDB_PATH, "Update ES Rush translations")
    except Exception as e:
        log(f"❌ Error al subir: {e}")
        return False
    log("✅ Rush subido. GitHub Actions compilará el rush-pre-release.ypk en unos minutos.")
    return True

def rush_show_diff():
    """Muestra las diferencias entre el CDB local y el publicado. No modifica nada."""
    log("=" * 60)
    log("RUSH: Diferencias local vs publicado")
    log("=" * 60)

    local_path = RUSH_RAW_DIR / RUSH_CDB_NAME
    if not local_path.is_file():
        log(f"❌ No existe tu CDB local: {local_path}")
        log("   Ejecuta primero 'Sincronizar Rush'.")
        return

    log("Descargando publicado...")
    diff = _rush_diff(local_path, RUSH_REPO_CDB_PATH)
    if diff is None:
        return

    log("")
    log("📊 Comparación:")
    log(f"   Cartas locales:    {diff['local_count']}")
    log(f"   Cartas publicadas: {diff['remote_count']}")
    log("")
    log(f"   name con diferencias: {len(diff['name_changes'])}")
    log(f"   desc con diferencias: {len(diff['desc_changes'])}")
    log(f"   str con diferencias:  {len(diff['str_changes'])}")
    log(f"   Cartas locales no publicadas: {len(diff['new_ids'])}")
    log(f"   Cartas publicadas no locales: {len(diff['removed_ids'])}")

    if diff["name_changes"]:
        sample = diff["name_changes"][:15]
        suffix = " ..." if len(diff["name_changes"]) > 15 else ""
        log(f"      IDs (name): {sample}{suffix}")
    if diff["desc_changes"]:
        sample = diff["desc_changes"][:15]
        suffix = " ..." if len(diff["desc_changes"]) > 15 else ""
        log(f"      IDs (desc): {sample}{suffix}")
    if diff["str_changes"]:
        sample = diff["str_changes"][:15]
        suffix = " ..." if len(diff["str_changes"]) > 15 else ""
        log(f"      (ID, campo) str con diferencias: {sample}{suffix}")
    if diff["new_ids"]:
        sample = diff["new_ids"][:15]
        suffix = " ..." if len(diff["new_ids"]) > 15 else ""
        log(f"      IDs nuevos: {sample}{suffix}")
    if diff["removed_ids"]:
        sample = diff["removed_ids"][:15]
        suffix = " ..." if len(diff["removed_ids"]) > 15 else ""
        log(f"      IDs publicados no locales: {sample}{suffix}")

    total = len(diff["name_changes"]) + len(diff["desc_changes"]) + len(diff["str_changes"])
    if total == 0 and not diff["new_ids"] and not diff["removed_ids"]:
        log("")
        log("✅ Todo sincronizado: tu local coincide con el publicado.")

def standard_menu():
    while True:
        print()
        print("── STANDARD ──")
        print("1 = Sincronizar raw2 Standard desde GitHub")
        print("2 = Subir workspace Standard a GitHub")
        print("3 = Exportar str sin traducir de Standard (a TXT)")
        print("4 = Importar traducciones de Standard (desde TXT)")
        print("5 = Volver al menú principal")
        choice = input("Opción Standard: ").strip()
        if choice == "1":
            option1_sync_only()
        elif choice == "2":
            option2_full_update()
        elif choice == "3":
            option3_export_standard()
        elif choice == "4":
            option4_import_standard()
        elif choice == "5":
            return
        else:
            print("Opción inválida.")

def rush_menu():
    while True:
        print()
        print("── RUSH ──")
        print("1 = Sincronizar Rush desde GitHub")
        print("2 = Subir traducciones Rush a GitHub")
        print("3 = Abrir carpeta local de traducción (Rush/raw2)")
        print("4 = Ver reporte de conflictos (Rush/Reports)")
        print("5 = Exportar str sin traducir de Rush (a TXT)")
        print("6 = Importar traducciones de Rush (desde TXT)")
        print("7 = Ver diferencias local vs publicado")
        print("8 = Volver al menú principal")
        choice = input("Opción Rush: ").strip()
        if choice == "1":
            rush_sync()
        elif choice == "2":
            rush_upload()
        elif choice == "3":
            _open_folder(RUSH_RAW_DIR)
        elif choice == "4":
            _open_folder(RUSH_REPORTS_DIR)
        elif choice == "5":
            rush_export()
        elif choice == "6":
            rush_import()
        elif choice == "7":
            rush_show_diff()
        elif choice == "8":
            return
        else:
            print("Opción inválida.")
# =========================================================
# ==================  FIN RUSH  ===========================
# =========================================================


# =========================================================
# ============  STANDARD: REFRESH DESDE GITHUB  ===========
# =========================================================
def _fetch_standard_remote_shas():
    """Obtiene los shas de los 3 archivos Standard sin descargar contenido."""
    try:
        result = github_api_request("GET", f"git/trees/{GITHUB_BRANCH}?recursive=1")
    except Exception as e:
        raise RuntimeError(f"No se pudo consultar el árbol del repo: {e}")

    if result.get("truncated"):
        log("⚠️  Árbol del repo truncado; usando contents API individual.")
        shas = {}
        for key, path in STANDARD_FILES.items():
            info = github_api_request("GET", f"contents/{path}")
            shas[key] = info.get("sha", "")
        return shas

    path_to_sha = {e["path"]: e.get("sha", "") for e in result.get("tree", [])}
    return {key: path_to_sha.get(path, "") for key, path in STANDARD_FILES.items()}


def _load_stored_shas():
    if not STANDARD_REMOTE_SHA_FILE.exists():
        return {}
    try:
        return json.loads(STANDARD_REMOTE_SHA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_stored_shas(shas):
    try:
        STANDARD_REMOTE_SHA_FILE.write_text(
            json.dumps(shas, indent=2, sort_keys=True) + "\n",
            encoding="utf-8"
        )
    except Exception as e:
        log(f"⚠️  No se pudo guardar {STANDARD_REMOTE_SHA_FILE}: {e}")


def _merge_cdb_into_local(local_path, upstream_bytes):
    """Fusiona el CDB upstream con el local.
    - datas: upsert todas (gameplay se actualiza)
    - texts: solo INSERTAR cartas nuevas (preserva traducciones)
    - Elimina entradas que ya no existen en upstream"""
    local_path = Path(local_path)
    with tempfile.NamedTemporaryFile(suffix=".cdb", delete=False) as tmp:
        tmp.write(upstream_bytes)
        upstream_path = Path(tmp.name)

    try:
        conn_l = sqlite3.connect(str(local_path))
        conn_u = sqlite3.connect(str(upstream_path))
        cur_l, cur_u = conn_l.cursor(), conn_u.cursor()

        # datas: upsert
        cur_u.execute("SELECT * FROM datas")
        cols = [d[0] for d in cur_u.description]
        idx = {name: i for i, name in enumerate(cols)}
        id_idx = idx.get("id", 0)
        set_cols = [c for c in cols if c != "id"]
        set_idx = [idx[c] for c in set_cols]
        assignments = ", ".join(f"{c}=?" for c in set_cols)
        update_sql = f"UPDATE datas SET {assignments} WHERE id=?"
        placeholders = ",".join("?" * len(cols))
        for row in cur_u.fetchall():
            cid = row[id_idx]
            cur_l.execute("SELECT 1 FROM datas WHERE id=?", (cid,))
            if cur_l.fetchone() is None:
                cur_l.execute(f"INSERT INTO datas VALUES ({placeholders})", row)
            else:
                values = [row[i] for i in set_idx]
                cur_l.execute(update_sql, (*values, cid))

        # texts: solo insertar nuevas
        cur_u.execute("SELECT * FROM texts")
        cols_t = [d[0] for d in cur_u.description]
        idx_t = {name: i for i, name in enumerate(cols_t)}
        id_idx_t = idx_t.get("id", 0)
        placeholders_t = ",".join("?" * len(cols_t))
        cur_l.execute("SELECT id FROM texts")
        existing_ids = {row[0] for row in cur_l.fetchall()}
        inserted = 0
        for row in cur_u.fetchall():
            cid = row[id_idx_t]
            if cid in existing_ids:
                continue
            cur_l.execute(f"INSERT INTO texts VALUES ({placeholders_t})", row)
            inserted += 1

        # eliminar las que ya no están en upstream
        cur_u.execute("SELECT id FROM datas")
        up_ids = {r[0] for r in cur_u.fetchall()}
        cur_l.execute("SELECT id FROM datas")
        local_ids = {r[0] for r in cur_l.fetchall()}
        to_remove = local_ids - up_ids
        if to_remove:
            cur_l.executemany("DELETE FROM datas WHERE id=?", [(c,) for c in to_remove])

        cur_u.execute("SELECT id FROM texts")
        up_tids = {r[0] for r in cur_u.fetchall()}
        cur_l.execute("SELECT id FROM texts")
        local_tids = {r[0] for r in cur_l.fetchall()}
        to_remove_t = local_tids - up_tids
        if to_remove_t:
            cur_l.executemany("DELETE FROM texts WHERE id=?", [(c,) for c in to_remove_t])

        conn_l.commit()
        cur_l.execute("VACUUM")
        conn_l.commit()
        conn_l.close()
        conn_u.close()

        log(f"   Merge CDB: cartas nuevas insertadas={inserted}, "
            f"datas retiradas={len(to_remove)}, texts retiradas={len(to_remove_t)}")
    finally:
        try:
            upstream_path.unlink()
        except Exception:
            pass


def option1_sync_only(dry_run=False):
    if dry_run:
        log("*** MODO SIMULACIÓN (dry-run) ***")
        return
    start = time.time()

    try:
        if not check_github_token():
            log("❌ No se puede continuar sin un token válido.")
            return
    except Exception as e:
        log(f"❌ Error verificando token: {e}")
        return

    log("Verificando novedades en GitHub...")
    try:
        remote_shas = _fetch_standard_remote_shas()
    except Exception as e:
        log(f"❌ {e}")
        return

    stored = _load_stored_shas()
    changed = {}
    for key, path in STANDARD_FILES.items():
        r_sha = remote_shas.get(key, "")
        o_sha = stored.get(key, "")
        if not r_sha:
            log(f"⚠️  No se encontró {path} en el repo.")
            continue
        if r_sha != o_sha:
            changed[key] = r_sha

    if not changed:
        log("✅ Sin novedades: los 3 archivos (CDB, conf, Mappings) están sin cambios.")
        respuesta = input("¿Forzar descarga y sincronización de todos modos? (s/n): ").strip().lower()
        if respuesta != "s":
            log("Operación cancelada.")
            return
        changed = {k: remote_shas[k] for k in STANDARD_FILES if remote_shas.get(k)}
        log("Forzando sincronización de los 3 archivos.")
    else:
        log(f"🔔 Novedades detectadas en: {', '.join(sorted(changed.keys()))}")

    respuesta = input("¿Descargar y aplicar ahora? (s/n): ").strip().lower()
    if respuesta != "s":
        log("Operación cancelada.")
        return

    # Descargar solo lo que cambió
    downloaded = {}
    for key in changed:
        path = STANDARD_FILES[key]
        log(f"Descargando {path}...")
        try:
            downloaded[key] = download_remote_file(path)
            log(f"   {len(downloaded[key])} bytes")
        except Exception as e:
            log(f"❌ Error descargando {path}: {e}")
            return

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # 1) CDB
    if "cdb" in downloaded:
        target = RAW_DIR / "test-release.cdb"
        if target.exists():
            log("Fusionando CDB upstream con local (preservando traducciones)...")
            _merge_cdb_into_local(target, downloaded["cdb"])
        else:
            target.write_bytes(downloaded["cdb"])
            log(f"   Guardado nuevo {target}")

    # 2) test-strings.conf
    if "conf" in downloaded:
        sync_test_strings_conf(downloaded["conf"], RAW_DIR / "test-strings.conf",
                               log_fn=log, report_limit=25)

    # 3) Mappings (fusión local + remoto)
    if "mappings" in downloaded:
        log("Fusionando Shared/Mappings.csv con tu Mappings.csv local...")
        try:
            remote_rows = _parse_mappings_text(downloaded["mappings"].decode("utf-8-sig"))
            _fuse_mappings(remote_rows, MAPPINGS_CSV)
        except Exception as e:
            log(f"⚠️  Error fusionando Mappings: {e}")

    # 4) Parches finales sobre el CDB local
    raw_cdb = RAW_DIR / "test-release.cdb"
    if raw_cdb.exists():
        lang = "es"
        pend_lang = _normalize_pend_lang(lang, CARDS_CDB)

        patch_cdb(raw_cdb, lang=lang, log_fn=log, report_limit=25)

        scan, present_ids = scan_cdb_placeholders(raw_cdb)
        existing = load_mappings_csv(MAPPINGS_CSV)
        merged = merge_mappings(existing, scan, present_ids=present_ids, log_fn=log, report_limit=25)
        save_mappings_csv(MAPPINGS_CSV, merged)
        apply_mappings_to_cdb(raw_cdb, merged, CARDS_CDB, log_fn=log, report_limit=25)

        patch_pendulum_layout_desc(raw_cdb, pend_lang, log_fn=log, report_limit=25)
        log("Aplicando reemplazo directo de {CARD} desde Mappings.csv...")
        reemplazar_card_desde_csv(raw_cdb, MAPPINGS_CSV, CARDS_CDB)

    # 5) Guardar shas actuales
    new_stored = dict(stored)
    for key in changed:
        new_stored[key] = changed[key]
    _save_stored_shas(new_stored)

    log(f"✅ Sincronización completada en {time.time()-start:.1f}s")


def option2_full_update():
    start = time.time()
    if not check_github_token():
        log("No se puede continuar sin un token válido.")
        return

    cdb_path = RAW_DIR / "test-release.cdb"
    conf_path = RAW_DIR / "test-strings.conf"

    available = []
    if cdb_path.exists():
        available.append("cdb")
    if conf_path.exists():
        available.append("conf")

    if not available:
        log("No hay archivos disponibles para subir. Ejecuta la opción 1 primero.")
        return

    print("\nArchivos disponibles:")
    print("1 = test-release.cdb (siempre recomendado)")
    print("2 = test-strings.conf")
    print("3 = Subir ambos")
    print("4 = Selección individual (s/n uno por uno)")
    choice = input("Elige una opción (1-4): ").strip()

    files_to_upload = []
    if choice == "1":
        if "cdb" in available:
            files_to_upload.append((cdb_path, f"{REPO_SUBDIR}/Workspace/test-release.cdb"))
        else:
            log("test-release.cdb no está disponible.")
    elif choice == "2":
        if "conf" in available:
            files_to_upload.append((conf_path, f"{REPO_SUBDIR}/Workspace/test-strings.conf"))
        else:
            log("test-strings.conf no está disponible.")
    elif choice == "3":
        if "cdb" in available:
            files_to_upload.append((cdb_path, f"{REPO_SUBDIR}/Workspace/test-release.cdb"))
        if "conf" in available:
            files_to_upload.append((conf_path, f"{REPO_SUBDIR}/Workspace/test-strings.conf"))
    elif choice == "4":
        if "cdb" in available and input("¿Subir test-release.cdb? (s/n): ").lower() == 's':
            files_to_upload.append((cdb_path, f"{REPO_SUBDIR}/Workspace/test-release.cdb"))
        if "conf" in available and input("¿Subir test-strings.conf? (s/n): ").lower() == 's':
            files_to_upload.append((conf_path, f"{REPO_SUBDIR}/Workspace/test-strings.conf"))
    else:
        log("Opción inválida.")
        return

    if not files_to_upload:
        log("No se seleccionó ningún archivo para subir.")
        return

    # Validación de release antes de subir conf
    if any("test-strings.conf" in str(remote) for _, remote in files_to_upload):
        try:
            ensure_test_strings_ready_for_release(conf_path, log_fn=log, report_limit=25)
        except Exception as e:
            log(f"❌ test-strings.conf no está listo: {e}")
            return

    names = []
    for _, remote in files_to_upload:
        if "test-release.cdb" in str(remote):
            names.append("CDB")
        elif "test-strings.conf" in str(remote):
            names.append("strings.conf")
    desc_files = ", ".join(names)
    tag_name = next_tag()
    version_num = tag_name.replace(TAG_PREFIX, "")
    commit_msg = f"{SUMMARY} - v{version_num}\n\nArchivos subidos: {desc_files}"

    uploaded = 0
    total = len(files_to_upload)
    for idx, (local, remote) in enumerate(files_to_upload, 1):
        log(f"Subiendo {idx}/{total}: {remote}")
        upload_file_via_api(local, remote, commit_msg)
        uploaded += 1
    log(f"✅ Subidos {uploaded}/{total}.")

    crear_tag = input("¿Crear tag de release? (s/n): ").strip().lower() == 's'
    if crear_tag:
        branch_info = github_api_request("GET", f"branches/{GITHUB_BRANCH}")
        create_tag(tag_name, branch_info["commit"]["sha"])
        log(f"✅ Tag {tag_name} creado.")

    log(f"Proceso completado en {time.time()-start:.1f}s")

def option3_export_standard():
    export_untranslated_str(
        RAW_DIR / "test-release.cdb",
        RAW_DIR / "untranslated_str.txt",
        include_desc=False,
    )


def option4_import_standard():
    import_str_translations(
        RAW_DIR / "test-release.cdb",
        RAW_DIR / "untranslated_str.txt",
    )


def rush_export():
    export_untranslated_str(
        RUSH_RAW_DIR / RUSH_CDB_NAME,
        RUSH_RAW_DIR / "untranslated_str.txt",
        include_desc=False,
    )


def rush_import():
    import_str_translations(
        RUSH_RAW_DIR / RUSH_CDB_NAME,
        RUSH_RAW_DIR / "untranslated_str.txt",
    )

def reemplazar_card_desde_csv(cdb_path, csv_path, cards_cdb_path):
    cdb_path = Path(cdb_path)
    csv_path = Path(csv_path)
    cards_cdb_path = Path(cards_cdb_path)

    if not cdb_path.exists():
        log(f"Error: {cdb_path} no existe")
        return 0
    if not csv_path.exists():
        log(f"Error: {csv_path} no existe")
        return 0

    mappings = {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                cid = int(row["id"])
            except:
                continue
            cols = {}
            for i in range(1, 17):
                val = row.get(f"str{i}", "").strip()
                if val:
                    cols[f"str{i}"] = val
            if cols:
                mappings[cid] = cols

    if not mappings:
        log("No se encontraron mappings en el CSV.")
        return 0

    conn = sqlite3.connect(cdb_path)
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM texts")
    nombres_pre = {row[0]: row[1] for row in cur.fetchall()}

    nombres_oficiales = {}
    if cards_cdb_path.exists():
        conn_cards = sqlite3.connect(cards_cdb_path)
        cur_cards = conn_cards.cursor()
        cur_cards.execute("SELECT id, name FROM texts")
        for row in cur_cards.fetchall():
            nombres_oficiales[row[0]] = row[1]
        conn_cards.close()

    actualizadas = 0
    for cid, cols in mappings.items():
        cur.execute("SELECT 1 FROM texts WHERE id=?", (cid,))
        if not cur.fetchone():
            continue
        columnas = list(cols.keys())
        cur.execute(f"SELECT {','.join(columnas)} FROM texts WHERE id=?", (cid,))
        row = cur.fetchone()
        if not row:
            continue
        updates = {}
        for col, valor_actual in zip(columnas, row):
            if not valor_actual or "{CARD}" not in valor_actual:
                continue
            mapping_val = cols[col]
            nombre = None
            if mapping_val == "self":
                nombre = nombres_pre.get(cid, "")
            else:
                try:
                    target_id = int(mapping_val)
                    if target_id in nombres_oficiales:
                        nombre = nombres_oficiales[target_id]
                    else:
                        nombre = nombres_pre.get(target_id, "")
                except ValueError:
                    pass
            if not nombre:
                continue
            nuevo_valor = valor_actual.replace("{CARD}", nombre)
            updates[col] = nuevo_valor
        if updates:
            set_clause = ", ".join([f"{col}=?" for col in updates])
            valores = list(updates.values()) + [cid]
            cur.execute(f"UPDATE texts SET {set_clause} WHERE id=?", valores)
            actualizadas += 1

    conn.commit()
    conn.close()
    log(f"Reemplazo completado: {actualizadas} cartas actualizadas.")
    return actualizadas

if __name__ == "__main__":
    try:
        while True:
            print()
            print("Elige una opción:")
            print("1 = ── STANDARD ── (submenú)")
            print("2 = ── RUSH ── (submenú)")
            print("3 = Salir")
            choice = input("Opción: ").strip()
            if choice == "1":
                standard_menu()
            elif choice == "2":
                rush_menu()
            elif choice == "3":
                print("Saliendo.")
                break
            else:
                print("Opción inválida.")
    except KeyboardInterrupt:
        print("\nOperación cancelada por el usuario.")
        sys.exit(0)
    except Exception as e:
        log(f"Error inesperado: {e}")
        sys.exit(1)

