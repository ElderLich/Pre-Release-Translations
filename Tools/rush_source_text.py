#!/usr/bin/env python3
"""Exact set-code source matching for the current Rush patch; never expands IDs."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".github/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from card_text_autofill import fetch_ocg_cards, fetch_yugipedia_cards, manual_source_report, needs_source_text

SUPPORTED = {"en", "fr", "jp"}
SET_LINE = re.compile(r"\((RD/[A-Z0-9]+-JP[A-Z]?)\).*?`(\d+X+)`", re.I)
SET_CODE = re.compile(r"RD/[A-Z0-9]+-JP[A-Z]?\d{2,3}")


def identity_map(sets_text: str, ids: set[int], overrides: dict) -> tuple[dict[int, str], dict[int, str]]:
    """Use upstream's explicit ID ranges, including bounded XX promo subranges."""
    ranges = [(code.upper(), pattern.upper()) for code, pattern in SET_LINE.findall(sets_text)]
    resolved, issues = {}, {}
    for card_id in sorted(ids):
        value = str(card_id)
        matches = set()
        for code, pattern in ranges:
            prefix = pattern.rstrip("X")
            if len(value) == len(pattern) and value.startswith(prefix):
                # Three-digit numbers also apply to bounded XX promo ranges.
                # Lettered print groups use the published JPA01-style suffix.
                number = int(value[len(prefix):])
                width = 2 if re.search(r"-JP[A-Z]$", code) else 3
                matches.add(code + f"{number:0{width}d}")
        explicit = overrides.get(str(card_id))
        if explicit is not None:
            code = explicit.get("set_code", "") if isinstance(explicit, dict) else ""
            if not isinstance(code, str) or not SET_CODE.fullmatch(code) or not explicit.get("evidence"):
                issues[card_id] = "Invalid override: provide exact set_code and evidence URL/note"
                continue
            matches = {code}
        if len(matches) == 1:
            resolved[card_id] = matches.pop()
        else:
            issues[card_id] = "Ambiguous upstream ID range" if matches else "No upstream sets.md range or reviewed mapping"
    return resolved, issues


def fetch_rows(root: Path, source: Path, original: dict, rows: dict, lang: str,
               system_ids: set[int], log=print) -> tuple[dict, dict]:
    """Fill only blank/source-identical untranslated fields; existing edits win."""
    if lang in {"kr", "ko"}:
        report = manual_source_report(lang)
        log(f"[SourceText] {report['reason']}")
        return rows, report
    if lang not in SUPPORTED:
        return rows, {}
    ids = {card_id for card_id, row in rows.items() if card_id not in system_ids
           and any(needs_source_text(row[i] or "", original[card_id][i] or "", lang) for i in (1, 2))}
    path = root / "Shared/Rush/source_mappings.json"
    mappings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(mappings, dict) or not isinstance(mappings.get("cards", {}), dict):
        raise ValueError("Rush source_mappings.json must contain a cards object")
    slug_map = mappings.get("ocg_slugs", {})
    if not isinstance(slug_map, dict) or any(not isinstance(value, str) or not value for value in slug_map.values()):
        raise ValueError("Rush source_mappings.json ocg_slugs values must be nonempty strings")
    sets_path = source / "sets.md"
    sets_text = sets_path.read_text(encoding="utf-8-sig") if sets_path.is_file() else ""
    identities, issues = identity_map(sets_text, ids, mappings.get("cards", {}))
    cards = {}
    diagnostics = {}
    if lang == "jp":
        slug_overrides = mappings.get("ocg_slugs", {})
        slugs = {slug_overrides.get(code.split("/", 1)[1].split("-", 1)[0],
                                    code.split("/", 1)[1].split("-", 1)[0].lower())
                 for code in identities.values()}
        cards = fetch_ocg_cards(sorted(slugs), log, rush=True)
    else:
        cards = fetch_yugipedia_cards(list(identities.values()), log, lang=lang, rush=True, diagnostics=diagnostics)
    result, matches = dict(rows), {}
    for card_id, code in identities.items():
        card = cards.get(code)
        if not card:
            issues[card_id] = diagnostics.get(code, {}).get("reason", "No complete localized card text at the verified set code (or source unavailable)")
            continue
        row = list(rows[card_id])
        changed = []
        for index, value in ((1, card.name), (2, card.desc)):
            if value and needs_source_text(row[index] or "", original[card_id][index] or "", lang):
                if row[index] != value:
                    row[index] = value
                    changed.append("name" if index == 1 else "desc")
        result[card_id] = tuple(row)
        matches[str(card_id)] = {"set_code": code, "source": card.source, "fields": changed}
    report = {"language": lang, "requested_ids": sorted(ids), "matches": matches,
              "unresolved": [{"id": card_id, "set_code": identities.get(card_id),
                              **diagnostics.get(identities.get(card_id), {}), "reason": reason}
                             for card_id, reason in sorted(issues.items())]}
    return result, report
