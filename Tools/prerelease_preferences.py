"""Local desktop preferences and a windowless database-editor launch."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess


def translations_root(repo: Path, configured: str = "") -> str:
    """Repair the renamed sibling checkout without replacing custom sources."""
    current = repo.resolve().parent / "MDPro4_Data/Data/locales"
    if not configured:
        return str(current)
    selected = Path(configured).expanduser()
    previous = repo.resolve().parent / "MDPro3_Data/Data/locales"
    if (selected.is_absolute() and selected.resolve() == previous
            and not selected.exists() and current.is_dir()):
        return str(current)
    return configured


def find_editor(configured: str = "") -> Path | None:
    # An explicit preference wins; never silently replace an invalid chosen path.
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    candidates = [os.environ.get("DATAEDITORX_PATH", ""), shutil.which("DataEditorX.exe"),
                  "D:/Modding/Programs/DataEditorX/DataEditorX.exe",
                  Path.home() / "Programs/DataEditorX/DataEditorX.exe"]
    for root in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if root:
            candidates.append(Path(root) / "DataEditorX/DataEditorX.exe")
    return next((Path(value) for value in candidates if value and Path(value).is_file()), None)


def database_path(local: Path, lang: str, rush: bool) -> Path:
    return local / lang / ("Rush/raw2/rush-pre-release.cdb" if rush else "raw2/test-release.cdb")


def launch_editor(editor: Path, database: Path):
    if not editor.is_file() or not database.is_file():
        raise FileNotFoundError(editor if not editor.is_file() else database)
    return subprocess.Popen([str(editor.resolve()), str(database.resolve())], cwd=editor.parent,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
