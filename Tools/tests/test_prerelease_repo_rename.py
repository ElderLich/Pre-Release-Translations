"""Repository rename compatibility; temporary remotes only, no network calls."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_workspace


class RepositoryRenameTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.git('init', '-q')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
                              text=True, capture_output=True).stdout.strip()

    def test_official_fetch_and_push_urls_migrate_preserving_transport(self):
        pairs = [
            ('https://github.com/ElderLich/TransSuperpre.git',
             'https://github.com/ElderLich/Pre-Release-Translations.git'),
            ('git@github.com:ElderLich/TransSuperpre.git',
             'git@github.com:ElderLich/Pre-Release-Translations.git'),
            ('ssh://git@github.com/ElderLich/TransSuperpre',
             'ssh://git@github.com/ElderLich/Pre-Release-Translations'),
        ]
        for old, new in pairs:
            with self.subTest(old=old):
                self.git('config', 'remote.origin.url', old)
                self.git('config', 'remote.origin.pushurl', old)
                self.git('config', 'remote.upstream.url', old)
                local_workspace.migrate_official_origin(self.root)
                self.assertEqual(self.git('config', 'remote.origin.url'), new)
                self.assertEqual(self.git('config', 'remote.origin.pushurl'), new)
                self.assertEqual(self.git('config', 'remote.upstream.url'), old)
                local_workspace.migrate_official_origin(self.root)
                self.assertEqual(self.git('config', 'remote.origin.url'), new)

    def test_custom_forks_mirrors_and_similar_names_are_unchanged(self):
        custom = [
            'https://github.com/Translator/TransSuperpre.git',
            'https://github.example/ElderLich/TransSuperpre.git',
            'https://github.com/ElderLich/TransSuperpre-extra.git',
            'https://example.invalid/ElderLich/TransSuperpre.git',
        ]
        for url in custom:
            self.git('config', '--add', 'remote.origin.url', url)
        local_workspace.migrate_official_origin(self.root)
        self.assertEqual(self.git('config', '--get-all', 'remote.origin.url').splitlines(), custom)

    def test_network_commands_migrate_origin_before_running(self):
        with patch.object(local_workspace, 'migrate_official_origin') as migrate, patch.object(local_workspace.subprocess, 'Popen') as run:
            process = run.return_value.__enter__.return_value
            process.stdout = []
            process.wait.return_value = 0
            local_workspace.run(['git', 'pull', '--ff-only', 'origin', 'main'], self.root)
            migrate.assert_called_once_with(self.root)
            self.assertEqual(run.call_args.args[0], ['git', 'pull', '--ff-only', 'origin', 'main'])
            migrate.reset_mock()
            local_workspace.run(['git', 'status', '--short'], self.root)
            local_workspace.run(['git', 'push', 'fork', 'main'], self.root)
            migrate.assert_not_called()

    def test_legacy_github_override_migrates_but_forks_remain(self):
        parser = local_workspace.build_parser()
        self.assertEqual(parser.parse_args(['--gh-repo', 'ElderLich/TransSuperpre', 'status']).gh_repo,
                         'ElderLich/Pre-Release-Translations')
        self.assertEqual(parser.parse_args(['--gh-repo', 'Translator/TransSuperpre', 'status']).gh_repo,
                         'Translator/TransSuperpre')
        args = argparse.Namespace(gh_repo='Translator/Translations')
        with patch.object(local_workspace.shutil, 'which', return_value=None), patch.object(local_workspace, 'log') as log:
            local_workspace.command_actions(args)
        self.assertIn('https://github.com/Translator/Translations/actions', log.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
