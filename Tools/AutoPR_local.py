#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal local wrapper for Pre-Release Translations GitHub Actions.

Copy this file into a local language folder and rename it to AutoPR.py, for
example:

    Super-Pre Translations/ES/AutoPR.py
    Super-Pre Translations/JP/AutoPR.py

Opening this file launches the shared Standard/Rush window from the repository.
Use --menu for the legacy numbered menu; existing command arguments still work.
The standard build runs in GitHub Actions. This wrapper can refresh the local raw2
workspace from the latest upstream package, then copy it into the Pre-Release Translations
repository and push it to GitHub.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SUPPORTED_LANGS = {
    "de": "DE",
    "en": "EN",
    "es": "ES",
    "fr": "FR",
    "it": "IT",
    "jp": "JP",
    "kr": "KR",
    "pt": "PT",
    "th": "TH",
}

SCRIPT_DIR = Path(__file__).resolve().parent
LOCAL_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = LOCAL_ROOT / ".transsuperpre_local.json"
REPO_ENV_NAMES = ("TRANSSUPERPRE_REPO", "TRANSSUPERPRE_ROOT")
LANG_ENV_NAME = "TRANSSUPERPRE_LANG"
COMMON_REPO_PATHS = (
    # Recognize new clones while retaining existing checkout paths and preferences.
    Path(r"D:\Programs\GitHub\Pre-Release-Translations"),
    Path.home() / "Documents" / "GitHub" / "Pre-Release-Translations",
    Path.home() / "GitHub" / "Pre-Release-Translations",
    Path(r"D:\Programs\GitHub\TransSuperpre"),
    Path.home() / "Documents" / "GitHub" / "TransSuperpre",
    Path.home() / "GitHub" / "TransSuperpre",
)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(repo_root: Path) -> None:
    data = load_config()
    data["repo_root"] = str(repo_root)
    CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def normalize_lang(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().lower().replace("_", "-")
    if key in SUPPORTED_LANGS:
        return key
    for lang, folder in SUPPORTED_LANGS.items():
        if key == folder.lower():
            return lang
    return None


def detect_lang(*, gui: bool = False) -> str:
    env_lang = normalize_lang(os.environ.get(LANG_ENV_NAME))
    if env_lang:
        return env_lang

    folder_lang = normalize_lang(SCRIPT_DIR.name)
    if folder_lang:
        return folder_lang

    if gui:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        try:
            while True:
                value = simpledialog.askstring("Pre-Release Translations", "Card language: " + ", ".join(SUPPORTED_LANGS.values()), parent=root)
                if value is None:
                    raise SystemExit(0)
                lang = normalize_lang(value)
                if lang:
                    return lang
        finally:
            root.destroy()
    print("Could not detect the language from the folder name.")
    print("Put this file inside a folder named one of:")
    print("  " + ", ".join(sorted(SUPPORTED_LANGS.values())))
    print(f"Or set {LANG_ENV_NAME}=es, jp, kr, de, en, fr, it, pt, or th.")
    while True:
        raw = input("Language: ").strip()
        lang = normalize_lang(raw)
        if lang:
            return lang
        print("Unsupported language.")


def helper_path(repo_root: Path) -> Path:
    return repo_root / "Tools" / "local_workspace.py"


def is_valid_repo(repo_root: Path) -> bool:
    return helper_path(repo_root).exists()


def repo_from_env() -> Path | None:
    for name in REPO_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser()
    return None


def repo_from_config() -> Path | None:
    value = load_config().get("repo_root")
    return Path(value).expanduser() if value else None


def prompt_repo_root(*, gui: bool = False) -> Path:
    if gui:
        import tkinter as tk
        from tkinter import filedialog, messagebox
        root = tk.Tk()
        root.withdraw()
        try:
            while True:
                value = filedialog.askdirectory(parent=root, title="Select Pre-Release Translations repository")
                if not value:
                    raise SystemExit(0)
                repo_root = Path(value)
                if is_valid_repo(repo_root):
                    save_config(repo_root)
                    return repo_root
                messagebox.showerror("Pre-Release Translations", "This folder does not contain Tools/local_workspace.py.", parent=root)
        finally:
            root.destroy()
    print("Pre-Release Translations repo path was not found.")
    print("Paste the folder that contains Tools\\local_workspace.py.")
    while True:
        raw = input("Pre-Release Translations repo path: ").strip().strip('"')
        if not raw:
            print("Path is required.")
            continue
        repo_root = Path(raw).expanduser()
        if is_valid_repo(repo_root):
            save_config(repo_root)
            return repo_root
        print(f"Could not find Tools\\local_workspace.py under: {repo_root}")


def resolve_repo_root(*, force_prompt: bool = False, gui: bool = False) -> Path:
    if force_prompt:
        return prompt_repo_root(gui=gui)
    for candidate in (repo_from_env(), repo_from_config(), *COMMON_REPO_PATHS):
        if candidate and is_valid_repo(candidate):
            return candidate
    return prompt_repo_root(gui=gui)


def run_tool(*args: str) -> int:
    repo_root = resolve_repo_root()
    tool = helper_path(repo_root)
    cmd = [
        sys.executable,
        str(tool),
        "--repo-root",
        str(repo_root),
        "--local-root",
        str(LOCAL_ROOT),
        *args,
    ]
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode


def menu(lang: str) -> str:
    print("Pre-Release Translations local workspace helper")
    print(f"Language  : {lang.upper()}")
    print(f"Local root: {LOCAL_ROOT}")
    repo_root = resolve_repo_root()
    print(f"Repo root : {repo_root}")
    print()
    print("1 = Refresh local raw2 and Mappings.csv from latest .ypk")
    print("2 = Upload raw2 workspace and push to GitHub")
    print("3 = Copy raw2 workspace into repo only")
    print(f"4 = Pull latest {lang.upper()} workspace from GitHub")
    print("5 = Show repo and Actions status")
    print("6 = Change Pre-Release Translations repo path")
    print("7 = Exit")
    print(f"8 = Refresh {lang.upper()} Rush from published workspace")
    print(f"9 = Open {lang.upper()} Rush translation folder")
    print(f"10 = Upload {lang.upper()} Rush translations and push to GitHub")
    print(f"11 = Copy {lang.upper()} Rush translations into repo only")
    print(f"12 = Open {lang.upper()} Rush reports")
    print("13 = Resolve reviewed Rush conflicts by keeping local text")
    return input("Option: ").strip()


def gui_requested(argv=None) -> bool:
    argv = sys.argv if argv is None else argv
    return len(argv) == 1 or argv[1].lower() == "gui"


def relaunch_windowless() -> bool:
    """Only relaunch our GUI process; CLI commands keep their normal console."""
    if os.name != "nt" or not gui_requested() or Path(sys.executable).stem.lower().startswith("pythonw"):
        return False
    executable = Path(sys.executable).with_name("pythonw.exe")
    if not executable.is_file():
        return False
    subprocess.Popen([str(executable), str(Path(__file__).resolve()), *sys.argv[1:]],
                     cwd=SCRIPT_DIR, creationflags=subprocess.CREATE_NO_WINDOW,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def main() -> int:
    if relaunch_windowless():
        return 0
    gui = gui_requested()
    lang = detect_lang(gui=gui)
    if gui:
        repo_root = resolve_repo_root(gui=True)
        sys.path.insert(0, str(repo_root / "Tools"))
        from prerelease_gui import launch
        launch(repo_root, LOCAL_ROOT, lang)
        return 0
    if sys.argv[1] != "--menu":
        command = sys.argv[1].lower()
        if command == "rush-pull":
            return run_tool("rush-pull", "--lang", lang, *sys.argv[2:])
        if command in {"rush-refresh", "rush-update"}:
            return run_tool("rush-refresh", "--lang", lang, *sys.argv[2:])
        if command in {"rush-upload", "rush-push"}:
            return run_tool("rush-upload", "--lang", lang, "--push", *sys.argv[2:])
        if command in {"rush-copy", "rush-sync"}:
            return run_tool("rush-upload", "--lang", lang, *sys.argv[2:])
        if command in {"rush-open", "rush-reports"}:
            return run_tool(command, "--lang", lang, *sys.argv[2:])
        if command in {"refresh", "update"}:
            return run_tool("refresh", "--lang", lang, *sys.argv[2:])
        if command in {"upload", "push"}:
            return run_tool("upload", "--lang", lang, "--push", *sys.argv[2:])
        if command in {"copy", "sync"}:
            return run_tool("upload", "--lang", lang, *sys.argv[2:])
        if command == "pull":
            return run_tool("pull", "--lang", lang, *sys.argv[2:])
        if command in {"status", "actions"}:
            return run_tool("status", "--lang", lang, *sys.argv[2:])
        if command in {"config", "setup"}:
            resolve_repo_root(force_prompt=True)
            return 0
        print(f"Unknown command: {command}")
        return 2

    choice = menu(lang)
    if choice == "1":
        return run_tool("refresh", "--lang", lang)
    if choice == "2":
        return run_tool("upload", "--lang", lang, "--push")
    if choice == "3":
        return run_tool("upload", "--lang", lang)
    if choice == "4":
        return run_tool("pull", "--lang", lang)
    if choice == "5":
        return run_tool("status", "--lang", lang)
    if choice == "6":
        resolve_repo_root(force_prompt=True)
        return 0
    if choice == "7":
        return 0
    if choice == "8":
        return run_tool("rush-refresh", "--lang", lang)
    if choice == "9":
        return run_tool("rush-open", "--lang", lang)
    if choice == "10":
        return run_tool("rush-upload", "--lang", lang, "--push")
    if choice == "11":
        return run_tool("rush-upload", "--lang", lang)
    if choice == "12":
        return run_tool("rush-reports", "--lang", lang)
    if choice == "13":
        if input("Keep your local text for every conflict listed in Rush/Reports/sync.json? [y/N]: ").strip().lower() == "y":
            return run_tool("rush-refresh", "--lang", lang, "--no-pull-first", "--keep-local")
        return 0
    print("Invalid option.")
    return 2


def pause_if_menu() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--menu":
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass



if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        exit_code = 130
    except Exception as error:
        if not gui_requested():
            raise
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Pre-Release Translations", str(error), parent=root)
        root.destroy()
    finally:
        pause_if_menu()
    raise SystemExit(exit_code)
