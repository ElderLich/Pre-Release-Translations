from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import fetch_rush_sources as sources


class RushFetchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory / 'source'
        sleeper = patch.object(sources.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def fetch(self):
        sources.clone_repository(sources.RUSH_SOURCE, 'master', self.target, required=('RD Patch.cdb',))

    def test_partial_timeout_is_discarded_before_retry(self):
        attempts = []
        def clone(command, **kwargs):
            folder = Path(command[-1])
            self.assertFalse(folder.exists())
            self.assertFalse(self.target.exists())
            folder.mkdir()
            attempts.append(folder)
            if len(attempts) == 1:
                (folder / 'partial').write_text('unfinished')
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
            self.assertFalse(attempts[0].exists())
            (folder / 'RD Patch.cdb').write_text('fresh patch')
        with patch.object(sources, 'run_git', side_effect=clone) as run:
            self.fetch()
        self.assertEqual(run.call_count, 2)
        self.assertEqual((self.target / 'RD Patch.cdb').read_text(), 'fresh patch')
        self.assertEqual(list(self.directory.iterdir()), [self.target])

    def test_existing_checkout_is_never_reset_or_removed(self):
        self.target.mkdir()
        sentinel = self.target / 'local edit'
        sentinel.write_text('keep')
        with patch.object(sources, 'run_git') as run:
            with self.assertRaises(ValueError):
                self.fetch()
        run.assert_not_called()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_connection_failure_stops_after_bounded_attempts(self):
        with patch.object(sources, 'run_git', side_effect=subprocess.CalledProcessError(128, 'git')) as run:
            with self.assertRaisesRegex(RuntimeError, 'no packages were built'):
                self.fetch()
        self.assertEqual(run.call_count, sources.ATTEMPTS)
        self.assertTrue(all(call.kwargs['timeout'] == sources.GIT_TIMEOUT for call in run.call_args_list))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_successful_clone_missing_patch_cannot_publish_source_folder(self):
        def clone(command, **kwargs):
            Path(command[-1]).mkdir()
        with patch.object(sources, 'run_git', side_effect=clone):
            with self.assertRaises(RuntimeError):
                self.fetch()
        self.assertFalse(self.target.exists())

    def test_sparse_checkout_must_finish_and_contain_translated_database(self):
        def run(command, **kwargs):
            if 'clone' in command:
                Path(command[-1]).mkdir()
            else:
                checkout = Path(command[command.index('-C') + 1])
                database = checkout / 'Data/locales/en-US/rush_cards.cdb'
                database.parent.mkdir(parents=True)
                database.write_text('seed')
        with patch.object(sources, 'run_git', side_effect=run) as mock:
            sources.clone_repository(sources.RUSH_TRANSLATIONS, 'main', self.target,
                                     sparse=sources.RUSH_DATABASES, required=('Data/locales/en-US/rush_cards.cdb',))
        self.assertEqual(mock.call_count, 2)
        self.assertEqual(mock.call_args.args[0][-2:], ['--no-cone', '/Data/locales/*/rush_cards.cdb'])
        self.assertTrue((self.target / 'Data/locales/en-US/rush_cards.cdb').is_file())

    def test_timeout_terminates_its_workers_before_partial_checkout_cleanup(self):
        process = MagicMock()
        process.pid = 24680
        process.wait.side_effect = [subprocess.TimeoutExpired('git', 1), 1]
        process.poll.return_value = 1
        process.__enter__.return_value = process
        with patch.object(sources.subprocess, 'Popen', return_value=process), \
             patch.object(sources.subprocess, 'run') as taskkill:
            if sources.os.name == 'nt':
                with self.assertRaises(subprocess.TimeoutExpired):
                    sources.run_git(['git', 'clone'], env={}, timeout=1)
                self.assertEqual(taskkill.call_args.args[0], ['taskkill', '/F', '/T', '/PID', '24680'])
            else:
                with patch.object(sources.os, 'killpg') as killgroup:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        sources.run_git(['git', 'clone'], env={}, timeout=1)
                killgroup.assert_called_once_with(24680, sources.signal.SIGKILL)
        self.assertEqual(process.wait.call_count, 2)


if __name__ == '__main__':
    unittest.main()
