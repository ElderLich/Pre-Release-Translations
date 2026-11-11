"""Export the patch menu's Standard and Rush source texts for offline review."""

from __future__ import annotations

from contextlib import closing, contextmanager
import csv
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory

if __package__:
    from .PromptPatcher import normalize_rush_prompt
    from .rush_local import workspace_lock
else:
    from PromptPatcher import normalize_rush_prompt
    from rush_local import workspace_lock


DEFAULT_STANDARD_ZH = Path(r"D:\Programs\GitHub\ygopro-database-elder\locales\zh-CN")
DEFAULT_STANDARD_EN = Path(r"D:\Programs\GitHub\ygopro-database-elder\locales\en-US")
DEFAULT_RUSH_ZH = Path(r"D:\Programs\GitHub\ygopro-rush-duel")
DEFAULT_RUSH_EN = Path(r"D:\Modding\Card Replace Database Rush\DBs\en")

SLOTS = tuple(f"str{i}" for i in range(1, 17))
TEXT_COLUMNS = ("id", "name", "desc", *SLOTS)
REVIEW_COLUMNS = (
    "id", "slot", "zh_source", "zh_name", "zh_prompt",
    "en_name", "en_prompt", "status", "catalog_key",
)
RUSH_DATABASES = ("RD Standard.cdb", "RD Alternate.cdb", "RD Patch.cdb")


@contextmanager
def _open_source(path):
    uri = path.resolve().as_uri() + "?mode=ro"
    with path.open("rb") as stream:
        wal_mode = stream.read(20)[18:20] == b"\x02\x02"
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    if not wal_mode or (wal.is_file() and shm.is_file()):
        # SQLite takes a consistent snapshot including any uncheckpointed data.
        with closing(sqlite3.connect(uri, uri=True)) as db:
            yield db
        return

    # The checked-in English CDB has a WAL-mode header but no sidecars.
    # Reading it directly can create them. Work on a private copy and retain
    # any shipped WAL (SQLite's immutable mode would silently ignore it).
    with TemporaryDirectory(prefix="prompt-source-") as temp:
        copy = Path(temp) / path.name
        signature = _snapshot_signature(path, wal, shm)
        shutil.copyfile(path, copy)
        if signature[1] is not None:
            shutil.copyfile(wal, Path(str(copy) + "-wal"))
        if signature != _snapshot_signature(path, wal, shm):
            raise ValueError(f"Prompt source changed during extraction: {path}. Retry the export.")
        with closing(sqlite3.connect(copy)) as db:
            yield db


def _snapshot_signature(*paths):
    signature = []
    for path in paths:
        try:
            status = path.stat()
            signature.append((status.st_size, status.st_mtime_ns))
        except FileNotFoundError:
            signature.append(None)
    return signature


def _read_texts(path: Path) -> dict[int, sqlite3.Row]:
    if not path.is_file():
        raise FileNotFoundError(f"Prompt source database is missing: {path}")
    rows = {}
    try:
        with _open_source(path) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute(f"SELECT {', '.join(TEXT_COLUMNS)} FROM texts"):
                card_id = row["id"]
                if not isinstance(card_id, int) or card_id in rows:
                    raise ValueError(f"Invalid or duplicate card ID {card_id!r} in {path}")
                if any(value is not None and not isinstance(value, str) for value in row[1:]):
                    raise ValueError(f"Invalid text field for card {card_id} in {path}")
                rows[card_id] = row
    except sqlite3.Error as error:
        raise ValueError(f"Cannot read the texts table in {path}: {error}") from error
    return rows


def _review_rows(chinese, english, source_names, *, rush):
    for card_id in sorted(chinese.keys() | english.keys()):
        zh = chinese.get(card_id)
        en = english.get(card_id)
        for slot in SLOTS:
            zh_prompt = (zh[slot] or "") if zh is not None else ""
            en_prompt = (en[slot] or "") if en is not None else ""
            has_zh, has_en = bool(zh_prompt.strip()), bool(en_prompt.strip())
            if not has_zh and not has_en:
                continue
            status = "paired"
            if not has_zh:
                status = "missing_zh"
            elif not has_en:
                status = "missing_en"
            yield (
                card_id, slot, source_names.get(card_id, ""),
                zh["name"] if zh is not None else "", zh_prompt,
                en["name"] if en is not None else "", en_prompt, status,
                normalize_rush_prompt(zh_prompt) if rush and has_zh else "",
            )


def _write_csv(path, columns, rows):
    count = 0
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def _publish(staged, destination, filenames):
    # An open CSV in Excel can block replacement on Windows. Keep the previous
    # files until every replacement succeeds, even for a late lock failure.
    previous = staged / "previous"
    previous.mkdir()
    for name in filenames:
        if (destination / name).exists():
            shutil.copy2(destination / name, previous / name)
    installed = []
    try:
        for name in filenames:
            (staged / name).replace(destination / name)
            installed.append(name)
    except OSError:
        for name in reversed(installed):
            if (previous / name).exists():
                (previous / name).replace(destination / name)
            else:
                (destination / name).unlink()
        raise


def export_prompt_sources(
    output_dir,
    *,
    standard_zh=DEFAULT_STANDARD_ZH,
    standard_en=DEFAULT_STANDARD_EN,
    rush_zh=DEFAULT_RUSH_ZH,
    rush_en=DEFAULT_RUSH_EN,
) -> dict[str, int]:
    """Export full texts tables and exact ID/slot comparisons; return CSV row counts.

    All four source arguments are directories. If a Chinese Rush ID appears
    in multiple inputs, the entire RD Patch row wins over Alternate, and the
    entire Alternate row wins over Standard. Empty slots in a newer row are
    authoritative. English references are never borrowed from a different ID.
    """
    standard_chinese = _read_texts(Path(standard_zh) / "cards.cdb")
    standard_english = _read_texts(Path(standard_en) / "cards.cdb")
    rush_chinese = {}
    rush_source_names = {}
    for name in RUSH_DATABASES:
        rows = _read_texts(Path(rush_zh) / name)
        rush_chinese.update(rows)
        rush_source_names.update(dict.fromkeys(rows, name))
    rush_english = _read_texts(Path(rush_en) / "rush_cards.cdb")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    counts = {}
    busy = f"Another prompt export is already running in {destination}. Wait for it to finish."
    with workspace_lock(destination, "prompt-export", busy), TemporaryDirectory(prefix=".prompt-export-", dir=destination) as temp:
        staged = Path(temp)
        for name, rows in (
            ("texts.csv", standard_chinese), ("texts_en.csv", standard_english),
            ("rush_texts.csv", rush_chinese), ("rush_texts_en.csv", rush_english),
        ):
            counts[name] = _write_csv(staged / name, TEXT_COLUMNS, (rows[key] for key in sorted(rows)))
        counts["standard_prompts.csv"] = _write_csv(
            staged / "standard_prompts.csv", REVIEW_COLUMNS,
            _review_rows(standard_chinese, standard_english, dict.fromkeys(standard_chinese, "cards.cdb"), rush=False),
        )
        counts["rush_prompts.csv"] = _write_csv(
            staged / "rush_prompts.csv", REVIEW_COLUMNS,
            _review_rows(rush_chinese, rush_english, rush_source_names, rush=True),
        )
        _publish(staged, destination, counts)
    return counts
