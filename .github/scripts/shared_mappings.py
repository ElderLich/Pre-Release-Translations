#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared locale-aware Mappings.csv helpers."""

from __future__ import annotations

import csv
import re
import sqlite3
from contextlib import closing
from pathlib import Path

LANG_FIELD = "lang"
MAPPING_FIELDS = ["id", "name"] + [f"str{i}" for i in range(1, 17)]
SHARED_FIELDS = [LANG_FIELD, *MAPPING_FIELDS]
GLOBAL_LANGS = {"", "*", "all", "default"}
LANG_ORDER = {
    "all": 0,
    "de": 1,
    "en": 2,
    "es": 3,
    "fr": 4,
    "it": 5,
    "jp": 6,
    "kr": 7,
    "pt": 8,
    "th": 9,
}


def workspace_mapping_names(workspace: sqlite3.Connection, source_cdb: Path | None,
                            *, allow_shared_characters: bool = False) -> dict[int, str]:
    """Keep source-language names out of prompts until a translator can bind them."""
    originals = {}
    if source_cdb is not None:
        with closing(sqlite3.connect(source_cdb.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            originals = {cid: (name, desc) for cid, name, desc in source.execute("SELECT id,name,desc FROM texts")}
    names = {}
    for cid, name, desc in workspace.execute("SELECT id,name,desc FROM texts"):
        if not name:
            continue
        original = originals.get(cid)
        if original and name == original[0] and re.search(r"[\u3400-\u9fff]", name):
            # A Japanese or Korean name can share its written form with the source.
            if not allow_shared_characters or desc == original[1]:
                continue
        names[cid] = name
    return names


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def normalize_lang(value: object) -> str:
    return clean_cell(value).lower()


def _row_to_mapping(row: dict[str, object], card_id: int) -> dict[str, str]:
    mapping = {"id": str(card_id), "name": clean_cell(row.get("name"))}
    for index in range(1, 17):
        mapping[f"str{index}"] = clean_cell(row.get(f"str{index}"))
    return mapping


def _read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return SHARED_FIELDS, []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [{key: clean_cell(value) for key, value in row.items() if key is not None} for row in reader]
    return fields, rows


def load_locale_mappings_csv(path: Path, lang: str | None = None) -> dict[int, dict[str, str]]:
    """Load legacy mappings or the rows for one language from a shared CSV."""
    fields, rows = _read_csv_rows(path)
    target_lang = normalize_lang(lang)
    has_lang = LANG_FIELD in fields

    mappings: dict[int, dict[str, str]] = {}
    for row in rows:
        row_lang = normalize_lang(row.get(LANG_FIELD))
        if has_lang and target_lang and row_lang not in GLOBAL_LANGS:
            continue
        try:
            card_id = int(clean_cell(row.get("id")))
        except ValueError:
            continue
        mappings[card_id] = _row_to_mapping(row, card_id)

    if has_lang and target_lang:
        for row in rows:
            if normalize_lang(row.get(LANG_FIELD)) != target_lang:
                continue
            try:
                card_id = int(clean_cell(row.get("id")))
            except ValueError:
                continue
            mappings[card_id] = _row_to_mapping(row, card_id)

    return mappings


def _canonical_shared_row(row: dict[str, object]) -> dict[str, str]:
    out = {field: "" for field in SHARED_FIELDS}
    for field in SHARED_FIELDS:
        out[field] = clean_cell(row.get(field))
    if normalize_lang(out[LANG_FIELD]) in {"", "*", "default"}:
        out[LANG_FIELD] = "all"
    return out


def _sort_key(row: dict[str, str]) -> tuple[int, str, int]:
    lang = normalize_lang(row.get(LANG_FIELD))
    try:
        card_id = int(clean_cell(row.get("id")))
    except ValueError:
        card_id = 0
    return (LANG_ORDER.get(lang, 100), lang, card_id)


def save_locale_mappings_csv(path: Path, rows: dict[int, dict[str, str]], lang: str | None = None) -> None:
    """Save mappings, preserving other languages when the file is shared."""
    target_lang = normalize_lang(lang)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not target_lang:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MAPPING_FIELDS, lineterminator="\n")
            writer.writeheader()
            for card_id in sorted(rows):
                row = {field: "" for field in MAPPING_FIELDS}
                row.update(rows[card_id])
                row["id"] = str(card_id)
                writer.writerow(row)
        return

    fields, existing_rows = _read_csv_rows(path)
    preserved: list[dict[str, str]] = []
    for row in existing_rows:
        shared_row = _canonical_shared_row(row if LANG_FIELD in fields else {**row, LANG_FIELD: "all"})
        if normalize_lang(shared_row.get(LANG_FIELD)) != target_lang:
            preserved.append(shared_row)

    for card_id in sorted(rows):
        row = {field: "" for field in SHARED_FIELDS}
        row[LANG_FIELD] = target_lang
        row.update({field: clean_cell(rows[card_id].get(field)) for field in MAPPING_FIELDS})
        row["id"] = str(card_id)
        preserved.append(row)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHARED_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in sorted(preserved, key=_sort_key):
            writer.writerow(_canonical_shared_row(row))
