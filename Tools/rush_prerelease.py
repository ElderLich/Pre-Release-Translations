#!/usr/bin/env python3
"""Synchronize the authoritative Rush patch into translated, reproducible YPKs.

Workspace CDBs are translator inputs; completed locales publish independently.
Incomplete locales keep their last-good packages, with retired cards removed,
while updated workspaces and diagnostic reports remain available.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANGS = {"en": ("EN", "en-US"), "de": ("DE", "de-DE"),
         "es": ("ES", "es-ES"), "fr": ("FR", "fr-FR"),
         "it": ("IT", "it-IT"), "jp": ("JP", "ja-JP"),
         "kr": ("KR", "ko-KR"), "pt": ("PT", "pt-PT"),
         "th": ("TH", "en-US"), "zh-cn": ("ZH-CN", "zh-CN"),
         "zh-tw": ("ZH-TW", "zh-TW")}
DATA_FIELDS = "id ot alias setcode type atk def level race attribute category".split()
TEXT_FIELDS = ["id", "name", "desc"] + [f"str{i}" for i in range(1, 17)]
CJK = re.compile(r"[\u3400-\u9fff]")
SCRIPT_LOAD = re.compile(r'(?:LoadScript|dofile)\s*\(\s*["\']([^"\']+\.lua)["\']')
CARD_REFERENCE = re.compile(r"\bc(\d+)\s*[.\[]")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_changed(path: Path, data: bytes) -> bool:
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return True


def read_json(path: Path, default: object = None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def read_cdb(path: Path, optional: bool = False) -> tuple[dict, dict]:
    if optional and not path.is_file():
        return {}, {}
    if not path.is_file():
        raise ValueError(f"Required database is missing: {path}")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError(f"Invalid database: {path}")
        result = []
        for table, fields in (("datas", DATA_FIELDS), ("texts", TEXT_FIELDS)):
            actual = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
            if actual != fields:
                raise ValueError(f"Unexpected {table} schema in {path}: {actual}")
            result.append({row[0]: tuple(row) for row in db.execute(f"SELECT * FROM {table}")})
    if result[0].keys() != result[1].keys():
        raise ValueError(f"datas/texts IDs differ in {path}")
    return result[0], result[1]


def cdb_bytes(datas: dict, texts: dict) -> bytes:
    with tempfile.TemporaryDirectory(prefix="rush-cdb-") as directory:
        path = Path(directory) / "cards.cdb"
        with closing(sqlite3.connect(path)) as db:
            for table, fields, rows in (("datas", DATA_FIELDS, datas), ("texts", TEXT_FIELDS, texts)):
                columns = ["id integer primary key"] + [
                    f'"{field}" {"integer" if table == "datas" else "text"}' for field in fields[1:]]
                db.execute(f'CREATE TABLE {table}({",".join(columns)})')
                db.executemany(f'INSERT INTO {table} VALUES({",".join("?" for _ in fields)})',
                               (rows[card_id] for card_id in sorted(rows)))
            db.commit()
        return path.read_bytes()


def revision(path: Path) -> str | None:
    result = subprocess.run(["git", "-c", f"safe.directory={path.resolve().as_posix()}",
                             "-C", str(path), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def file_fingerprint(root: Path, paths: list[Path]) -> str:
    value = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        value.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        value.update(hashlib.sha256(path.read_bytes()).digest())
    return value.hexdigest()


def local_inputs_sha256(repo_root: Path | str) -> str:
    """Small public contract used by the daily dispatcher before starting jobs."""
    root = Path(repo_root)
    paths = [root / "Tools/rush_prerelease.py", root / "Shared/Rush/config.json",
             root / "Tools/rush_source_text.py", root / "Shared/Rush/source_mappings.json",
             root / ".github/scripts/card_text_autofill.py", root / ".github/scripts/codexygo_source.py",
             root / "Tools/PromptPatcher.py", root / "Shared/Mappings.csv",
             root / ".github/scripts/shared_mappings.py", root / ".github/scripts/tcg_autopr.py"]
    paths += list((root / "Shared/Rush Artworks").glob("*"))
    paths += list(root.glob("*/Rush/Workspace/*.cdb"))
    return file_fingerprint(root, [path for path in paths if path.is_file()])


def script_closure(source: Path, datas: dict) -> dict[str, bytes]:
    """Include active scripts, explicit dependencies and the shared RD modules.

    special.lua is the client's initializer and is intentionally not overridden.
    Standard/alias scripts may be dependencies without adding their CDB rows.
    """
    directory = source / "script"
    if not directory.is_dir():
        raise ValueError(f"Missing upstream script folder: {directory}")
    names = {path.name for path in directory.glob("RD*.lua")}
    for row in datas.values():
        for card_id in (row[0], row[2]):
            if card_id and (directory / f"c{card_id}.lua").is_file():
                names.add(f"c{card_id}.lua")
        # Normal monsters and name-only simulator helpers legitimately have no Lua.
        if (row[4] & 0x10) == 0 and row[4] and not any(
                (directory / f"c{card_id}.lua").is_file() for card_id in (row[0], row[2]) if card_id):
            raise ValueError(f"Missing effect script for patch card {row[0]}")
    files = {}
    while names:
        name = min(names)
        names.remove(name)
        if name in files:
            continue
        if Path(name).name != name or name == "special.lua":
            raise ValueError(f"Unsupported script dependency: {name}")
        path = directory / name
        if not path.is_file():
            raise ValueError(f"Missing script dependency: {name}")
        data = path.read_bytes()
        files[name] = data
        text = data.decode("utf-8-sig")
        dependencies = set(SCRIPT_LOAD.findall(text))
        dependencies.update(f"c{card_id}.lua" for card_id in CARD_REFERENCE.findall(text)
                            if (directory / f"c{card_id}.lua").is_file())
        # RD.GetCardData(code) loads the named existing script dynamically.
        dependencies.update(f"c{card_id}.lua" for card_id in re.findall(
            r"RD\.GetCardData\s*\(\s*(\d+)\s*\)", text))
        names.update(dependencies - files.keys())
    return {f"script/{name}": data for name, data in sorted(files.items())}


def art_files(root: Path, ids: set[int]) -> tuple[dict[str, bytes], list[int]]:
    available = {}
    for path in sorted((root / "Shared/Rush Artworks").glob("*")):
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg") or not path.stem.isdigit():
            continue
        card_id = int(path.stem)
        if card_id not in ids:
            continue
        if card_id in available:
            raise ValueError(f"More than one shared artwork for {card_id}")
        data = path.read_bytes()
        valid = data.startswith(b"\x89PNG\r\n\x1a\n") if path.suffix.lower() == ".png" else data.startswith(b"\xff\xd8\xff")
        if not valid:
            raise ValueError(f"Invalid image header: {path}")
        available[card_id] = (f"Picture/Rush_Arts/{card_id}{path.suffix.lower()}", data)
    return dict(available.values()), sorted(ids - available.keys())


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name])
    return buffer.getvalue()


def translated_row(row: tuple, original: tuple, lang: str, system: bool) -> list[str]:
    errors = []
    for index, field in enumerate(TEXT_FIELDS[1:], 1):
        value, raw = row[index] or "", original[index] or ""
        if not value.strip() and (field == "name" or (raw.strip() and not system)):
            errors.append(field)
        elif lang not in ("zh-cn", "zh-tw", "jp") and CJK.search(value):
            errors.append(field)
        elif lang == "jp" and value and value == raw and CJK.search(raw) and len(raw) > 8:
            errors.append(field)
        if re.search(r"\{(?:CARD|NUMBER)\}", value):
            errors.append(field)
    return sorted(set(errors))


def text_field_hashes(row: tuple) -> dict[str, str]:
    return {field: digest((row[index] or "").encode("utf-8"))
            for index, field in enumerate(TEXT_FIELDS[1:], 1) if row[index]}


def backfill_seed(row: tuple, source_row: tuple, seed: tuple | None) -> tuple:
    """Fill untouched import fields without replacing a translator's changes."""
    if seed is None:
        return row
    result = list(row)
    for index in range(1, len(TEXT_FIELDS)):
        if (not result[index] or result[index] == source_row[index]) and seed[index]:
            result[index] = seed[index]
    return tuple(result)


