"""Shared Standard/Rush window launched by each locale's AutoPR.py wrapper."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from rush_local import python_console

STANDARD_LANGS = ("DE", "EN", "ES", "FR", "IT", "JP", "KR", "PT", "TH")
RUSH_LANGS = (*STANDARD_LANGS, "ZH-CN", "ZH-TW")


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


def read_reports(repo: Path) -> list[tuple]:
    rows = []
    for lang in RUSH_LANGS:
        try:
            path = repo / lang / "Rush/report.json"
            report = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            pending = report.get("pending_translations", [])
            version = repo / lang / "rush-version.txt"
            rows.append((lang, report.get("active_cards", "—"),
                         len(pending) if isinstance(pending, list) else pending,
                         len(report.get("missing_artwork", [])),
                         version.read_text().strip() if version.is_file() else "Not built"))
        except (OSError, ValueError, TypeError):
            rows.append((lang, "Read error", "—", "—", "Check report.json"))
    return rows


def stream_job(command: list[str], cwd: Path, events: queue.Queue) -> None:
    """Worker never touches Tk; all UI changes happen in the main-thread poll."""
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1", GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
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
        self.language = tk.StringVar(value=lang.upper())
        self.source = tk.StringVar(value=self.config.get("rush_source_root", str(repo.parent / "ygopro-rush-duel")))
        self.translations = tk.StringVar(value=self.config.get("rush_translations_root", str(repo.parent / "MDPro3_Data/Data/locales")))
        self.offline = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready")
        root.title("TransSuperpre · Pre-Release Cards")
        root.geometry("980x780")
        root.minsize(820, 700)
        root.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Pre-Release Cards", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(outer, text=str(self.repo)).pack(anchor="w", pady=(2, 12))
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="x")
        standard = ttk.Frame(notebook, padding=14)
        rush = ttk.Frame(notebook, padding=14)
        notebook.add(standard, text=" Standard ")
        notebook.add(rush, text=" Rush · All Locales ")
        self.notebook = notebook
        ttk.Label(standard, text="Refresh your local translations, then copy or upload them for the standard GitHub build.").pack(anchor="w")
        language_row = ttk.Frame(standard)
        language_row.pack(fill="x", pady=14)
        ttk.Label(language_row, text="Language").pack(side="left", padx=(0, 10))
        selector = ttk.Combobox(language_row, textvariable=self.language, values=STANDARD_LANGS, state="readonly", width=10)
        selector.pack(side="left")
        self.controls.append((selector, "readonly"))
        for label, command in (("Fetch Standard Updates (MyCard)", "refresh"),
                               ("Copy Workspace into Repository", "upload"),
                               ("Upload Workspace & Push to GitHub", "push"),
                               ("Pull Published Workspace", "pull"), ("Repository & Actions Status", "status")):
            self.button(standard, label, lambda command=command: self.start_standard(command)).pack(fill="x", pady=4)
        self.button(standard, "Open Local Translation Folder", lambda: self.open_path(self.local / self.language.get() / "raw2")).pack(fill="x", pady=4)
        ttk.Label(rush, text="Fetch the latest Rush patch and build EN, DE, ES, FR, IT, JP, KR, PT, TH, ZH-CN and ZH-TW.").pack(anchor="w")
        paths = ttk.Frame(rush)
        paths.pack(fill="x", pady=10)
        paths.columnconfigure(1, weight=1)
        for row, (title, variable) in enumerate((("Rush source", self.source), ("Locale databases", self.translations))):
            ttk.Label(paths, text=title).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            entry = ttk.Entry(paths, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            self.controls.append((entry, "normal"))
            self.button(paths, "Browse…", lambda variable=variable: self.browse(variable)).grid(row=row, column=2, padx=(8, 0), pady=3)
        actions = ttk.Frame(rush)
        actions.pack(fill="x")
        self.fetch_button = self.button(actions, "Fetch Updates & Build All Locales", lambda: self.start_rush(True))
        self.fetch_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.build_button = self.button(actions, "Build All Locales", lambda: self.start_rush(False))
        self.build_button.pack(side="left", fill="x", expand=True)
        offline = ttk.Checkbutton(rush, text="Use local translations only (skip web text sources)", variable=self.offline)
        offline.pack(anchor="w", pady=(8, 2))
        self.controls.append((offline, "normal"))
        ttk.Label(rush, text="Builds save locally. Thai uses English. Missing translations appear in the reports below.").pack(anchor="w", pady=(0, 6))
        table_frame = ttk.Frame(rush)
        table_frame.pack(fill="x")
        columns = ("Locale", "Cards", "Pending translations", "Missing artwork", "Package version")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=6, selectmode="browse")
        for column, width in zip(columns, (70, 70, 170, 130, 175)):
            self.table.heading(column, text=column)
            self.table.column(column, width=width, minwidth=60, anchor="center")
        self.table.pack(side="left", fill="x", expand=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scrollbar.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.refresh_reports()
        if lang.upper() in RUSH_LANGS:
            self.table.selection_set(lang.upper())
        links = ttk.Frame(rush)
        links.pack(fill="x", pady=(8, 0))
        self.button(links, "Open Selected Workspace", lambda: self.open_rush("Workspace")).pack(side="left", padx=(0, 6))
        self.button(links, "Open Selected Report", lambda: self.open_rush("report.json")).pack(side="left", padx=(0, 6))
        self.button(links, "Shared Artworks", lambda: self.open_path(self.repo / "Shared/Rush Artworks")).pack(side="left")
        progress_row = ttk.Frame(outer)
        progress_row.pack(fill="x", pady=(12, 6))
        ttk.Label(progress_row, textvariable=self.status).pack(side="left")
        self.progress = ttk.Progressbar(progress_row, mode="indeterminate", length=150)
        self.progress.pack(side="right")
        self.log = ScrolledText(outer, height=10, wrap="word", font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True)
        self.append_log("Select a tab to update Standard or Rush cards. Rush operations build every locale.\n")
        self.poll_id = root.after(100, self.poll)

    def button(self, parent, title, action):
        button = ttk.Button(parent, text=title, command=action)
        self.controls.append((button, "normal"))
        return button

    def browse(self, variable):
        path = filedialog.askdirectory(parent=self.root, initialdir=variable.get(), title="Select folder")
        if path:
            variable.set(path)

    def open_path(self, path: Path):
        if not path.exists():
            messagebox.showinfo("Not available yet", f"Build or refresh first to create:\n{path}", parent=self.root)
            return
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if os.sys.platform == "darwin" else "xdg-open", str(path)])
        except OSError as error:
            messagebox.showerror("Could not open", str(error), parent=self.root)

    def open_rush(self, name: str):
        selected = self.table.selection()
        self.open_path(self.repo / (selected[0] if selected else "EN") / "Rush" / name)

    def save_paths(self):
        if self.config_error:
            raise ValueError(f"Could not read {self.config_path}: {self.config_error}. Repair that settings file before saving paths.")
        self.config.update(repo_root=str(self.repo), rush_source_root=self.source.get().strip(),
                           rush_translations_root=self.translations.get().strip())
        temporary = self.config_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.config, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.config_path)

    def start_standard(self, command: str):
        self.start(workspace_command(self.repo, self.local, command, self.language.get()), "Standard")

    def start_rush(self, fetch: bool):
        if self.busy:
            return
        try:
            if not self.source.get().strip() or not self.translations.get().strip():
                raise ValueError("Choose the Rush source and locale database folders.")
            source = Path(self.source.get().strip()).expanduser().resolve()
            translations = Path(self.translations.get().strip()).expanduser().resolve()
            if not (source / "RD Patch.cdb").is_file():
                raise ValueError("Rush source must contain RD Patch.cdb.")
            if not (translations / "en-US/rush_cards.cdb").is_file():
                raise ValueError("Locale databases must contain en-US/rush_cards.cdb.")
            self.save_paths()
        except (OSError, ValueError) as error:
            messagebox.showerror("Check Rush paths", str(error), parent=self.root)
            return
        self.start(rush_command(self.repo, source, translations, fetch=fetch, offline=self.offline.get()), "Rush")

    def start(self, command: list[str], kind: str):
        if self.busy:
            return
        self.busy, self.job_kind = True, kind
        self.status.set(f"{kind} update running…")
        self.progress.start(12)
        for control, _ in self.controls:
            control.configure(state="disabled")
        self.append_log("\n" + subprocess.list2cmdline(command) + "\n\n")
        threading.Thread(target=stream_job, args=(command, self.repo, self.events), daemon=True).start()

    def append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 2000:
            self.log.delete("1.0", f"{lines - 2000}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_reports(self):
        for row in read_reports(self.repo):
            if self.table.exists(row[0]):
                self.table.item(row[0], values=row)
            else:
                self.table.insert("", "end", iid=row[0], values=row)

    def poll(self):
        for _ in range(150):
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "log":
                self.append_log(value)
            else:
                self.busy = False
                self.progress.stop()
                for control, state in self.controls:
                    control.configure(state=state)
                self.refresh_reports()
                if value == 0:
                    self.status.set("Rush packages built locally — review the locale reports." if self.job_kind == "Rush" else "Standard operation completed.")
                else:
                    self.status.set("Update stopped — review the log and translation reports.")
                self.append_log(f"\nProcess finished with exit code {value}.\n")
        self.poll_id = self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo("Update in progress", "Please wait for the current operation to finish before closing.", parent=self.root)
            return
        self.root.after_cancel(self.poll_id)
        self.root.destroy()


def launch(repo: Path, local: Path, lang: str) -> None:
    root = tk.Tk()
    PrereleaseWindow(root, repo, local, lang)
    root.mainloop()
