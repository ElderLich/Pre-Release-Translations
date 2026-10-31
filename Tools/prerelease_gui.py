"""Shared translation workspace with Standard, Rush, and all-locale overview tabs."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from rush_local import python_console
from prerelease_i18n import LANGUAGE_NAMES, LocalizedVar, translate
from prerelease_preferences import find_editor, database_path, launch_editor, translations_root
from prerelease_activity import ActivityBar, activity_phase, activity_result

STANDARD_LANGS = ("DE", "EN", "ES", "FR", "IT", "JP", "KR", "PT", "TH")
RUSH_LANGS = (*STANDARD_LANGS, "ZH-CN", "ZH-TW")

COLORS = {
    "background": "#101720", "panel": "#182330", "field": "#111b27",
    "border": "#2b3b4d", "text": "#edf3fa", "muted": "#9caec3",
    "accent": "#70dec6", "accent_hover": "#91edd9", "accent_text": "#092820",
    "hover": "#26394a", "disabled": "#63758b", "log": "#0c131c",
    "warning": "#edc080", "error": "#ff9f9f", "blue": "#9ac5ff",
}


def workspace_command(repo: Path, local: Path, command: str, lang: str) -> list[str]:
    args = [python_console(), "-u", str(repo / "Tools/local_workspace.py"),
            "--repo-root", str(repo), "--local-root", str(local)]
    args += ["upload" if command == "push" else command, "--lang", lang.lower()]
    if command == "push":
        args.append("--push")
    return args


def rush_command(repo: Path, source: Path, translations: Path, *, fetch: bool, offline: bool) -> list[str]:
    args = [python_console(), "-u", str(repo / "Tools/local_workspace.py"),
            "--repo-root", str(repo), "rush", "--source-root", str(source),
            "--translations-root", str(translations)]
    if fetch:
        args.append("--fetch")
    if offline:
        args.append("--offline")
    return args


def rush_workspace_command(repo: Path, local: Path, command: str, lang: str) -> list[str]:
    action = {"refresh": "rush-refresh", "pull": "rush-pull", "upload": "rush-upload",
              "push": "rush-upload", "status": "status", "resolve": "rush-refresh"}[command]
    args = [python_console(), "-u", str(repo / "Tools/local_workspace.py"),
            "--repo-root", str(repo), "--local-root", str(local), action, "--lang", lang.lower()]
    if command == "push":
        args.append("--push")
    elif command == "resolve":
        args += ["--no-pull-first", "--keep-local"]
    return args


def read_json_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a report object: {path.name}")
    return value


def workspace_rows(path: Path) -> dict:
    if not path.is_file():
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as database:
        return {str(row[0]): tuple(row) for row in database.execute("SELECT * FROM texts")}


def row_hash(row: tuple) -> str:
    serialized = json.dumps(list(row), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def locale_snapshot(repo: Path, lang: str) -> dict:
    """Only describe this checkout; inspecting a tab never performs network work."""
    folder = repo / lang
    snapshot = {"lang": lang, "status": "Not built", "cards": "—", "pending": [], "missing": [],
                "version": "Not built", "built": "Not built", "names": {}, "changed": [], "error": None}
    try:
        report = read_json_object(folder / "Rush/report.json")
        snapshot["cards"] = report.get("active_cards", "—")
        snapshot["pending"] = report.get("pending_translations", [])
        snapshot["missing"] = report.get("missing_artwork", [])
        if not isinstance(snapshot["pending"], list) or not isinstance(snapshot["missing"], list):
            raise ValueError("Invalid pending translation or missing artwork list")
        version = folder / "rush-version.txt"
        if version.is_file():
            snapshot["version"] = version.read_text(encoding="utf-8").strip()
            try:
                snapshot["built"] = datetime.fromtimestamp(int(snapshot["version"]), timezone.utc).strftime("%d %b %Y · %H:%M UTC")
            except (ValueError, OverflowError, OSError):
                snapshot["built"] = snapshot["version"]
        rows = workspace_rows(folder / "Rush/Workspace/rush-pre-release.cdb")
        snapshot["names"] = {key: row[1] for key, row in rows.items()}
        hashes = report.get("published_text_sha256", {})
        if not isinstance(hashes, dict):
            raise ValueError("Invalid published text fingerprints")
        if hashes:
            snapshot["changed"] = sorted(key for key in rows.keys() | hashes.keys() if key not in rows or row_hash(rows[key]) != hashes.get(key))
        built = version.is_file() and (folder / "rush-pre-release.ypk").is_file()
        if snapshot["pending"]:
            snapshot["status"] = "Translation pending"
        elif snapshot["changed"]:
            snapshot["status"] = "Workspace changed"
        elif built:
            snapshot["status"] = "Built · art missing" if snapshot["missing"] else "Built"
        if not report:
            snapshot["status"] = "No build report"
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        snapshot["status"], snapshot["error"] = "Read error", str(error)
        snapshot["pending"] = snapshot["pending"] if isinstance(snapshot["pending"], list) else []
        snapshot["missing"] = snapshot["missing"] if isinstance(snapshot["missing"], list) else []
    return snapshot


def snapshot_details(snapshot: dict, tr=lambda value, **kw: value.format(**kw)) -> str:
    lines = [f"{snapshot['lang']} · {tr(snapshot['status'])} · {snapshot['version']}"]
    if snapshot["error"]:
        return "\n".join(lines + [snapshot["error"]])
    names = snapshot["names"]
    for issue in snapshot["pending"]:
        if isinstance(issue, dict):
            card_id = str(issue.get("id", "?"))
            name = issue.get("name") or names.get(card_id) or "Name unavailable"
            fields = ", ".join(issue.get("fields", [])) or "translation review"
            lines.append(f"{tr('Translations')} · {card_id} · {name} · {fields}")
        else:
            lines.append(f"{tr('Translations')} · {issue} · {names.get(str(issue), 'Name unavailable')}")
    for card_id in snapshot["missing"]:
        lines.append(f"{tr('Artwork missing')} · {card_id} · {names.get(str(card_id), 'Name unavailable')}")
    for card_id in snapshot["changed"]:
        lines.append(f"{tr('Workspace changed')} · {card_id} · {names.get(str(card_id), 'Removed from workspace')}")
    if len(lines) == 1:
        lines.append(tr("No missing translations or artwork are listed in the latest local build report."))
    return "\n".join(lines)


def read_reports(repo: Path) -> list[tuple]:
    snapshots = (locale_snapshot(repo, lang) for lang in RUSH_LANGS)
    return [(item["lang"], item["cards"], len(item["pending"]), len(item["missing"]), item["version"]) for item in snapshots]


def stream_job(command: list[str], cwd: Path, events: queue.Queue) -> None:
    """Worker never touches Tk; all UI changes happen in the main-thread poll."""
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1", GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", TRANSSUPERPRE_GUI="1")
    try:
        with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            for line in process.stdout:
                events.put(("log", line))
            code = process.wait()
    except Exception as error:
        events.put(("log", f"Could not run update: {error}\n"))
        code = 1
    events.put(("done", code))


class PrereleaseWindow:
    def __init__(self, root: tk.Tk, repo: Path, local: Path, lang: str):
        self.root, self.repo, self.local = root, repo.resolve(), local.resolve()
        self.config_path = self.local / ".transsuperpre_local.json"
        self.events = queue.Queue()
        self.busy = False
        self.controls = []
        self.job_kind = ""
        self.config = {}
        self.config_error = None
        try:
            if self.config_path.is_file():
                self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
                if not isinstance(self.config, dict):
                    raise ValueError("Expected a settings object")
        except (ValueError, OSError) as error:
            self.config_error = str(error)
            self.config = {}
        self.localized_widgets = {}
        self.localized_vars = []
        self.ui_language = self.config.get("interface_language", "AUTO")
        self.display_language = lang.upper() if lang.upper() in LANGUAGE_NAMES else "EN"
        if self.ui_language in LANGUAGE_NAMES:
            self.display_language = self.ui_language
        self.editor_path = self.config.get("dataeditorx_path", "")
        self.language = tk.StringVar(value=lang.upper() if lang.upper() in STANDARD_LANGS else "EN")
        self.rush_language = tk.StringVar(value=lang.upper() if lang.upper() in RUSH_LANGS else "EN")
        self.source = tk.StringVar(value=self.config.get("rush_source_root", str(repo.parent / "ygopro-rush-duel")))
        self.translations = tk.StringVar(value=translations_root(self.repo, self.config.get("rush_translations_root", "")))
        self.offline = tk.BooleanVar(value=False)
        self.status = LocalizedVar(self, value="No operation running.")
        self.log_visible = tk.BooleanVar(value=self.config.get("show_log", True))
        self.activity_detail = LocalizedVar(self)
        self.status_kind = LocalizedVar(self, value="READY")
        self.report_summary = LocalizedVar(self, value="")
        self.rush_workspace_status = LocalizedVar(self, value="")
        self.rush_workspace_note = LocalizedVar(self, value="")
        self.rush_publish_controls = []
        self.build_tools_visible = tk.BooleanVar(value=False)
        self.report_cache = {}
        self.scale = max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))
        self._configure_styles()
        root.title(self.tr("Pre-Release Translations · Translation Workspace"))
        root.configure(background=COLORS["background"])
        available_w = root.winfo_screenwidth() - self.px(60)
        available_h = root.winfo_screenheight() - self.px(90)
        root.geometry(f"{min(self.px(1120), available_w)}x{min(self.px(940), available_h)}")
        root.minsize(min(self.px(900), available_w), min(self.px(660), available_h))
        root.protocol("WM_DELETE_WINDOW", self.close)

        outer = ttk.Frame(root, style="App.TFrame", padding=self.px(22))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        header = ttk.Frame(outer, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, self.px(18)))
        header.columnconfigure(0, weight=1)
        self.label(header, "PRE-RELEASE TRANSLATIONS", "Brand").grid(row=0, column=0, sticky="w")
        self.label(header, "Translation workspace", "Title").grid(row=1, column=0, sticky="w", pady=(self.px(3), self.px(3)))
        self.label(header, "Prepare card updates. Review translations. Publish when ready.", "Muted").grid(row=2, column=0, sticky="w")
        repository = ttk.Frame(header, style="App.TFrame")
        repository.grid(row=0, column=1, rowspan=3, sticky="e", padx=(self.px(18), 0))
        self.label(repository, "CONNECTED REPOSITORY", "Caption").pack(anchor="e")
        repository_name = self.repo.name
        if repository_name.casefold() in {"transsuperpre", "pre-release-translations"}:
            repository_name = "Pre-Release Translations"
        self.label(repository, repository_name, "Body").pack(anchor="e", pady=(self.px(4), self.px(4)))
        self.button(repository, "Open folder  ↗", lambda: self.open_path(self.repo), style="Link").pack(anchor="e")

        self.button(repository, "Settings", self.open_settings, style="Link").pack(anchor="e")

        navigation = ttk.Frame(outer, style="App.TFrame")
        navigation.grid(row=1, column=0, sticky="w")
        self.nav_buttons = []
        self.notebook = ttk.Notebook(outer, style="Workspace.TNotebook")
        self.notebook.grid(row=2, column=0, sticky="nsew")
        for index, title in enumerate(("Standard", "Rush", "All locales")):
            button = ttk.Button(navigation, text=title, style="Navigation.TButton", command=lambda index=index: self.notebook.select(index))
            button.pack(side="left", padx=(0, self.px(7)))
            self.set_text(button, title)
            self.nav_buttons.append(button)
        self.page_scrolls = []
        self.standard_page = self._scrollable_page("Standard")
        self.rush_page = self._scrollable_page("Rush")
        self.overview_page = self._scrollable_page("All locales")
        self.notebook.bind("<<NotebookTabChanged>>", self._sync_navigation)
        self.notebook.enable_traversal()
        self._sync_navigation()
        self._build_standard(self.standard_page)
        self._build_rush_workspace(self.rush_page)
        self._build_overview(self.overview_page)
        self._build_activity(outer)
        root.bind("<MouseWheel>", self._scroll_page, add="+")
        self.refresh_reports()
        self.language.trace_add("write", lambda *_: self.apply_language())
        self.rush_language.trace_add("write", lambda *_: self.apply_language())
        self.refresh_rush_status()
        if lang.upper() in RUSH_LANGS:
            self.table.selection_set(lang.upper())
        self.append_log(self.tr("No operation running.") + "\n")
        self.apply_language()
        self._show_log()
        root.update_idletasks()
        if min(self.px(940), available_h) < root.winfo_reqheight() - self.px(40):
            if self.log_visible.get():
                self.toggle_log(persist=False)
        self.poll_id = root.after(100, self.poll)

    def tr(self, key, **values):
        return translate(key, self.display_language, **values)

    def set_text(self, widget, key, **values):
        self.localized_widgets[widget] = (key, values)
        widget.configure(text=self.tr(key, **values))

    def apply_language(self):
        tab = self.notebook.index(self.notebook.select())
        automatic = self.rush_language.get() if tab == 1 else self.language.get() if tab == 0 else self.display_language
        self.display_language = self.ui_language if self.ui_language in LANGUAGE_NAMES else automatic
        self.set_text(self.standard_refresh_note, "English names and effects are refreshed."
                      if self.language.get() == "EN" else "Your existing translations are preserved.")
        self.root.title(self.tr("Pre-Release Translations · Translation Workspace"))
        for widget, (key, values) in list(self.localized_widgets.items()):
            if widget.winfo_exists():
                widget.configure(text=self.tr(key, **values))
        for variable in self.localized_vars:
            variable.render()
        for tab, key in zip(self.notebook.tabs(), ("Standard", "Rush", "All locales")):
            self.notebook.tab(tab, text=self.tr(key))
        for column in self.table['columns']:
            self.table.heading(column, text=self.tr(column))
        self.refresh_reports()

    def open_settings(self):
        if self.busy:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(self.tr("Settings"))
        dialog.configure(background=COLORS["background"])
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, style="App.TFrame", padding=self.px(22))
        body.pack(fill="both", expand=True)
        def title(key):
            ttk.Label(body, text=self.tr(key), style="Heading.TLabel").pack(anchor="w", pady=(self.px(8), self.px(8)))
        title("Interface language")
        options = {self.tr("Follow card language"): "AUTO", **{name: key for key, name in LANGUAGE_NAMES.items()}}
        selected = tk.StringVar(value=next((label for label, key in options.items() if key == self.ui_language), next(iter(options))))
        ttk.Combobox(body, values=list(options), textvariable=selected, state="readonly", style="Dark.TCombobox", width=43).pack(fill="x")
        title("Database editor")
        detected = find_editor(self.editor_path)
        editor = tk.StringVar(value=self.editor_path or (str(detected) if detected else ""))
        ttk.Entry(body, textvariable=editor, style="Dark.TEntry", width=60).pack(fill="x")
        actions = ttk.Frame(body, style="App.TFrame")
        actions.pack(fill="x", pady=self.px(10))
        def browse_editor():
            path = filedialog.askopenfilename(parent=dialog, title=self.tr("Select DataEditorX.exe"),
                                              filetypes=[("DataEditorX", "*.exe")], initialdir=str(Path(editor.get()).parent) if editor.get() else None)
            if path:
                editor.set(path)
        def detect_editor():
            path = find_editor()
            if path:
                editor.set(str(path))
            else:
                messagebox.showinfo(self.tr("Database editor"), self.tr("DataEditorX was not found. Browse to its executable."), parent=dialog)
        ttk.Button(actions, text=self.tr("Browse…"), command=browse_editor, style="Secondary.TButton").pack(side="left")
        ttk.Button(actions, text=self.tr("Detect automatically"), command=detect_editor, style="Secondary.TButton").pack(side="left", padx=self.px(8))
        def save():
            path = editor.get().strip()
            if path and not Path(path).is_file():
                messagebox.showerror(self.tr("Database editor"), self.tr("Choose an existing DataEditorX executable."), parent=dialog)
                return
            old_language, old_path = self.ui_language, self.editor_path
            self.ui_language, self.editor_path = options[selected.get()], path
            try:
                self.save_paths()
            except (OSError, ValueError) as error:
                self.ui_language, self.editor_path = old_language, old_path
                messagebox.showerror(self.tr("Settings could not be saved"), str(error), parent=dialog)
                return
            dialog.destroy()
            self.apply_language()
        footer = ttk.Frame(body, style="App.TFrame")
        footer.pack(fill="x", pady=(self.px(14), 0))
        ttk.Button(footer, text=self.tr("Save settings"), command=save, style="Primary.TButton").pack(side="right")
        ttk.Button(footer, text=self.tr("Cancel"), command=dialog.destroy, style="Secondary.TButton").pack(side="right", padx=self.px(8))
        dialog.grab_set()

    def open_database(self, rush):
        if self.busy:
            return
        lang = self.rush_language.get() if rush else self.language.get()
        if rush and lang in ("TH", "ZH-CN"):
            return
        path = database_path(self.local, lang, rush)
        if not path.is_file():
            messagebox.showinfo(self.tr("Not available yet"), self.tr("Fetch updates first to create the database.") + "\n\n" + str(path), parent=self.root)
            return
        editor = find_editor(self.editor_path)
        if editor is None:
            self.open_settings()
            return
        try:
            launch_editor(editor, path)
            self.status.set("Opened database in DataEditorX.")
        except OSError as error:
            messagebox.showerror(self.tr("Could not open"), str(error), parent=self.root)


    def px(self, value):
        return round(value * self.scale)

    def _configure_styles(self):
        c, style = COLORS, ttk.Style(self.root)
        # A native Windows theme ignores several dark colors; clam is bundled with Tk.
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=c["background"], foreground=c["text"])
        style.configure("App.TFrame", background=c["background"])
        style.configure("Panel.TFrame", background=c["panel"], bordercolor=c["border"], relief="solid", borderwidth=1)
        style.configure("Inner.TFrame", background=c["panel"])
        fonts = {"Title": ("Segoe UI", 23, "bold"), "Heading": ("Segoe UI", 15, "bold"),
                 "CardTitle": ("Segoe UI", 12, "bold"), "Body": ("Segoe UI", 10),
                 "Muted": ("Segoe UI", 10), "Caption": ("Segoe UI", 9),
                 "Brand": ("Segoe UI", 9, "bold"), "Status": ("Segoe UI", 9, "bold")}
        for name, font in fonts.items():
            color = c["accent"] if name in ("Brand", "Status") else c["muted"] if name in ("Muted", "Caption") else c["text"]
            for surface, background in (("", c["background"]), ("Panel", c["panel"])):
                style.configure(f"{surface}{name}.TLabel", font=font, background=background, foreground=color)
        for name, background, foreground in (("Secondary", c["field"], c["text"]),
                                              ("Primary", c["accent"], c["accent_text"]),
                                              ("Link", c["background"], c["blue"])):
            style.configure(f"{name}.TButton", font=("Segoe UI", 10, "bold" if name == "Primary" else "normal"),
                            background=background, foreground=foreground, bordercolor=c["border"],
                            lightcolor=background, darkcolor=background, relief="flat",
                            padding=(self.px(14), self.px(10 if name != "Link" else 4)), borderwidth=0 if name == "Link" else 1,
                            focusthickness=1, focuscolor=c["accent_text"] if name == "Primary" else c["accent"])
            active = c["accent_hover"] if name == "Primary" else c["hover"]
            style.map(f"{name}.TButton", background=[("disabled", c["panel"]), ("pressed", active), ("active", active)],
                      foreground=[("disabled", c["disabled"])], bordercolor=[("focus", c["accent"]), ("active", c["accent"])])
        style.configure("Workspace.TNotebook", background=c["background"], bordercolor=c["background"],
                        lightcolor=c["background"], darkcolor=c["background"], borderwidth=0, tabmargins=0)
        style.layout("Workspace.TNotebook.Tab", [])
        for name, background, foreground in (("Navigation", c["panel"], c["muted"]), ("Selected.Navigation", c["hover"], c["accent"])):
            style.configure(f"{name}.TButton", background=background, foreground=foreground, borderwidth=0,
                            bordercolor=background, lightcolor=background, darkcolor=background, relief="flat",
                            font=("Segoe UI", 11, "bold"), padding=(self.px(22), self.px(11)), focuscolor=c["accent"])
            style.map(f"{name}.TButton", background=[("active", c["hover"])], foreground=[("active", c["accent"])])
        style.configure("Dark.TEntry", foreground=c["text"], fieldbackground=c["field"], insertcolor=c["text"],
                        bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], padding=self.px(7))
        style.map("Dark.TEntry", bordercolor=[("focus", c["accent"])], foreground=[("disabled", c["disabled"])])
        style.configure("Dark.TCombobox", foreground=c["text"], fieldbackground=c["field"], background=c["field"],
                        arrowcolor=c["accent"], bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], padding=self.px(6))
        style.map("Dark.TCombobox", fieldbackground=[("readonly", c["field"])], foreground=[("disabled", c["disabled"]), ("readonly", c["text"])],
                  selectbackground=[("readonly", c["field"])], selectforeground=[("readonly", c["text"])])
        self.root.option_add("*TCombobox*Listbox.background", c["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["hover"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", c["accent"])
        style.configure("Dark.TCheckbutton", background=c["panel"], foreground=c["muted"], padding=0,
                        indicatorbackground=c["field"], indicatorforeground=c["accent"], indicatorcolor=c["field"], bordercolor=c["border"])
        style.map("Dark.TCheckbutton", background=[("active", c["panel"])], foreground=[("disabled", c["disabled"]), ("active", c["text"])],
                  indicatorbackground=[("selected", c["accent"])])
        style.configure("Reports.Treeview", background=c["panel"], fieldbackground=c["panel"], foreground=c["text"],
                        borderwidth=0, bordercolor=c["panel"], lightcolor=c["panel"], darkcolor=c["panel"], rowheight=self.px(29), font=("Segoe UI", 10))
        style.map("Reports.Treeview", background=[("selected", c["hover"])], foreground=[("selected", c["accent"])])
        style.configure("Reports.Treeview.Heading", background=c["field"], foreground=c["muted"], font=("Segoe UI", 9, "bold"),
                        borderwidth=0, relief="flat", padding=(self.px(10), self.px(7)))
        style.map("Reports.Treeview.Heading", background=[("active", c["field"])])
        style.configure("Dark.Vertical.TScrollbar", background=c["border"], troughcolor=c["panel"], arrowcolor=c["muted"],
                        lightcolor=c["border"], darkcolor=c["border"], bordercolor=c["panel"], arrowsize=self.px(13), borderwidth=0)
        style.map("Dark.Vertical.TScrollbar", background=[("active", c["disabled"])])
        style.configure("Activity.Horizontal.TProgressbar", background=c["accent"], troughcolor=c["panel"],
                        bordercolor=c["panel"], lightcolor=c["accent"], darkcolor=c["accent"], thickness=self.px(3))

    def label(self, parent, text=None, kind="Body", *, panel=False, **kwargs):
        label = ttk.Label(parent, style=f"{'Panel' if panel else ''}{kind}.TLabel", **kwargs)
        if text is not None:
            self.set_text(label, text)
        if kind in ("Muted", "Caption") and "wraplength" not in kwargs:
            label.bind("<Configure>", lambda event: label.configure(wraplength=max(self.px(100), event.width)))
        return label

    def card(self, parent, padding=18):
        return ttk.Frame(parent, style="Panel.TFrame", padding=self.px(padding))

    def button(self, parent, title, action, *, style="Secondary"):
        button = ttk.Button(parent, text=title, command=action, style=f"{style}.TButton")
        self.set_text(button, title)
        self.controls.append((button, "normal"))
        return button

    def _sync_navigation(self, _event=None):
        selected = self.notebook.index(self.notebook.select())
        for index, button in enumerate(self.nav_buttons):
            button.configure(style="Selected.Navigation.TButton" if index == selected else "Navigation.TButton")

        if hasattr(self, "table"):
            self.apply_language()

    def _scrollable_page(self, title):
        """Keep every action reachable on short displays and at Windows text scaling."""
        page = ttk.Frame(self.notebook, style="App.TFrame")
        self.notebook.add(page, text=title)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        canvas = tk.Canvas(page, background=COLORS["background"], highlightthickness=0, borderwidth=0,
                           yscrollincrement=self.px(24))
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview, style="Dark.Vertical.TScrollbar")
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)
        content = ttk.Frame(canvas, style="App.TFrame", padding=(0, self.px(18), 0, self.px(10)))
        item = canvas.create_window(0, 0, window=content, anchor="nw")

        def resize(_event=None):
            height = max(canvas.winfo_height(), content.winfo_reqheight())
            canvas.itemconfigure(item, width=canvas.winfo_width(), height=height)
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), height))
            if height > canvas.winfo_height() + 1:
                scrollbar.grid()
            else:
                scrollbar.grid_remove()
                canvas.yview_moveto(0)

        canvas.bind("<Configure>", resize)
        content.bind("<Configure>", resize)
        self.page_scrolls.append((canvas, content, scrollbar))
        return content

    def _scroll_page(self, event):
        # Give the locale table, log, and selectors their own normal wheel behavior.
        if event.widget.winfo_class() in ("Treeview", "Text", "TCombobox", "TScrollbar") or not event.delta:
            return
        for canvas, content, scrollbar in self.page_scrolls:
            widget = event.widget
            while widget is not None:
                if widget in (canvas, content):
                    if scrollbar.winfo_ismapped():
                        steps = int(event.delta / 120) or (1 if event.delta > 0 else -1)
                        canvas.yview_scroll(-steps, "units")
                        return "break"
                    return
                widget = getattr(widget, "master", None)

    def _build_standard(self, parent):
        parent.columnconfigure(0, weight=1)
        self.label(parent, "Standard prerelease", "Heading").grid(row=0, column=0, sticky="w")
        self.label(parent, "01  Fetch updates     /     02  Review translations     /     03  Publish", "Muted").grid(row=1, column=0, sticky="w", pady=(self.px(5), self.px(16)))

        update = self.card(parent)
        update.grid(row=2, column=0, sticky="ew")
        update.columnconfigure(0, weight=1)
        self.label(update, "Start with the latest cards", "CardTitle", panel=True).grid(row=0, column=0, sticky="w")
        self.label(update, "Refresh your local workspace from MyCard.", "Muted", panel=True).grid(row=1, column=0, sticky="w", pady=(self.px(5), self.px(14)))
        language = ttk.Frame(update, style="Inner.TFrame")
        language.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(self.px(14), 0))
        self.label(language, "TRANSLATION LANGUAGE", "Caption", panel=True).pack(anchor="w", pady=(0, self.px(5)))
        selector = ttk.Combobox(language, textvariable=self.language, values=STANDARD_LANGS, state="readonly", width=18, style="Dark.TCombobox")
        selector.pack(fill="x")
        self.controls.append((selector, "readonly"))
        self.standard_fetch_button = self.button(update, "Fetch updates from MyCard", lambda: self.start_standard("refresh"), style="Primary")
        self.standard_fetch_button.grid(row=2, column=0, sticky="w")
        self.standard_refresh_note = self.label(update, kind="Caption", panel=True)
        self.standard_refresh_note.grid(row=2, column=1, sticky="e", padx=(self.px(18), 0))

        cards = ttk.Frame(parent, style="App.TFrame")
        cards.grid(row=3, column=0, sticky="ew", pady=self.px(14))
        cards.columnconfigure((0, 1), weight=1, uniform="standard")
        workspace = self.card(cards)
        workspace.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(7)))
        self.label(workspace, "Local workspace", "CardTitle", panel=True).pack(anchor="w")
        self.label(workspace, "Edit and prepare the selected language.", "Muted", panel=True).pack(anchor="w", pady=(self.px(5), self.px(15)))
        self.standard_editor_button = self.button(workspace, "Open Database in DataEditorX", lambda: self.open_database(False))
        self.standard_editor_button.pack(fill="x", pady=(0, self.px(8)))
        self.button(workspace, "Open translation folder  ↗", lambda: self.open_path(self.local / self.language.get() / "raw2")).pack(fill="x")
        publish = self.card(cards)
        publish.grid(row=0, column=1, sticky="nsew", padx=(self.px(7), 0))
        self.label(publish, "GitHub & publishing", "CardTitle", panel=True).pack(anchor="w")
        self.label(publish, "Upload when your translations are ready.", "Muted", panel=True).pack(anchor="w", pady=(self.px(5), self.px(15)))
        self.button(publish, "Upload workspace & push to GitHub", lambda: self.start_standard("push")).pack(fill="x", pady=(0, self.px(8)))
        secondary = ttk.Frame(publish, style="Inner.TFrame")
        secondary.pack(fill="x")
        secondary.columnconfigure((0, 1), weight=1, uniform="publish")
        self.button(secondary, "Pull published", lambda: self.start_standard("pull")).grid(row=0, column=0, sticky="ew", padx=(0, self.px(4)))
        self.button(secondary, "Actions & status", lambda: self.start_standard("status")).grid(row=0, column=1, sticky="ew", padx=(self.px(4), 0))

    def _build_rush_workspace(self, parent):
        parent.columnconfigure(0, weight=1)
        self.label(parent, "Rush prerelease", "Heading").grid(row=0, column=0, sticky="w")
        self.label(parent, "01  Fetch your locale     /     02  Review translations     /     03  Publish", "Muted").grid(row=1, column=0, sticky="w", pady=(self.px(5), self.px(16)))
        update = self.card(parent)
        update.grid(row=2, column=0, sticky="ew")
        update.columnconfigure(0, weight=1)
        self.label(update, "Update your Rush workspace", "CardTitle", panel=True).grid(row=0, column=0, sticky="w")
        self.label(update, "Fetch the published cards for your language.", "Muted", panel=True).grid(row=1, column=0, sticky="w", pady=(self.px(5), self.px(14)))
        language = ttk.Frame(update, style="Inner.TFrame")
        language.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(self.px(14), 0))
        self.label(language, "TRANSLATION LANGUAGE", "Caption", panel=True).pack(anchor="w", pady=(0, self.px(5)))
        selector = ttk.Combobox(language, textvariable=self.rush_language, values=RUSH_LANGS, state="readonly", width=18, style="Dark.TCombobox")
        selector.pack(fill="x")
        self.controls.append((selector, "readonly"))
        self.rush_fetch_button = self.button(update, "Fetch Rush updates", lambda: self.start_rush_workspace("refresh"), style="Primary")
        self.rush_fetch_button.grid(row=2, column=0, sticky="w")
        self.label(update, "No extra source repositories needed.", "Caption", panel=True).grid(row=2, column=1, sticky="e", padx=(self.px(15), 0))
        self.label(update, kind="Status", panel=True, textvariable=self.rush_workspace_status).grid(row=3, column=0, columnspan=2, sticky="w", pady=(self.px(16), self.px(4)))
        self.label(update, kind="Caption", panel=True, textvariable=self.rush_workspace_note, wraplength=self.px(740)).grid(row=4, column=0, columnspan=2, sticky="w")
        self.thai_switch_button = self.button(update, "Switch to English", lambda: self.rush_language.set("EN"))
        self.thai_switch_button.grid(row=5, column=0, sticky="w", pady=(self.px(10), 0))
        self.thai_switch_button.grid_remove()

        cards = ttk.Frame(parent, style="App.TFrame")
        cards.grid(row=3, column=0, sticky="ew", pady=self.px(14))
        cards.columnconfigure((0, 1), weight=1, uniform="rush_workspace")
        workspace = self.card(cards)
        workspace.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(7)))
        self.label(workspace, "Local workspace", "CardTitle", panel=True).pack(anchor="w")
        self.label(workspace, "Edit and prepare this locale's Rush cards.", "Muted", panel=True).pack(anchor="w", pady=(self.px(5), self.px(15)))
        self.rush_editor_button = self.button(workspace, "Open Database in DataEditorX", lambda: self.open_database(True))
        self.rush_editor_button.pack(fill="x", pady=(0, self.px(8)))
        edit = self.button(workspace, "Open translation folder  ↗", lambda: self.open_path(self.local / self.rush_language.get() / "Rush/raw2"))
        edit.pack(fill="x")
        publish = self.card(cards)
        publish.grid(row=0, column=1, sticky="nsew", padx=(self.px(7), 0))
        self.label(publish, "GitHub & publishing", "CardTitle", panel=True).pack(anchor="w")
        self.label(publish, "Upload only the selected Rush locale.", "Muted", panel=True).pack(anchor="w", pady=(self.px(5), self.px(15)))
        push = self.button(publish, "Upload workspace & push to GitHub", lambda: self.start_rush_workspace("push"))
        push.pack(fill="x", pady=(0, self.px(8)))
        self.rush_publish_controls = [self.rush_editor_button, push]
        secondary = ttk.Frame(publish, style="Inner.TFrame")
        secondary.pack(fill="x")
        secondary.columnconfigure((0, 1), weight=1, uniform="rush_publish")
        self.button(secondary, "Pull published", lambda: self.start_rush_workspace("pull")).grid(row=0, column=0, sticky="ew", padx=(0, self.px(4)))
        self.button(secondary, "Actions & status", lambda: self.start_rush_workspace("status")).grid(row=0, column=1, sticky="ew", padx=(self.px(4), 0))
        links = ttk.Frame(parent, style="App.TFrame")
        links.grid(row=4, column=0, sticky="ew")
        self.button(links, "Open local reports  ↗", lambda: self.open_path(self.local / self.rush_language.get() / "Rush/Reports"), style="Link").pack(side="left", padx=(0, self.px(10)))
        self.button(links, "View locale details", self.show_rush_overview, style="Link").pack(side="left")
        self.resolve_button = self.button(parent, "Keep my reviewed translations", lambda: self.start_rush_workspace("resolve"))
        self.resolve_button.grid(row=5, column=0, sticky="w", pady=(self.px(10), 0))
        self.resolve_button.grid_remove()

    def _build_overview(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        heading = ttk.Frame(parent, style="App.TFrame")
        heading.grid(row=0, column=0, sticky="ew")
        self.label(heading, "All Rush locales", "Heading").pack(side="left")
        self.label(heading, kind="Caption", textvariable=self.report_summary).pack(side="right")
        self.label(parent, "Local package and workspace status. Fetch updates to check GitHub.", "Muted").grid(row=1, column=0, sticky="w", pady=(self.px(5), self.px(14)))
        table_frame = self.card(parent, 0)
        table_frame.grid(row=2, column=0, sticky="nsew")
        columns = ("Locale", "Status", "Cards", "Translations", "Artwork", "Package built")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=5, selectmode="browse", style="Reports.Treeview")
        for column, width in zip(columns, (65, 170, 60, 105, 85, 225)):
            self.table.heading(column, text=column)
            self.table.column(column, width=self.px(width), minwidth=self.px(50), anchor="w" if column in ("Status", "Package built") else "center")
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview, style="Dark.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.tag_configure("alternate", background="#1c2938")
        self.table.bind("<<TreeviewSelect>>", self.show_locale_details)
        self.details_heading = self.label(parent, "Selected locale details", "CardTitle")
        self.details_heading.grid(row=3, column=0, sticky="w", pady=(self.px(15), self.px(8)))
        details = self.card(parent, 0)
        details.grid(row=4, column=0, sticky="ew")
        self.details_text = tk.Text(details, height=4, wrap="word", state="disabled", font=("Segoe UI", 10),
                                    background=COLORS["panel"], foreground=COLORS["muted"], borderwidth=0,
                                    highlightthickness=0, padx=self.px(12), pady=self.px(9),
                                    selectbackground=COLORS["hover"], selectforeground=COLORS["text"])
        self.details_text.pack(side="left", fill="both", expand=True)
        details_scroll = ttk.Scrollbar(details, orient="vertical", command=self.details_text.yview, style="Dark.Vertical.TScrollbar")
        details_scroll.pack(side="right", fill="y")
        self.details_text.configure(yscrollcommand=details_scroll.set)
        links = ttk.Frame(parent, style="App.TFrame")
        links.grid(row=5, column=0, sticky="ew", pady=(self.px(8), self.px(10)))
        self.button(links, "View build report  ↗", lambda: self.open_rush("report.json"), style="Link").pack(side="left", padx=(0, self.px(8)))
        self.button(links, "Shared artwork  ↗", lambda: self.open_path(self.repo / "Shared/Rush Artworks"), style="Link").pack(side="left")
        self.button(links, "Refresh overview", self.refresh_reports, style="Link").pack(side="right")
        self.build_toggle = ttk.Button(parent, text="Show all-locale build tools", style="Secondary.TButton", command=self.toggle_build_tools)
        self.set_text(self.build_toggle, "Show all-locale build tools")
        self.build_toggle.grid(row=6, column=0, sticky="w", pady=(self.px(5), 0))
        self.build_tools = self.card(parent, 14)
        self.build_tools.grid(row=7, column=0, sticky="ew", pady=(self.px(10), 0))
        self._build_all_locale_controls(self.build_tools)
        self.build_tools.grid_remove()

    def _build_all_locale_controls(self, sources):
        sources.columnconfigure(1, weight=1)
        self.label(sources, "All-locale package builder", "CardTitle", panel=True).grid(row=0, column=0, columnspan=3, sticky="w")
        self.label(sources, "For maintainers with the Rush source and full locale databases installed.", "Muted", panel=True).grid(row=1, column=0, columnspan=3, sticky="w", pady=(self.px(5), self.px(12)))
        for row, (title, variable) in enumerate((("Rush source", self.source), ("Locale databases", self.translations)), start=2):
            self.label(sources, title, "Body", panel=True).grid(row=row, column=0, sticky="w", padx=(0, self.px(14)), pady=self.px(3))
            entry = ttk.Entry(sources, textvariable=variable, style="Dark.TEntry")
            entry.grid(row=row, column=1, sticky="ew", pady=self.px(3))
            self.controls.append((entry, "normal"))
            self.button(sources, "Browse…", lambda variable=variable: self.browse(variable)).grid(row=row, column=2, padx=(self.px(8), 0), pady=self.px(3))
        actions = ttk.Frame(sources, style="Inner.TFrame")
        actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(self.px(12), self.px(9)))
        self.fetch_button = self.button(actions, "Fetch updates & build all locales", lambda: self.start_rush(True), style="Primary")
        self.fetch_button.pack(side="left", padx=(0, self.px(10)))
        self.build_button = self.button(actions, "Build all locales", lambda: self.start_rush(False))
        self.build_button.pack(side="left")
        offline = ttk.Checkbutton(sources, text="Use local translations only · skip web text sources", variable=self.offline, style="Dark.TCheckbutton")
        self.set_text(offline, "Use local translations only · skip web text sources")
        offline.grid(row=5, column=0, columnspan=3, sticky="w", pady=(self.px(2), 0))
        self.controls.append((offline, "normal"))

    def toggle_build_tools(self):
        visible = not self.build_tools_visible.get()
        self.build_tools_visible.set(visible)
        if visible:
            self.build_tools.grid()
        else:
            self.build_tools.grid_remove()
        self.set_text(self.build_toggle, "Hide all-locale build tools" if visible else "Show all-locale build tools")

    def show_rush_overview(self):
        lang = self.rush_language.get()
        self.notebook.select(2)
        self.table.selection_set(lang)
        self.table.see(lang)
        self.show_locale_details()

    def show_locale_details(self, _event=None):
        selected = self.table.selection()
        lang = selected[0] if selected else self.rush_language.get()
        snapshot = self.report_cache.get(lang)
        if snapshot is None:
            return
        self.set_text(self.details_heading, "{lang} · Card details", lang=lang)
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", "end")
        self.details_text.insert("end", snapshot_details(snapshot, self.tr))
        self.details_text.configure(state="disabled")

    def _build_activity(self, parent):
        activity = self.card(parent, 0)
        activity.grid(row=3, column=0, sticky="ew", pady=(self.px(12), 0))
        activity.columnconfigure(0, weight=1)
        bar = ttk.Frame(activity, style="Inner.TFrame", padding=(self.px(14), self.px(9)))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        self.status_badge = self.label(bar, kind="Status", panel=True, textvariable=self.status_kind)
        self.status_badge.grid(row=0, column=0, sticky="w", padx=(0, self.px(14)))
        self.label(bar, kind="Caption", panel=True, textvariable=self.status, wraplength=self.px(580)).grid(row=0, column=1, sticky="w")
        self.log_toggle = ttk.Button(bar, text="Hide log", command=self.toggle_log, style="Secondary.TButton", padding=(self.px(9), self.px(3)))
        self.set_text(self.log_toggle, "Hide log")
        self.log_toggle.grid(row=0, column=2, sticky="e", padx=(self.px(10), 0))
        self.activity_detail_label = self.label(bar, kind="Caption", panel=True, textvariable=self.activity_detail, wraplength=self.px(690))
        self.activity_detail_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(self.px(5), 0))
        self.activity_detail_label.grid_remove()
        progress_host = ttk.Frame(activity, style="Inner.TFrame", height=self.px(7))
        progress_host.grid(row=1, column=0, sticky="ew")
        progress_host.pack_propagate(False)
        self.progress = ActivityBar(progress_host)
        self.log_frame = ttk.Frame(activity, style="Inner.TFrame", padding=(self.px(2), self.px(2), self.px(2), self.px(2)))
        self.log_frame.grid(row=2, column=0, sticky="ew")
        self.log = tk.Text(self.log_frame, height=6, wrap="word", font=("Cascadia Mono", 9), state="disabled",
                           background=COLORS["log"], foreground=COLORS["muted"], borderwidth=0,
                           highlightthickness=0, padx=self.px(13), pady=self.px(10),
                           selectbackground=COLORS["hover"], selectforeground=COLORS["text"], insertbackground=COLORS["text"])
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log.yview, style="Dark.Vertical.TScrollbar")
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)
        self.log.tag_configure("error", foreground=COLORS["error"])
        self.log.tag_configure("warning", foreground=COLORS["warning"])
        self.log.tag_configure("complete", foreground=COLORS["accent"])

    def toggle_log(self, persist=True):
        self.log_visible.set(not self.log_visible.get())
        self._show_log()
        if persist:
            try:
                self.save_paths()
            except (OSError, ValueError) as error:
                self.append_log(f"Settings: {error}\n")

    def _show_log(self):
        visible = self.log_visible.get()
        if visible:
            self.log_frame.grid()
        else:
            self.log_frame.grid_remove()
        self.set_text(self.log_toggle, "Hide log" if visible else "Show log")

    def set_status_kind(self, title, color):
        self.status_kind.set(title)
        self.status_badge.configure(foreground=COLORS[color])

    def browse(self, variable):
        path = filedialog.askdirectory(parent=self.root, initialdir=variable.get(), title=self.tr("Select folder"))
        if path:
            variable.set(path)

    def open_path(self, path: Path):
        if not path.exists():
            messagebox.showinfo(self.tr("Not available yet"), self.tr("Build or refresh first to create:") + "\n" + str(path), parent=self.root)
            return
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if os.sys.platform == "darwin" else "xdg-open", str(path)])
        except OSError as error:
            messagebox.showerror(self.tr("Could not open"), str(error), parent=self.root)

    def open_rush(self, name: str):
        selected = self.table.selection()
        self.open_path(self.repo / (selected[0] if selected else "EN") / "Rush" / name)

    def save_paths(self):
        if self.config_error:
            raise ValueError(f"Could not read {self.config_path}: {self.config_error}. Repair that settings file before saving paths.")
        self.config.update(repo_root=str(self.repo), rush_source_root=self.source.get().strip(),
                           rush_translations_root=self.translations.get().strip(), interface_language=self.ui_language,
                           dataeditorx_path=self.editor_path, show_log=self.log_visible.get())
        temporary = self.config_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.config, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.config_path)

    def start_standard(self, command: str):
        self.start(workspace_command(self.repo, self.local, command, self.language.get()), "Standard")

    def start_rush_workspace(self, command: str):
        if self.busy:
            return
        lang = self.rush_language.get()
        if lang in ("TH", "ZH-CN") and command in ("upload", "push", "resolve"):
            messagebox.showinfo(self.tr("Managed locale"), self.tr("Thai uses English translations; Simplified Chinese follows upstream. Update the owning source instead."), parent=self.root)
            return
        if command == "refresh":
            try:
                self.save_paths()
            except (OSError, ValueError) as error:
                messagebox.showerror(self.tr("Check Rush paths"), str(error), parent=self.root)
                return
        if command == "resolve":
            try:
                conflicts = read_json_object(self.local / lang / "Rush/Reports/sync.json").get("conflicts", [])
            except (OSError, ValueError) as error:
                messagebox.showerror(self.tr("Could not read conflicts"), str(error), parent=self.root)
                return
            if not conflicts:
                self.refresh_rush_status()
                return
            fields = "\n".join(f"{item.get('id', '?')} · {item.get('field', '?')}" for item in conflicts[:12])
            message = self.tr("Keep your local text for {count} conflicting fields?", count=len(conflicts)) + "\n\n" + fields + "\n\n" + self.tr("Review Rush/Reports/sync.json first. Non-conflicting source updates will still be applied.")
            if not messagebox.askyesno(self.tr("Keep reviewed translations"), message, parent=self.root, default="no"):
                return
        self.start(rush_workspace_command(self.repo, self.local, command, lang), "Rush locale")

    def refresh_rush_status(self):
        lang = self.rush_language.get()
        managed = lang in ("TH", "ZH-CN")
        for control in self.rush_publish_controls:
            control.configure(state="disabled" if self.busy or managed else "normal")
        self.resolve_button.grid_remove()
        self.thai_switch_button.grid_remove()
        note = "Local repository snapshot. Fetch updates to check the latest published workspace."
        title = "Not fetched yet"
        try:
            local_root = self.local / lang / "Rush"
            local_file = local_root / "raw2/rush-pre-release.cdb"
            sync = read_json_object(local_root / "Reports/sync.json")
            conflicts = sync.get("conflicts", [])
            if conflicts:
                title = self.tr("Review {count} merge conflicts", count=len(conflicts))
                note = "Open local reports and review sync.json. Your database has been preserved."
                if not managed:
                    self.resolve_button.grid()
            elif local_file.is_file():
                current = workspace_rows(local_file)
                published = workspace_rows(self.repo / lang / "Rush/Workspace/rush-pre-release.cdb")
                if current == published:
                    title = "Matches repository workspace"
                else:
                    title = "Local translations differ · review before publishing"
            else:
                note = "Fetch Rush updates to create this locale's translation folder."
            snapshot = self.report_cache.get(lang, {})
            pending = snapshot.get("pending", [])
            if pending:
                note = self.tr(note) + " " + self.tr("Latest build report: {count} cards need translation review.", count=len(pending))
        except (OSError, ValueError, TypeError, sqlite3.Error) as error:
            title, note = "Could not read local status", str(error)
        if lang == "TH":
            title = "English-managed locale"
            note = "Thai packages use English text. Fetch and view Thai here; make translation changes in English."
            self.thai_switch_button.grid()
        elif lang == "ZH-CN":
            title = "Upstream-managed locale"
            note = "Simplified Chinese follows the upstream source. Fetch and view its reports here; text is managed upstream."
        self.rush_workspace_status.set(title)
        self.rush_workspace_note.set(note)

    def start_rush(self, fetch: bool):
        if self.busy:
            return
        try:
            if not self.source.get().strip() or not self.translations.get().strip():
                raise ValueError(self.tr("Choose the Rush source and locale database folders."))
            source = Path(self.source.get().strip()).expanduser().resolve()
            translations = Path(self.translations.get().strip()).expanduser().resolve()
            if not (source / "RD Patch.cdb").is_file():
                raise ValueError(self.tr("Rush source must contain RD Patch.cdb."))
            if not (translations / "en-US/rush_cards.cdb").is_file():
                raise ValueError(self.tr("Locale databases must contain en-US/rush_cards.cdb."))
            self.save_paths()
        except (OSError, ValueError) as error:
            messagebox.showerror(self.tr("Check Rush paths"), str(error), parent=self.root)
            return
        self.start(rush_command(self.repo, source, translations, fetch=fetch, offline=self.offline.get()), "Rush")

    def start(self, command: list[str], kind: str):
        if self.busy:
            return
        self.busy, self.job_kind = True, kind
        self.activity_detail.set("")
        self.activity_detail_label.grid_remove()
        self.status.set_template("{kind} update running…", kind=self.tr(kind))
        self.set_status_kind("WORKING", "blue")
        self.progress.pack(fill="both", expand=True)
        self.progress.start(12)
        for control, _ in self.controls:
            control.configure(state="disabled")
        self.append_log("\n" + subprocess.list2cmdline(command) + "\n\n")
        threading.Thread(target=stream_job, args=(command, self.repo, self.events), daemon=True).start()

    def append_log(self, text: str):
        self.log.configure(state="normal")
        lower = text.lower()
        tag = "error" if any(word in lower for word in ("error:", "failed", "traceback", "could not")) else "warning" if "warning" in lower else "complete" if "exit code 0" in lower else ""
        self.log.insert("end", text, tag)
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 2000:
            self.log.delete("1.0", f"{lines - 2000}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_reports(self):
        self.report_cache = {lang: locale_snapshot(self.repo, lang) for lang in RUSH_LANGS}
        pending = sum(len(item["pending"]) for item in self.report_cache.values())
        self.report_summary.set_template("{count} locales · {pending} translation issues", count=len(self.report_cache), pending=pending)
        for index, (lang, item) in enumerate(self.report_cache.items()):
            row = (lang, self.tr(item["status"]), item["cards"], len(item["pending"]), len(item["missing"]), self.tr(item["built"]))
            if self.table.exists(lang):
                self.table.item(lang, values=row)
            else:
                self.table.insert("", "end", iid=lang, values=row, tags=("alternate",) if index % 2 else ())
        self.show_locale_details()
        self.refresh_rush_status()

    def poll(self):
        for _ in range(150):
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "log":
                self.append_log(value)
                result = activity_result(value)
                if result:
                    self.activity_detail.set_template(result[0], **result[1])
                    self.activity_detail_label.grid()
                phase = activity_phase(value)
                if phase:
                    self.status.set(phase)
            else:
                self.busy = False
                self.progress.finish(value == 0)
                for control, state in self.controls:
                    control.configure(state=state)
                self.refresh_reports()
                if value == 0:
                    self.set_status_kind("COMPLETE", "accent")
                    if self.job_kind == "Rush":
                        self.status.set("Rush packages built locally — review the locale reports.")
                    else:
                        self.status.set_template("{kind} operation completed.", kind=self.tr(self.job_kind))
                else:
                    self.set_status_kind("NEEDS ATTENTION", "warning")
                    self.status.set("Update stopped — review the log and translation reports.")
                    if not self.log_visible.get():
                        self.toggle_log()
                self.append_log(f"\nProcess finished with exit code {value}.\n")
        self.poll_id = self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo(self.tr("Update in progress"), self.tr("Please wait for the current operation to finish before closing."), parent=self.root)
            return
        self.progress.stop()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()


def launch(repo: Path, local: Path, lang: str) -> None:
    root = tk.Tk()
    PrereleaseWindow(root, repo, local, lang)
    root.mainloop()
