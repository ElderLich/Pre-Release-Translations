"""Bounded layout checks for the shared Tk workspace; no jobs or network calls."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import prerelease_gui as gui
except ImportError:
    gui = None


@unittest.skipIf(gui is None, "Tkinter unavailable")
class WorkspaceLayoutTests(unittest.TestCase):
    def make_window(self, scale):
        try:
            root = gui.tk.Tk()
        except gui.tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        root.tk.call("tk", "scaling", scale * 96 / 72)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name)
        window = gui.PrereleaseWindow(root, path, path, "EN")
        self.addCleanup(window.close)
        root.deiconify()
        root.update()
        return root, window

    def test_actions_fit_and_short_windows_scroll_at_common_dpi_scales(self):
        for scale in (1, 1.25, 1.5):
            with self.subTest(scale=scale):
                root, window = self.make_window(scale)
                for size in ((window.px(1120), window.px(940)), root.minsize()):
                    root.geometry(f"{size[0]}x{size[1]}")
                    root.update()
                    for tab in (0, 1, 2):
                        window.notebook.select(tab)
                        root.update()
                        canvas, content, scrollbar = window.page_scrolls[tab]
                        self.assertGreater(canvas.winfo_height(), window.px(180))
                        for control, _ in window.controls:
                            if not control.winfo_ismapped():
                                continue
                            ancestor = control
                            while ancestor is not None and ancestor != content:
                                ancestor = getattr(ancestor, "master", None)
                            if ancestor == content:
                                self.assertGreaterEqual(control.winfo_rootx(), content.winfo_rootx())
                                self.assertLessEqual(control.winfo_rootx() + control.winfo_width(), content.winfo_rootx() + content.winfo_width())
                                if isinstance(control, gui.ttk.Button):
                                    self.assertGreaterEqual(control.winfo_width() + 2, control.winfo_reqwidth(), control.cget("text"))
                        if content.winfo_reqheight() > canvas.winfo_height() + 1:
                            self.assertTrue(scrollbar.winfo_ismapped())
                            canvas.yview_moveto(1)
                            root.update()
                            self.assertAlmostEqual(canvas.yview()[1], 1, delta=.05)

    def test_navigation_and_collapsible_log_preserve_the_notebook_contract(self):
        root, window = self.make_window(1)
        window.nav_buttons[1].invoke()
        root.update()
        self.assertEqual(window.notebook.index(window.notebook.select()), 1)
        self.assertEqual(window.nav_buttons[1].cget("style"), "Selected.Navigation.TButton")
        window.notebook.select(0)
        root.update()
        self.assertEqual(window.nav_buttons[0].cget("style"), "Selected.Navigation.TButton")
        text = window.log.get("1.0", "end")
        window.toggle_log()
        root.update()
        self.assertFalse(window.log_frame.winfo_ismapped())
        window.toggle_log()
        root.update()
        self.assertTrue(window.log_frame.winfo_ismapped())
        self.assertEqual(window.log.get("1.0", "end"), text)
        self.assertFalse(window.progress.winfo_ismapped())
        self.assertEqual(len(window.notebook.tabs()), 3)
        self.assertFalse(window.build_tools_visible.get())
        window.notebook.select(2)
        window.toggle_build_tools()
        root.update()
        self.assertTrue(window.build_tools.winfo_ismapped())
        window.toggle_build_tools()
        root.update()
        self.assertFalse(window.build_tools.winfo_ismapped())

    def test_locale_rush_actions_need_no_maintainer_paths_and_managed_locales_cannot_publish(self):
        root, window = self.make_window(1)
        window.source.set("missing-source")
        window.translations.set("missing-locale-databases")
        window.rush_language.set("FR")
        with patch.object(window, "start") as start:
            window.rush_fetch_button.invoke()
            command, kind = start.call_args.args
            self.assertIn("rush-refresh", command)
            self.assertEqual(command[-1], "fr")
            self.assertEqual(kind, "Rush locale")
            self.assertNotIn("--source-root", command)
            self.assertNotIn("--translations-root", command)
            self.assertNotIn("--push", command)
            window.start_rush_workspace("push")
            self.assertIn("rush-upload", start.call_args.args[0])
            self.assertIn("--push", start.call_args.args[0])
        for language in ("TH", "ZH-CN"):
            window.rush_language.set(language)
            root.update()
            self.assertTrue(all(str(control.cget("state")) == "disabled" for control in window.rush_publish_controls))
        window.rush_language.set("ZH-TW")
        root.update()
        self.assertTrue(all(str(control.cget("state")) == "normal" for control in window.rush_publish_controls))
        self.assertEqual(window.language.get(), "EN")


@unittest.skipIf(gui is None, "Tkinter unavailable")
class LocaleOverviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.locale = self.repo / "FR"
        self.workspace = self.locale / "Rush/Workspace/rush-pre-release.cdb"
        self.workspace.parent.mkdir(parents=True)
        row = (123, "Test card", "Translated effect")
        with closing(sqlite3.connect(self.workspace)) as database, database:
            database.execute("CREATE TABLE texts(id INTEGER, name TEXT, desc TEXT)")
            database.execute("INSERT INTO texts VALUES (?, ?, ?)", row)
        self.report = {"active_cards": 1, "pending_translations": [], "missing_artwork": [123],
                       "published_text_sha256": {"123": gui.row_hash(row)}}
        self.report_path = self.locale / "Rush/report.json"
        self.report_path.write_text(json.dumps(self.report), encoding="utf-8")
        (self.locale / "rush-version.txt").write_text("1791713663", encoding="utf-8")
        (self.locale / "rush-pre-release.ypk").touch()

    def test_missing_card_names_and_build_status_come_from_local_reports(self):
        snapshot = gui.locale_snapshot(self.repo, "FR")
        self.assertEqual(snapshot["status"], "Built · art missing")
        self.assertEqual(snapshot["changed"], [])
        self.assertIn("Artwork missing · 123 · Test card", gui.snapshot_details(snapshot))
        self.assertNotIn("up to date", gui.snapshot_details(snapshot).lower())

    def test_unbuilt_workspace_changes_and_translation_fields_are_explicit(self):
        with closing(sqlite3.connect(self.workspace)) as database, database:
            database.execute("UPDATE texts SET desc='New manual text'")
        snapshot = gui.locale_snapshot(self.repo, "FR")
        self.assertEqual(snapshot["status"], "Workspace changed")
        self.assertEqual(snapshot["changed"], ["123"])
        self.report["pending_translations"] = [{"id": 123, "name": "Test card", "fields": ["desc", "source_changed"]}]
        self.report_path.write_text(json.dumps(self.report), encoding="utf-8")
        snapshot = gui.locale_snapshot(self.repo, "FR")
        self.assertEqual(snapshot["status"], "Translation pending")
        self.assertIn("desc, source_changed", gui.snapshot_details(snapshot))

    def test_valid_json_of_wrong_shape_is_reported_as_an_error(self):
        self.report_path.write_text("[]", encoding="utf-8")
        snapshot = gui.locale_snapshot(self.repo, "FR")
        self.assertEqual(snapshot["status"], "Read error")
        self.assertIn("Expected a report object", snapshot["error"])


if __name__ == "__main__":
    unittest.main()
