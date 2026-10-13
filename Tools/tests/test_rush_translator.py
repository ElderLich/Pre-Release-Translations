import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_workspace as helper
import rush_prerelease as rush
import rush_translator as translator
import AutoPR_local as wrapper


class RushTranslatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo, self.local_root = self.root / 'repo', self.root / 'translator'
        self.published, self.local, self.baseline, self.reports = translator.paths(self.repo, self.local_root, 'FR')
        self.write_published([100, 101])

    def write_published(self, ids, *, name='Published', effect='Published effect', attack=1000):
        datas = {i: (i, 4, 0, 0, 33, attack, 1000, 4, 1, 1, 0) for i in ids}
        texts = {i: (i, f'{name} {i}', effect, *([''] * 16)) for i in ids}
        rush.write_changed(self.published, rush.cdb_bytes(datas, texts))

    def edit_local(self, effect):
        datas, texts = rush.read_cdb(self.local)
        row = list(texts[100]); row[2] = effect; texts[100] = tuple(row)
        rush.write_changed(self.local, rush.cdb_bytes(datas, texts))

    def refresh(self, **kwargs):
        return translator.refresh_locale(self.repo, self.local_root, 'FR', log=lambda _: None, **kwargs)

    def test_only_transsuperpre_is_needed_and_refresh_does_not_push_or_fetch_sources(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('Unexpected network/command')):
            args = helper.build_parser().parse_args(['--repo-root', str(self.repo), '--local-root', str(self.local_root),
                                                    'rush-refresh', '--lang', 'fr', '--no-pull-first'])
            helper.command_rush_refresh(args)
        self.assertEqual(set(rush.read_cdb(self.local)[0]), {100, 101})
        self.assertEqual(self.baseline.read_bytes(), self.published.read_bytes())

    def test_new_removed_cards_and_gameplay_merge_without_losing_local_text(self):
        self.refresh()
        self.edit_local('Texte du traducteur')
        self.write_published([100, 102], attack=1900)
        self.refresh()
        datas, texts = rush.read_cdb(self.local)
        self.assertEqual(set(datas), {100, 102})
        self.assertEqual(datas[100][5], 1900)
        self.assertEqual(texts[100][2], 'Texte du traducteur')
        self.assertEqual(rush.read_json(self.reports / 'sync.json')['removed_ids'], [101])

    def test_existing_first_import_preserves_translation_without_baseline(self):
        rush.write_changed(self.local, self.published.read_bytes())
        self.edit_local('Existing local translation')
        self.write_published([100, 102], effect='New published effect')
        self.refresh()
        self.assertEqual(rush.read_cdb(self.local)[1][100][2], 'Existing local translation')
        self.assertTrue(rush.read_json(self.reports / 'sync.json')['adopted_existing_workspace'])

    def test_unedited_fields_follow_new_published_translations(self):
        self.refresh()
        self.write_published([100, 101], name='Nouveau nom', effect='Nouvel effet')
        self.refresh()
        self.assertEqual(rush.read_cdb(self.local)[1][100][1:3], ('Nouveau nom 100', 'Nouvel effet'))

    def test_conflicts_preserve_both_databases_until_reviewed(self):
        self.refresh()
        self.edit_local('Local correction')
        self.write_published([100, 101], effect='Remote correction')
        before = {p:p.read_bytes() for p in (self.local, self.published, self.baseline)}
        with self.assertRaisesRegex(ValueError, 'No card database was overwritten'):
            self.refresh()
        self.assertEqual(before, {p:p.read_bytes() for p in before})
        self.assertEqual(rush.read_json(self.reports / 'sync.json')['conflicts'][0]['field'], 'desc')
        self.refresh(keep_local=True)
        self.assertEqual(rush.read_cdb(self.local)[1][100][2], 'Local correction')
        self.assertTrue(rush.read_json(self.reports / 'sync.json')['resolved_using_local'])

    def test_upload_only_changes_selected_workspace_and_prunes_retired_ids(self):
        self.refresh()
        self.edit_local('Local correction')
        other = self.repo / 'EN/Rush/Workspace/rush-pre-release.cdb'
        rush.write_changed(other, self.published.read_bytes())
        other_before = other.read_bytes()
        self.write_published([100])
        with patch.object(subprocess, 'run', side_effect=AssertionError('Unexpected command')):
            changed = translator.copy_locale(self.repo, self.local_root, 'FR', log=lambda _: None)
        self.assertEqual(changed, [self.published])
        self.assertEqual(set(rush.read_cdb(self.published)[0]), {100})
        self.assertEqual(rush.read_cdb(self.published)[1][100][2], 'Local correction')
        self.assertEqual(other.read_bytes(), other_before)

    def test_upload_conflict_and_dry_run_do_not_write_databases(self):
        self.refresh()
        self.edit_local('Local correction')
        before = self.published.read_bytes()
        translator.copy_locale(self.repo, self.local_root, 'FR', dry_run=True, log=lambda _: None)
        self.assertEqual(self.published.read_bytes(), before)
        self.write_published([100, 101], effect='Remote correction')
        before = self.published.read_bytes()
        with self.assertRaises(ValueError):
            translator.copy_locale(self.repo, self.local_root, 'FR', log=lambda _: None)
        self.assertEqual(self.published.read_bytes(), before)

    def test_thai_upload_explains_english_policy(self):
        with self.assertRaisesRegex(ValueError, 'Thai Rush is generated from English'):
            translator.copy_locale(self.repo, self.local_root, 'TH')

    def test_simplified_chinese_upload_explains_upstream_policy(self):
        with self.assertRaisesRegex(ValueError, "managed by upstream"):
            translator.copy_locale(self.repo, self.local_root, "ZH-CN")

    def test_dirty_repository_is_not_pulled(self):
        status = subprocess.CompletedProcess([], 0, stdout=' M FR/Rush/Workspace/rush-pre-release.cdb\n')
        with patch.object(subprocess, 'run', return_value=status), patch.object(helper, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'uncommitted changes'):
                helper.pull_clean_translator_repo(self.repo)
            run.assert_not_called()

    def test_wrong_branch_is_not_pulled(self):
        outputs = [subprocess.CompletedProcess([], 0, stdout=''), subprocess.CompletedProcess([], 0, stdout='translation-work\n')]
        with patch.object(subprocess, 'run', side_effect=outputs), patch.object(helper, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'Switch Pre-Release Translations to main'):
                helper.pull_clean_translator_repo(self.repo)
            run.assert_not_called()

    def test_refresh_pull_is_fast_forward_only(self):
        outputs = [subprocess.CompletedProcess([], 0, stdout=''), subprocess.CompletedProcess([], 0, stdout='main\n')]
        with patch.object(subprocess, 'run', side_effect=outputs), patch.object(helper, 'run') as run:
            helper.pull_clean_translator_repo(self.repo)
        run.assert_called_once_with(['git', 'pull', '--ff-only', 'origin', 'main'], self.repo)

    def test_rush_upload_requires_explicit_push_and_uses_selected_locale(self):
        self.refresh()
        self.edit_local('Translation')
        args = helper.build_parser().parse_args(['--repo-root', str(self.repo), '--local-root', str(self.local_root),
                                                'rush-upload', '--lang', 'fr'])
        with patch.object(helper, 'commit_and_push') as push:
            helper.command_rush_upload(args)
            push.assert_not_called()
            args.push = True
            with patch.object(helper, 'ensure_no_staged_changes'), patch.object(helper, 'ensure_main_branch'):
                helper.command_rush_upload(args)
            push.assert_called_once_with(args, [self.published])
        self.assertEqual(args.message, 'Update FR Rush translations')

    def test_wrapper_rush_actions_are_bound_to_its_locale(self):
        for command, expected in [('rush-refresh', ('rush-refresh','--lang','fr')),
                                  ('rush-pull', ('rush-pull','--lang','fr')),
                                  ('rush-copy', ('rush-upload','--lang','fr')),
                                  ('rush-upload', ('rush-upload','--lang','fr','--push'))]:
            with patch.object(wrapper,'detect_lang',return_value='fr'), patch.object(sys,'argv',['AutoPR.py',command]), patch.object(wrapper,'run_tool',return_value=0) as run:
                self.assertEqual(wrapper.main(),0)
                run.assert_called_once_with(*expected)

    def test_rush_supports_all_locales_without_exposing_them_to_standard(self):
        for language in ('zh-cn', 'zh-tw'):
            args = helper.build_parser().parse_args(['rush-refresh','--lang',language,'--no-pull-first'])
            self.assertEqual(args.lang,language)
        self.assertEqual(len(helper.LANGS),9)
        self.assertEqual(len(helper.RUSH_LANGS),11)



    def test_publish_rejects_feature_branch_before_copying_local_text(self):
        self.refresh()
        self.edit_local('Unpublished local correction')
        before = self.published.read_bytes()
        args = helper.build_parser().parse_args(['--repo-root', str(self.repo), '--local-root', str(self.local_root),
                                                'rush-upload', '--lang', 'fr', '--push'])
        branch = subprocess.CompletedProcess([], 0, stdout='feature-work\n')
        with patch.object(subprocess, 'run', return_value=branch), patch.object(helper, 'commit_and_push') as push:
            with self.assertRaisesRegex(SystemExit, 'Switch Pre-Release Translations to main'):
                helper.command_rush_upload(args)
            push.assert_not_called()
        self.assertEqual(self.published.read_bytes(), before)
        with patch.object(helper, 'ensure_main_branch', side_effect=AssertionError('Copy-only checked publishing branch')):
            args.push = False
            helper.command_rush_upload(args)
        self.assertEqual(rush.read_cdb(self.published)[1][100][2], 'Unpublished local correction')

    def test_failed_push_can_be_retried_without_creating_another_commit(self):
        args = argparse.Namespace(repo_root=str(self.repo), lang='fr', message='Update FR Rush translations')
        commands = []
        def run(command, cwd):
            commands.append(command)
            if command[:2] == ['git', 'push'] and sum(item[:2] == ['git', 'push'] for item in commands) == 1:
                raise SystemExit(1)
            return subprocess.CompletedProcess(command, 0)
        # The first upload has staged changes; the retry has none because the
        # previous attempt already committed successfully before its push failed.
        diffs = [subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]
        with patch.object(helper, 'ensure_main_branch'), patch.object(helper, 'ensure_no_staged_changes'), patch.object(subprocess, 'run', side_effect=diffs), patch.object(helper, 'run', side_effect=run):
            with self.assertRaises(SystemExit):
                helper.commit_and_push(args, [self.published])
            helper.commit_and_push(args, [self.published])
        self.assertEqual(sum(command[:2] == ['git', 'commit'] for command in commands), 1)
        self.assertEqual(sum(command == ['git', 'push', 'origin', 'main'] for command in commands), 2)


if __name__ == '__main__':
    unittest.main()
