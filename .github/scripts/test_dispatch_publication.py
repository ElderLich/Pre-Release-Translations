"""Exercise guarded Actions continuations with a fake API; never sends a dispatch."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import dispatch_publication as dispatcher


ROOT = Path(__file__).resolve().parents[2]
TOKEN = 'fixture-token-must-never-appear-in-output'


def environment(source='rush-prerelease.yml', **overrides):
    return {
        'GITHUB_REPOSITORY': dispatcher.REPOSITORY,
        'GITHUB_ACTOR': 'github-actions[bot]', 'SOURCE_RESULT': 'success',
        'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
        'GITHUB_WORKFLOW_REF': dispatcher.REPOSITORY + '/.github/workflows/' + source + '@refs/heads/main',
        'GH_TOKEN': TOKEN, **overrides,
    }


def response(status=204):
    result = Mock(status=status)
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class DispatchGuardsTests(unittest.TestCase):
    def test_exact_twenty_source_handoffs_are_acyclic_and_end_at_existing_mirror(self):
        self.assertEqual(len(dispatcher.CONTINUATIONS), 20)
        for source, target in dispatcher.CONTINUATIONS.items():
            with self.subTest(source=source):
                self.assertEqual(dispatcher.continuation(environment(source), {}), target)
                visited = {source}
                while target in dispatcher.CONTINUATIONS:
                    self.assertNotIn(target, visited)
                    visited.add(target)
                    target = dispatcher.CONTINUATIONS[target]
                self.assertEqual(target, 'cloudflare-prerelease.yml')
                self.assertLessEqual(len(visited), 2)

    def test_human_fork_branch_failure_cancellation_and_unsupported_event_skip(self):
        for overrides in (
            {'GITHUB_ACTOR': 'ElderLich'}, {'GITHUB_REPOSITORY': 'fork/Pre-Release-Translations'},
            {'GITHUB_REF': 'refs/heads/test'}, {'GITHUB_REF': 'refs/tags/main'},
            {'SOURCE_RESULT': 'failure'}, {'SOURCE_RESULT': 'cancelled'}, {'SOURCE_RESULT': 'skipped'},
            {'SOURCE_RESULT': ''}, {'GITHUB_EVENT_NAME': 'pull_request'}, {'GITHUB_EVENT_NAME': 'pull_request_target'},
            {'GITHUB_WORKFLOW_REF': dispatcher.REPOSITORY + '/.github/workflows/rush-prerelease.yml@refs/heads/test'},
        ):
            with self.subTest(overrides=overrides):
                self.assertIsNone(dispatcher.continuation(environment(**overrides), {}))
        for source in ('cloudflare-prerelease.yml', 'daily-automation.yml', '../rush-prerelease.yml',
                       'release.yml', 'rush-prerelease.yml/extra'):
            self.assertIsNone(dispatcher.continuation(environment(source), {}))

    def test_workflow_run_requires_its_actual_successful_same_repository_main_context(self):
        env = environment('en-release.yml', GITHUB_EVENT_NAME='workflow_run')
        valid = {'conclusion': 'success', 'head_branch': 'main',
                 'head_repository': {'full_name': dispatcher.REPOSITORY}}
        self.assertEqual(dispatcher.continuation(env, {'workflow_run': valid}), 'cloudflare-prerelease.yml')
        for invalid in ({}, {**valid, 'conclusion': 'failure'}, {**valid, 'head_branch': 'other'},
                        {**valid, 'head_repository': {}},
                        {**valid, 'head_repository': {'full_name': 'fork/Pre-Release-Translations'}}):
            self.assertIsNone(dispatcher.continuation(env, {'workflow_run': invalid}))

    def test_actual_workflows_gate_only_continuation_job_and_preserve_human_completion_triggers(self):
        for source, target in dispatcher.CONTINUATIONS.items():
            with self.subTest(source=source):
                content = (ROOT / '.github/workflows' / source).read_text(encoding='utf-8')
                before, job = content.split('\n  continue-publication:\n')
                owner = 'sync' if source.endswith('-workspace-sync.yml') else 'build-zh-tw' if source == 'autopr-zh-tw.yml' else 'release'
                self.assertNotIn('actions: write', before)
                self.assertIn(f'needs: {owner}', job)
                self.assertIn(f"needs.{owner}.result == 'success'", job)
                for guard in ("github.repository == 'ElderLich/Pre-Release-Translations'",
                              "github.actor == 'github-actions[bot]'", "github.ref == 'refs/heads/main'",
                              "github.event.workflow_run.conclusion == 'success'",
                              "github.event.workflow_run.head_branch == 'main'",
                              "github.event.workflow_run.head_repository.full_name == github.repository"):
                    self.assertIn(guard, job)
                self.assertIn('    permissions:\n      contents: read\n      actions: write\n', job)
                self.assertIn('ref: main', job)
                self.assertIn('persist-credentials: false', job)
                self.assertIn('run: python3 .github/scripts/dispatch_publication.py', job)
                self.assertIn('SOURCE_RESULT: ${{ needs.' + owner + '.result }}', job)
                # Existing no-op branches exit successfully; the continuation must
                # depend on that result, not a new-package flag which would stop retries.
                no_change_branch = before.split('if git diff --cached --quiet; then', 1)[1].split('fi', 1)[0]
                self.assertIn('exit 0', no_change_branch)
                self.assertNotIn('changed', job)
                downstream = (ROOT / '.github/workflows' / target).read_text(encoding='utf-8')
                source_name = before.splitlines()[0].removeprefix('name: ')
                self.assertIn('"' + source_name + '"', downstream.split('  workflow_run:', 1)[1])


class DispatchApiTests(unittest.TestCase):
    def test_acknowledged_dispatch_uses_fixed_api_main_only_and_existing_defaults(self):
        api = Mock()
        api.open.return_value = response()
        dispatcher.dispatch('cloudflare-prerelease.yml', TOKEN, api)
        request = api.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.github.com/repos/ElderLich/Pre-Release-Translations/actions/workflows/cloudflare-prerelease.yml/dispatches')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(json.loads(request.data), {'ref': 'main'})
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TOKEN)
        self.assertEqual(api.open.call_args.kwargs, {'timeout': 30})

    def test_transient_failures_have_bounded_retry(self):
        api, sleep = Mock(), Mock()
        api.open.side_effect = [HTTPError('https://api.github.com', 503, TOKEN, {}, None), URLError(TOKEN), response()]
        dispatcher.dispatch('en-release.yml', TOKEN, api, sleep)
        self.assertEqual(api.open.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])

    def test_permanent_failure_does_not_retry_or_expose_response(self):
        api, sleep = Mock(), Mock()
        api.open.side_effect = HTTPError('https://api.github.com', 403, TOKEN, {}, io.BytesIO(TOKEN.encode()))
        with self.assertRaises(dispatcher.DispatchError) as raised:
            dispatcher.dispatch('cloudflare-prerelease.yml', TOKEN, api, sleep)
        self.assertIn('HTTP 403', str(raised.exception))
        self.assertIn('Source publication is preserved', str(raised.exception))
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertEqual(api.open.call_count, 1)
        sleep.assert_not_called()

    def test_exhausted_network_failure_is_diagnosable_and_preserves_source(self):
        api, sleep = Mock(), Mock()
        api.open.side_effect = URLError(TOKEN)
        with self.assertRaises(dispatcher.DispatchError) as raised:
            dispatcher.dispatch('cloudflare-prerelease.yml', TOKEN, api, sleep)
        self.assertEqual(api.open.call_count, 3)
        self.assertIn('rerun', str(raised.exception))
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_unknown_target_empty_token_redirect_and_unexpected_ack_fail_closed(self):
        api = Mock()
        with self.assertRaises(dispatcher.DispatchError):
            dispatcher.dispatch('arbitrary.yml', TOKEN, api)
        with self.assertRaises(dispatcher.DispatchError):
            dispatcher.dispatch('en-release.yml', '', api)
        api.open.assert_not_called()
        with self.assertRaises(dispatcher.DispatchError):
            dispatcher.NoRedirects().redirect_request(None, None, 302, 'Found', {}, 'https://other.invalid')
        api.open.return_value = response(200)
        with self.assertRaises(dispatcher.DispatchError):
            dispatcher.dispatch('en-release.yml', TOKEN, api)

    def test_cli_success_including_no_change_run_requests_recovery_and_writes_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / 'summary.md'
            env = environment(GITHUB_STEP_SUMMARY=str(summary))
            output = io.StringIO()
            with patch.dict(os.environ, env, clear=True), patch.object(dispatcher, 'dispatch') as send, contextlib.redirect_stdout(output):
                self.assertEqual(dispatcher.main(), 0)
            send.assert_called_once_with('cloudflare-prerelease.yml', TOKEN)
            self.assertNotIn(TOKEN, output.getvalue())
            self.assertIn('Requested cloudflare-prerelease.yml', summary.read_text())

    def test_cli_skipped_or_failed_source_does_not_contact_api(self):
        for result in ('failure', 'cancelled', 'skipped'):
            with patch.dict(os.environ, environment(SOURCE_RESULT=result), clear=True), patch.object(dispatcher, 'dispatch') as send, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(dispatcher.main(), 0)
            send.assert_not_called()

    def test_cli_dispatch_failure_fails_continuation_with_recovery_diagnostic(self):
        error = io.StringIO()
        with patch.dict(os.environ, environment(), clear=True), patch.object(dispatcher, 'dispatch', side_effect=dispatcher.DispatchError('Source publication is preserved; rerun continuation.')), contextlib.redirect_stderr(error):
            self.assertEqual(dispatcher.main(), 1)
        self.assertIn('::error::Source publication is preserved', error.getvalue())
        self.assertNotIn(TOKEN, error.getvalue())


if __name__ == '__main__':
    unittest.main()
