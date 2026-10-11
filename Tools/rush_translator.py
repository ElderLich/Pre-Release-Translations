#!/usr/bin/env python3
"""Per-locale Rush workspaces backed only by the published TransSuperpre repo."""
from __future__ import annotations

from pathlib import Path

from rush_prerelease import TEXT_FIELDS, cdb_bytes, read_cdb, write_changed, json_bytes

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


def refresh_locale(repo_root: Path, local_root: Path, folder: str, *, keep_local=False, log=print) -> Path:
    published, local, baseline, reports = paths(repo_root, local_root, folder)
    payload, report = merged_workspace(published, local, baseline, keep_local=keep_local)
    check_conflicts(reports, report)
    write_changed(local, payload)
    write_changed(baseline, published.read_bytes())
    for name in ("report.json", "source_text.json"):
        source = published.parent.parent / name
        if source.is_file():
            write_changed(reports / name, source.read_bytes())
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
