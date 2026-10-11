import argparse
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_workspace
import rush_local
try:
    import prerelease_gui as gui
except ImportError:
    gui = None


class SourceRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = self.root / "upstream"
        self.origin.mkdir()
        self.git(self.origin, "init", "-b", "master")
        (self.origin / "RD Patch.cdb").write_text("old")
        self.commit(self.origin)
        self.source = self.root / "source"
        self.git(self.root, "clone", "--no-hardlinks", str(self.origin), str(self.source))

    def git(self, cwd, *args):
        return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
                              cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, path):
        self.git(path, "add", ".")
        self.git(path, "commit", "-m", "fixture")

    def test_fetch_fast_forwards_and_repeated_fetch_is_safe(self):
        (self.origin / "RD Patch.cdb").write_text("new")
        self.commit(self.origin)
        rush_local.fetch_source(self.source, lambda _: None)
        self.assertEqual((self.source / "RD Patch.cdb").read_text(), "new")
        first = self.git(self.source, "rev-parse", "HEAD")
        rush_local.fetch_source(self.source, lambda _: None)
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD"), first)

    def test_dirty_source_is_preserved(self):
        (self.source / "RD Patch.cdb").write_text("manual work")
        with self.assertRaisesRegex(RuntimeError, "local changes"):
            rush_local.fetch_source(self.source, lambda _: None)
        self.assertEqual((self.source / "RD Patch.cdb").read_text(), "manual work")

    def test_local_commits_are_preserved(self):
        (self.source / "RD Patch.cdb").write_text("local commit")
        self.commit(self.source)
        first = self.git(self.source, "rev-parse", "HEAD")
        with self.assertRaisesRegex(RuntimeError, "local commits"):
            rush_local.fetch_source(self.source, lambda _: None)
        self.assertEqual(self.git(self.source, "rev-parse", "HEAD"), first)

    def test_other_branch_is_preserved(self):
        self.git(self.source, "checkout", "-b", "work")
        with self.assertRaisesRegex(RuntimeError, "master"):
            rush_local.fetch_source(self.source, lambda _: None)
        self.assertEqual(self.git(self.source, "branch", "--show-current"), "work")


class RushButtonContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for path in ("Tools/rush_prerelease.py", "source/RD Patch.cdb", "locales/en-US/rush_cards.cdb"):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        self.args = argparse.Namespace(repo_root=self.root, source_root=self.root / "source",
                                       translations_root=self.root / "locales", fetch=False, offline=False)

    def test_build_uses_all_locales_and_skips_git_refresh(self):
        self.args.offline = True
        with patch.object(rush_local, "fetch_source") as fetch, patch.object(local_workspace, "run") as run:
            local_workspace.command_rush(self.args)
        fetch.assert_not_called()
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--lang") + 1], "all")
        self.assertIn("--offline", command)
        self.assertNotIn("--push", command)

    def test_failed_refresh_does_not_build(self):
        self.args.fetch = True
        with patch.object(rush_local, "fetch_source", side_effect=RuntimeError("network unavailable")), patch.object(local_workspace, "run") as run:
            with self.assertRaisesRegex(SystemExit, "network unavailable"):
                local_workspace.command_rush(self.args)
        run.assert_not_called()

    def test_build_lock_rejects_second_window_then_releases(self):
        with rush_local.build_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "already running"):
                with rush_local.build_lock(self.root):
                    self.fail("Second build acquired lock")
        with rush_local.build_lock(self.root):
            pass

    @unittest.skipIf(gui is None, "Tkinter unavailable")
    def test_standard_actions_keep_push_explicit(self):
        for action in ("refresh", "upload", "pull", "status", "push"):
            command = gui.workspace_command(self.root, self.root / "local", action, "FR")
            self.assertEqual(command[command.index("--lang") + 1], "fr")
            self.assertEqual("--push" in command, action == "push")
            self.assertNotIn("rush", command)

    @unittest.skipIf(gui is None, "Tkinter unavailable")
    def test_wrapper_opens_shared_gui_and_keeps_refresh_command(self):
        import AutoPR_local as wrapper
        with patch.object(wrapper, "detect_lang", return_value="en"), patch.object(wrapper, "resolve_repo_root", return_value=self.root):
            with patch.object(sys, "argv", ["AutoPR.py"]), patch.object(gui, "launch") as launch:
                self.assertEqual(wrapper.main(), 0)
                launch.assert_called_once_with(self.root, wrapper.LOCAL_ROOT, "en")
            with patch.object(sys, "argv", ["AutoPR.py", "refresh"]), patch.object(wrapper, "run_tool", return_value=0) as run:
                self.assertEqual(wrapper.main(), 0)
                run.assert_called_once_with("refresh", "--lang", "en")

    @unittest.skipIf(gui is None, "Tkinter unavailable")
    def test_worker_streams_failures_and_finishes(self):
        events = queue.Queue()
        gui.stream_job([sys.executable, "-c", "print('failure detail'); raise SystemExit(7)"], self.root, events)
        self.assertEqual(events.get(), ("log", "failure detail\n"))
        self.assertEqual(events.get(), ("done", 7))

    @unittest.skipIf(gui is None, "Tkinter unavailable")
    def test_rush_buttons_use_worker_and_restore_controls(self):
        try:
            root = gui.tk.Tk()
        except gui.tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        window = gui.PrereleaseWindow(root, self.root, self.root, "en")
        self.addCleanup(window.close)
        window.source.set(str(self.args.source_root))
        window.translations.set(str(self.args.translations_root))
        commands, threads = [], []

        def worker(command, cwd, events):
            commands.append(command)
            threads.append(threading.get_ident())
            events.put(("log", "test job\n"))
            events.put(("done", 1 if len(commands) == 1 else 0))

        with patch.object(gui, "stream_job", side_effect=worker):
            for button in (window.fetch_button, window.build_button):
                button.invoke()
                self.assertTrue(window.busy)
                self.assertEqual(str(button.cget("state")), "disabled")
                deadline = time.monotonic() + 3
                while window.busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertFalse(window.busy)
                self.assertEqual(str(button.cget("state")), "normal")
        self.assertIn("--fetch", commands[0])
        self.assertNotIn("--fetch", commands[1])
        self.assertTrue(all(thread != threading.get_ident() for thread in threads))
        self.assertTrue(window.config_path.is_file())
        self.assertEqual(len(window.table.get_children()), 11)


if __name__ == "__main__":
    unittest.main()