def prompt_tools():
    # The same modules drive the existing standard prerelease translator flow.
    for directory in (ROOT / "Tools", ROOT / ".github/scripts"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    import PromptPatcher
    from shared_mappings import load_locale_mappings_csv
    from tcg_autopr import parse_card_ids, replace_placeholders
    return PromptPatcher, load_locale_mappings_csv, parse_card_ids, replace_placeholders


def apply_prompt_translations(row: tuple, source_row: tuple, lang: str,
                              pack: dict, mappings: dict, names: dict) -> tuple:
    patcher, _, parse_ids, replace_tokens = prompt_tools()
    result = list(row)
    for index in range(3, len(TEXT_FIELDS)):
        current = result[index] or ""
        if lang == "jp" and current and "{CARD}" not in current:
            # Importers may already substitute Japanese names inside Chinese prompts.
            skeleton = lambda text: re.sub(r"「[^」]*」", "「CARD」", text)
            if skeleton(current) != skeleton(source_row[index] or ""):
                continue
        value = patcher.patch_prompt(current, pack)
        if "{CARD}" in value:
            tokens = parse_ids(mappings.get(row[0], {}).get(TEXT_FIELDS[index]))
            if len(tokens) == value.count("{CARD}"):
                localized = [row[1] if token == "self" else names.get(int(token), (None, None))[1]
                             for token in tokens]
                if all(localized):
                    value = replace_tokens(value, localized)
        result[index] = value
    return tuple(result)



def prepare_retired_releases(root: Path, languages: list[str], active_ids: set[int]) -> tuple[dict, dict]:
    """Remove retired IDs even while new untranslated additions block publication.

    Retained rows and scripts come from the last-good archive, not the changed
    upstream source. Thus pruning cannot ship a partially translated new effect.
    Preflight every archive before replacing any output.
    """
    outputs, removed = {}, {}
    for lang in languages:
        folder = LANGS[lang][0]
        package_path = root / folder / "rush-pre-release.ypk"
        if not package_path.is_file():
            continue
        with zipfile.ZipFile(package_path) as archive:
            files = {entry.filename: archive.read(entry) for entry in archive.infolist() if not entry.is_dir()}
        with tempfile.TemporaryDirectory(prefix="rush-retirement-") as temporary:
            temporary_root = Path(temporary)
            database = temporary_root / "cards.cdb"
            database.write_bytes(files["rush-pre-release.cdb"])
            old_datas, old_texts = read_cdb(database)
            retired = set(old_datas) - active_ids
            if not retired:
                continue
            datas = {key: row for key, row in old_datas.items() if key in active_ids}
            texts = {key: old_texts[key] for key in datas}
            script_dir = temporary_root / "script"
            script_dir.mkdir()
            for name, data in files.items():
                if name.startswith("script/") and Path(name).name == name[len("script/"):]:
                    (script_dir / Path(name).name).write_bytes(data)
            scripts = script_closure(temporary_root, datas)
            artwork = {name: data for name, data in files.items()
                       if name.startswith("Picture/Rush_Arts/") and Path(name).stem.isdigit()
                       and int(Path(name).stem) in datas}
            payload = {"rush-pre-release.cdb": cdb_bytes(datas, texts), **scripts, **artwork}
            outputs[package_path] = zip_bytes(payload)
            outputs[root / folder / "rush-pre-release.json"] = json_bytes({
                str(key): {**dict(zip(DATA_FIELDS, datas[key])), **dict(zip(TEXT_FIELDS[1:], texts[key][1:]))}
                for key in sorted(datas)})
            removed[folder] = sorted(retired)
    return outputs, removed


def prepare_versions(outputs: dict[Path, bytes]) -> list[str]:
    """Validate every version before any ready or retirement-only package is written."""
    versions, changed_locales = {}, []
    for path, data in outputs.items():
        if path.suffix != ".ypk":
            continue
        version = path.with_name("rush-version.txt")
        old_version = int(version.read_text().strip()) if version.exists() else 0
        if not path.is_file() or path.read_bytes() != data or not version.exists():
            versions[version] = str(max(int(time.time()), old_version + 1)).encode("ascii")
            changed_locales.append(path.parent.name)
    outputs.update(versions)
    return sorted(changed_locales)


def synchronize(root: Path, source: Path, translations_root: Path | None,
                languages: list[str], fetch_source_text: bool = False) -> dict:
    config = read_json(root / "Shared/Rush/config.json", {})
    excluded = {int(card_id): int(target) for card_id, target in config.get("retired_redirects", {}).items()}
    # Publish just the identity ranges so translators need no upstream checkout.
    if (source / "sets.md").is_file():
        write_changed(root / "Shared/Rush/sets.md", (source / "sets.md").read_bytes())
    source_datas, source_texts = read_cdb(source / "RD Patch.cdb")
    datas = {key: row for key, row in source_datas.items() if key not in excluded}
    texts = {key: source_texts[key] for key in datas}
    ids = set(datas)
    system_ids = set(config.get("system_ids", []))
    patcher, load_mappings, _, _ = prompt_tools()
    from card_text_autofill import encode_rush_description
    scripts = script_closure(source, datas)
    artwork, missing_art = art_files(root, ids - system_ids)
    previous_source = read_json(root / "Shared/Rush/source.json", {})
    source_hashes = {str(key): digest(json_bytes(list(texts[key]))) for key in sorted(ids)}
    source_fields = {str(key): text_field_hashes(texts[key]) for key in sorted(ids)}
    outputs = {}
    formatted_workspaces = {}
    pending_workspaces = {}
    reports = {}
    publication_fields = {}
    pending_languages = []
    # Keep English and its Thai fallback together, including single-locale builds.
    selected = list(dict.fromkeys((["en", "th"] if {"en", "th"} & set(languages) else []) + languages))
    selected.sort(key=lambda key: (key != "en", key))
    english_rows = None
    english_pending = None
    for lang in selected:
        folder, locale = LANGS[lang]
        locale_root = root / folder / "Rush"
        workspace = locale_root / "Workspace/rush-pre-release.cdb"
        old_datas, old_texts = read_cdb(workspace, optional=True)
        seed_datas, seeds = read_cdb(translations_root / locale / "rush_cards.cdb", optional=True) if translations_root else ({}, {})
        merged = {}
        unformatted_rows = {}
        unresolved = []
        previous_report = read_json(locale_root / "report.json", {})
        # Diagnostic row hashes change on pending runs. Review must compare against
        # the last successful publication, including across staggered locale edits.
        old_published_hashes = previous_report.get("published_text_sha256", previous_report.get("text_sha256", {}))
        old_source_hashes = previous_report.get("published_source_sha256", previous_source.get("text_sha256", {}))
        old_source_fields = previous_report.get("published_source_fields", {})
        old_text_fields = previous_report.get("published_text_fields", {})
        pack = patcher.build_translation_pack(lang) if lang not in ("zh-cn", "zh-tw", "th") else None
        mappings = load_mappings(root / "Shared/Mappings.csv", lang)
        candidates = {key: backfill_seed(old_texts.get(key, seeds.get(key, texts[key])), texts[key], seeds.get(key))
                      for key in sorted(ids)}
        # Refresh the explicit manual-only report even for offline Korean builds.
        if fetch_source_text or lang == "kr":
            from rush_source_text import fetch_rows
            candidates, fetch_report = fetch_rows(root, source, texts, candidates, lang, system_ids)
            fetch_path = locale_root / "source_text.json"
            if fetch_report:
                write_changed(fetch_path, json_bytes(fetch_report))
        for card_id in sorted(ids):
            raw = texts[card_id]
            if lang == "zh-cn":
                row = raw
            elif lang == "th":
                row = english_rows[card_id]
            else:
                row = candidates[card_id]
                if pack:
                    row = apply_prompt_translations(row, raw, lang, pack, mappings,
                                                    {**seeds, **candidates, **merged})
            merged[card_id] = row
            unformatted_rows[card_id] = row
            fields = translated_row(row, raw, "en" if lang == "th" else lang, card_id in system_ids)
            old_source_hash = old_source_hashes.get(str(card_id))
            if old_source_hash and old_source_hash != source_hashes[str(card_id)] and lang not in ("zh-cn", "th"):
                if str(card_id) in old_source_fields and str(card_id) in old_text_fields:
                    current_fields = text_field_hashes(row)
                    needs_review = any(
                        old_source_fields[str(card_id)].get(field) != source_fields[str(card_id)].get(field)
                        and old_text_fields[str(card_id)].get(field) == current_fields.get(field)
                        for field in TEXT_FIELDS[1:])
                else:
                    # Migration from receipts written before per-field review tracking.
                    needs_review = old_published_hashes.get(str(card_id)) == digest(json_bytes(list(row)))
                if needs_review:
                    fields.append("source_changed")
            if fields:
                unresolved.append({"id": card_id, "name": row[1], "fields": fields})
            # Presentation changes occur after source review, so reformatting an
            # old translation cannot falsely approve an upstream effect change.
            formatted = list(row)
            formatted[2] = encode_rush_description(row[2] or "", "en" if lang == "th" else lang)
            merged[card_id] = tuple(formatted)
        if lang == "en":
            english_rows = unformatted_rows
            english_pending = unresolved
        elif lang == "th":
            # Thai is the English fallback, including review requirements. Its own
            # previous text must never approve an unreviewed English source change.
            unresolved = english_pending
        write_changed(locale_root / "Base Files/rush-pre-release.cdb", cdb_bytes(datas, texts))
        # A pending locale must not rewrite old text merely for presentation:
        # otherwise the next run could mistake formatting for translator review.
        pending_workspaces[workspace] = cdb_bytes(datas, unformatted_rows)
        formatted_workspaces[workspace] = cdb_bytes(datas, merged)
        report = {"source_cards": len(source_datas), "active_cards": len(ids),
                  "excluded_ids": sorted(set(source_datas) & excluded.keys()),
                  "removed_ids": sorted(set(old_datas) - ids),
                  "pending_translations": unresolved, "missing_artwork": missing_art,
                  "script_files": len(scripts), "artwork_files": len(artwork),
                  "published_text_sha256": old_published_hashes,
                  "published_source_sha256": old_source_hashes,
                  "published_source_fields": old_source_fields,
                  "published_text_fields": old_text_fields,
                  "text_sha256": {str(key): digest(json_bytes(list(merged[key]))) for key in sorted(ids)}}
        # Removal history belongs to git; keeping last-run counts would churn on a no-op run.
        report.pop("removed_ids")
        write_changed(locale_root / "report.json", json_bytes(report))
        reports[folder] = report
        # Pending locales keep their own last-published review baselines.
        publication_fields[folder] = {str(key): text_field_hashes(merged[key]) for key in sorted(ids)}
        if unresolved:
            pending_languages.append(lang)
            continue
        card_json = {str(key): {**dict(zip(DATA_FIELDS, datas[key])),
                               **dict(zip(TEXT_FIELDS[1:], merged[key][1:]))} for key in sorted(ids)}
        package = {"rush-pre-release.cdb": cdb_bytes(datas, merged), **scripts, **artwork}
        outputs[root / folder / "rush-pre-release.ypk"] = zip_bytes(package)
        outputs[root / folder / "rush-pre-release.json"] = json_bytes(card_json)
    pending_locales = {LANGS[lang][0]: len(reports[LANGS[lang][0]]["pending_translations"])
                       for lang in pending_languages}
    ready_locales = sorted(reports.keys() - pending_locales.keys())
    # Preflight all locale databases, retirement archives and version files before
    # replacing any release, so a real build error cannot publish a partial run.
    retired_outputs, retired = prepare_retired_releases(root, pending_languages, ids)
    outputs.update(retired_outputs)
    updated_locales = prepare_versions(outputs)
    for path, data in pending_workspaces.items():
        write_changed(path, data if path.parents[2].name in pending_locales else formatted_workspaces[path])
    for path, data in outputs.items():
        write_changed(path, data)
    for folder, report in reports.items():
        if folder in pending_locales:
            continue
        report["published_text_sha256"] = report["text_sha256"]
        report["published_source_sha256"] = source_hashes
        report["published_source_fields"] = source_fields
        report["published_text_fields"] = publication_fields[folder]
        write_changed(root / folder / "Rush/report.json", json_bytes(report))
    receipt = {"version": 2, "source_revision": revision(source), "built_locales": ready_locales,
               "pending_locales": pending_locales,
               "translations_revision": revision(translations_root.parents[1]) if translations_root else None,
               "patch_sha256": digest((source / "RD Patch.cdb").read_bytes()),
               "source_fingerprint": file_fingerprint(source, [source / "RD Patch.cdb"] + ([source / "sets.md"] if (source / "sets.md").exists() else []) + list((source / "script").glob("*.lua"))),
               "local_inputs_sha256": local_inputs_sha256(root),
               "card_ids": sorted(ids), "excluded_ids": sorted(set(source_datas) & excluded.keys()),
               "text_sha256": source_hashes}
    write_changed(root / "Shared/Rush/source.json", json_bytes(receipt))
    return {"status": "partial" if pending_locales and ready_locales else "pending_translations" if pending_locales else "ready",
            "source_cards": len(source_datas), "packaged_cards": len(ids) if ready_locales else 0,
            "locales": sorted(reports), "artworks": len(artwork), "missing_artwork": missing_art,
            "scripts": len(scripts), "pending_translations": sum(pending_locales.values()),
            "ready_locales": ready_locales, "updated_locales": updated_locales,
            "pending_locales": pending_locales, "retired_ids": retired}


def write_summary(path: Path, result: dict) -> None:
    pending = result.get("pending_locales", {})
    lines = ["## Rush translation update", "",
             "Completed locales publish independently. Translator workspaces and reports were saved.", "",
             "| Locale | Cards needing translation/review | Status |", "| --- | ---: | --- |"]
    for locale in result["locales"]:
        count = pending.get(locale, 0)
        if count:
            state = "Pending; publication withheld"
            if locale in result["retired_ids"]:
                state = "Pending; retired cards removed from last-good release"
        else:
            state = "Published" if locale in result["updated_locales"] else "Ready; already current"
        lines.append(f"| {locale} | {count} | {state} |")
    if pending:
        lines += ["", "See each locale's `Rush/report.json` for card IDs and fields. "
                  "Edit `Rush/Workspace/rush-pre-release.cdb` to complete the pending translations."]
        if result.get("retired_ids"):
            lines += ["", "Retired cards were removed from the last-good packages in: " +
                      ", ".join(sorted(result["retired_ids"])) + ". Retained cards keep their published text and scripts."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync",))
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--translations-root", type=Path)
    parser.add_argument("--lang", choices=("all", *LANGS), default="all")
    parser.add_argument("--offline", action="store_true", help="Use local translations only; skip automatic web source imports")
    parser.add_argument("--summary-file", type=Path, help="Write a Markdown build outcome for the Actions job summary")
    args = parser.parse_args()
    try:
        result = synchronize(args.repo_root.resolve(), args.source_root.resolve(),
                             args.translations_root.resolve() if args.translations_root else None,
                             list(LANGS) if args.lang == "all" else [args.lang], fetch_source_text=not args.offline)
    except (ValueError, sqlite3.Error, OSError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Rush sync failed: {error}\n")
    if args.summary_file:
        write_summary(args.summary_file, result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
