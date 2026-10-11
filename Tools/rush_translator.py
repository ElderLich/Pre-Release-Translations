#!/usr/bin/env python3
"""Per-locale Rush workspaces backed only by the published Pre-Release Translations repo."""
from __future__ import annotations

from pathlib import Path

from rush_prerelease import TEXT_FIELDS, cdb_bytes, read_cdb, write_changed, json_bytes, read_json

CDB_NAME = "rush-pre-release.cdb"


def paths(repo_root: Path, local_root: Path, folder: str) -> tuple[Path, Path, Path, Path]:
    published = repo_root / folder / "Rush/Workspace" / CDB_NAME
    local = local_root / folder / "Rush/raw2" / CDB_NAME
    baseline = local_root / folder / "Rush/Base Files" / CDB_NAME
    reports = local_root / folder / "Rush/Reports"
    if local.resolve() == published.resolve():
        raise ValueError("Keep the translator's Rush/raw2 folder separate from the repository Workspace.")
    return published, local, baseline, reports


def merged_workspace(published: Path, local: Path, baseline: Path, *, keep_local=False) -> tuple[bytes, dict]:
    """Authoritative published IDs/gameplay; merge text using last downloaded values."""
    datas, incoming = read_cdb(published)
    old_datas, edited = read_cdb(local, optional=True)
    _, before = read_cdb(baseline, optional=True)
    result, conflicts = {}, []
    for card_id, remote in incoming.items():
        row = list(remote)
        if card_id in edited:
            original = before.get(card_id)
            for index, field in enumerate(TEXT_FIELDS[1:], 1):
                local_value = edited[card_id][index]
                # Existing translator files without a baseline are adopted safely:
                # their text wins; only inventory/gameplay follows the repository.
                changed_here = original is None or local_value != original[index]
                if not changed_here:
                    continue
                changed_there = original is not None and remote[index] != original[index]
                if changed_there and remote[index] != local_value:
                    conflicts.append({"id": card_id, "field": field,
                                      "previous": original[index], "local": local_value,
                                      "published": remote[index]})
                row[index] = local_value
        result[card_id] = tuple(row)
    report = {"active_cards": len(datas), "added_ids": sorted(set(datas) - set(old_datas)),
              "removed_ids": sorted(set(old_datas) - set(datas)), "conflicts": [] if keep_local else conflicts,
              "resolved_using_local": conflicts if keep_local else [],
              "adopted_existing_workspace": local.is_file() and not baseline.is_file()}
    return cdb_bytes(datas, result), report


def check_conflicts(reports: Path, report: dict) -> None:
    write_changed(reports / "sync.json", json_bytes(report))
    if report["conflicts"]:
        raise ValueError(f"{len(report['conflicts'])} Rush translation fields changed both locally and in the repository. "
                         f"No card database was overwritten. Compare local/published values in {reports / 'sync.json'}, "
                         "accept the published values in Rush/raw2 and refresh again, or explicitly keep your local versions "
                         "with rush-refresh --keep-local after reviewing that report.")


def refresh_locale(repo_root: Path, local_root: Path, folder: str, *, keep_local=False, fetch_text=True, log=print) -> Path:
    published, local, baseline, reports = paths(repo_root, local_root, folder)
    payload, report = merged_workspace(published, local, baseline, keep_local=keep_local)
    check_conflicts(reports, report)
    write_changed(local, payload)
    write_changed(baseline, published.read_bytes())
    for name in ("report.json", "source_text.json"):
        source = published.parent.parent / name
        if source.is_file():
            write_changed(reports / name, source.read_bytes())
    if fetch_text:
        fill_source_text(repo_root, local, reports, folder, log)
    log(f"{folder} Rush ready: {report['active_cards']} cards; "
        f"{len(report['added_ids'])} added, {len(report['removed_ids'])} retired.")
    log(f"Translate this file: {local}")
    if folder == "TH":
        log("Thai Rush uses the English package; submit Rush text corrections in EN.")
    elif folder == "ZH-CN":
        log("Simplified Chinese Rush text is upstream-managed; this local copy is for reference.")
    return local


def copy_locale(repo_root: Path, local_root: Path, folder: str, *, dry_run=False, log=print) -> list[Path]:
    published, local, baseline, reports = paths(repo_root, local_root, folder)
    if folder == "TH":
        raise ValueError("Thai Rush is generated from English. Submit Rush text corrections through EN.")
    if folder == "ZH-CN":
        raise ValueError("Simplified Chinese Rush text is managed by upstream RD Patch.cdb. This workspace is read-only; submit source corrections upstream.")
    if not local.is_file():
        raise ValueError(f"No local Rush translation file: {local}. Refresh Rush first.")
    payload, report = merged_workspace(published, local, baseline)
    if not dry_run:
        check_conflicts(reports, report)
    elif report["conflicts"]:
        raise ValueError("Rush translation conflicts need resolution before uploading.")
    if dry_run:
        log(f"Would copy only {folder} Rush translations -> {published}")
    else:
        write_changed(published, payload)
        log(f"Copied {folder} Rush translations -> {published}")
    return [published]


def fill_source_text(repo_root: Path, local: Path, reports: Path, folder: str, log=print):
    """Fetch untranslated fields with the source snapshot shipped in this repo."""
    from rush_source_text import fetch_rows, SUPPORTED
    lang = folder.lower()
    if lang not in SUPPORTED and lang != "kr":
        return
    source = repo_root / folder / "Rush/Base Files" / CDB_NAME
    if not source.is_file():
        log("[SourceText] No upstream text snapshot published yet; local translations kept.")
        return
    datas, rows = read_cdb(local)
    _, original = read_cdb(source)
    # Only rows with a matching source snapshot can be safely judged untranslated.
    candidates = {key: row for key, row in rows.items() if key in original}
    system_ids = set(read_json(repo_root / "Shared/Rush/config.json", {}).get("system_ids", []))
    fetched, report = fetch_rows(repo_root, repo_root / "Shared/Rush", original, candidates, lang, system_ids, log)
    from card_text_autofill import encode_rush_description
    # A translator may save an already-open editor while the web requests run.
    # Re-read their latest file and only replace fields unchanged since lookup.
    latest_datas, latest = read_cdb(local)
    if latest_datas != datas or latest.keys() != rows.keys():
        raise ValueError("Rush database inventory changed while fetching text. "
                         "Your latest database was preserved; refresh again after editing.")
    for key, row in fetched.items():
        value, applied = list(latest[key]), []
        for index, field in ((1, "name"), (2, "desc")):
            if row[index] != rows[key][index] and latest[key][index] == rows[key][index]:
                value[index] = encode_rush_description(row[index], lang) if index == 2 else row[index]
                applied.append(field)
        latest[key] = tuple(value)
        if str(key) in report.get("matches", {}):
            report["matches"][str(key)]["fields"] = applied
    if report:
        write_changed(reports / "source_text.json", json_bytes(report))
        log(f"[SourceText] {folder}: {len(report.get('matches', {}))} matches; "
            f"{len(report.get('unresolved', []))} cards still need review.")
    write_changed(local, cdb_bytes(datas, latest))
