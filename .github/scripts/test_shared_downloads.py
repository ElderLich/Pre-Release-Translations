import hashlib
import io
import json
import os
from pathlib import Path
import ssl
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
import zipfile

import shared_downloads as downloads
import schedule_guard as guard


class Response(io.BytesIO):
    def __init__(self, content, *, length=None, status=200, headers=None):
        super().__init__(content)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(content) if length is None else length)}

    def geturl(self):
        return "https://cdn02.moecube.com/ygopro-super-pre/archive/current.ypk"


def package():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for member in downloads.YPK_MEMBERS:
            archive.writestr(member, 'fixture')
    return output.getvalue()


def card_database(schema="CREATE TABLE datas(id INTEGER); CREATE TABLE texts(id INTEGER,name TEXT,desc TEXT);"):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'fixture.cdb'
        connection = sqlite3.connect(path)
        try:
            connection.executescript(schema)
        finally:
            connection.close()
        return path.read_bytes()


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory / 'cards.ypk'
        environment = patch.dict(os.environ, {'AUTOPR_DOWNLOAD_CACHE': '', 'GITHUB_RUN_ID': '', 'GITHUB_RUN_ATTEMPT': ''})
        environment.start()
        self.addCleanup(environment.stop)
        sleeper = patch.object(downloads.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def download(self, url=downloads.YPK_URL_DEFAULT):
        downloads.download_file(url, self.target, user_agent='test', log_fn=lambda _: None)

    def enable_cache(self):
        cache = self.directory / 'cache'
        environment = patch.dict(os.environ, {'AUTOPR_DOWNLOAD_CACHE': str(cache),
                                              'GITHUB_RUN_ID': '101', 'GITHUB_RUN_ATTEMPT': '1'})
        environment.start()
        self.addCleanup(environment.stop)
        return downloads._cache_directory()

    def test_validated_disk_cache_reuses_bytes_for_separate_retry_target(self):
        self.enable_cache()
        script = """import sys
from pathlib import Path
from unittest.mock import patch
import shared_downloads as downloads
with patch.object(downloads.urllib.request, 'urlopen', side_effect=AssertionError('unexpected network')):
    downloads.download_file(sys.argv[1], Path(sys.argv[2]), user_agent='test', log_fn=lambda _: None)
"""
        for extension, url, payload in (('ypk', downloads.YPK_URL_DEFAULT, package()),
                                        ('cdb', 'https://database.example/cards.cdb', card_database())):
            with self.subTest(extension=extension):
                self.target = self.directory / ('first-process.' + extension)
                with patch.object(downloads.urllib.request, 'urlopen', return_value=Response(payload)) as request:
                    self.download(url)
                self.assertEqual(request.call_count, 1)
                # URL extension still validates when the target has a temporary filename.
                self.target = self.directory / ('second-process-' + extension + '.tmp')
                result = subprocess.run([sys.executable, '-c', script, url, str(self.target)],
                                        cwd=Path(downloads.__file__).parent, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.target.read_bytes(), payload)

    def test_invalid_cdb_never_enters_cache_or_replaces_target(self):
        cache = self.enable_cache()
        valid = card_database()
        self.target = self.directory / 'cards.cdb'
        self.target.write_bytes(valid)
        bad_inputs = (b'<html>upstream error</html>', valid[:100],
                      card_database('CREATE TABLE other(id INTEGER);'),
                      card_database('CREATE TABLE datas(id INTEGER); CREATE TABLE texts(id INTEGER);'))
        for body in bad_inputs:
            with self.subTest(size=len(body)), patch.object(
                    downloads.urllib.request, 'urlopen', side_effect=lambda *a, **k: Response(body)):
                with self.assertRaises(downloads.DownloadError):
                    self.download('https://database.example/download')
            self.assertEqual(self.target.read_bytes(), valid)
            self.assertFalse(cache.exists())
            self.assertFalse(list(self.directory.glob('*.part')))

    def test_cdb_validation_is_read_only_without_sidecars(self):
        self.target = self.directory / 'cards.cdb'
        original = card_database()
        self.target.write_bytes(original)
        previous = self.target.stat().st_mtime_ns
        downloads.validate_cdb(self.target)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(self.target.stat().st_mtime_ns, previous)
        self.assertEqual(list(self.directory.iterdir()), [self.target])

    def test_cached_cdb_with_matching_digest_still_requires_sqlite_validation(self):
        cache = self.enable_cache()
        self.target = self.directory / 'cards.cdb'
        url = 'https://database.example/cards.cdb'
        valid = card_database()
        with patch.object(downloads.urllib.request, 'urlopen', return_value=Response(valid)):
            self.download(url)
        stored = next(cache.glob('*.data'))
        metadata = stored.with_suffix('.json')
        invalid = b'<html>old unchecked cache entry</html>'
        stored.write_bytes(invalid)
        info = json.loads(metadata.read_text())
        info.update(size=len(invalid), sha256=hashlib.sha256(invalid).hexdigest())
        metadata.write_text(json.dumps(info))
        with patch.object(downloads.urllib.request, 'urlopen', return_value=Response(valid)) as request:
            self.download(url)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.target.read_bytes(), valid)

    def test_cdb_validation_interrupts_expensive_sqlite_work(self):
        self.target = self.directory / 'cards.cdb'
        self.target.write_bytes(card_database('''
            CREATE TABLE datas(id INTEGER);
            CREATE TABLE texts(id INTEGER,name TEXT,desc TEXT);
            WITH RECURSIVE ids(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM ids WHERE n<10000)
            INSERT INTO texts SELECT n,'name','description' FROM ids;
        '''))
        readings = iter((0, downloads.CDB_VALIDATION_BUDGET + 1))
        with patch.object(downloads.time, 'monotonic', side_effect=lambda: next(readings, downloads.CDB_VALIDATION_BUDGET + 1)):
            with self.assertRaisesRegex(downloads.DownloadError, 'validation exceeded'):
                downloads.validate_cdb(self.target)

    def test_cache_corruption_missing_files_or_wrong_url_refetches(self):
        cache = self.enable_cache()
        payload = package()
        for damage in ('corrupt', 'missing_payload', 'missing_metadata', 'wrong_url', 'bad_json'):
            with self.subTest(damage=damage):
                with patch.object(downloads.urllib.request, 'urlopen', side_effect=lambda *a, **k: Response(payload)):
                    self.download()
                stored = next(cache.glob('*.data'))
                metadata = stored.with_suffix('.json')
                if damage == 'corrupt':
                    stored.write_bytes(b'x' * len(payload))
                elif damage == 'missing_payload':
                    stored.unlink()
                elif damage == 'missing_metadata':
                    metadata.unlink()
                elif damage == 'wrong_url':
                    info = json.loads(metadata.read_text())
                    info['url'] = 'https://other.example/package.ypk'
                    metadata.write_text(json.dumps(info))
                else:
                    metadata.write_text('not json')
                with patch.object(downloads.urllib.request, 'urlopen', return_value=Response(payload)) as request:
                    self.download()
                self.assertEqual(request.call_count, 1)
                self.assertEqual(self.target.read_bytes(), payload)

    def test_cache_validates_again_and_never_saves_invalid_bytes(self):
        cache = self.enable_cache()
        with patch.object(downloads.urllib.request, 'urlopen', return_value=Response(package())):
            self.download()
        def reject(path):
            raise downloads.DownloadError('current validation rejected these bytes')
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=lambda *a, **k: Response(package())):
            with self.assertRaises(downloads.DownloadError):
                downloads.download_file(downloads.YPK_URL_DEFAULT, self.target, user_agent='test',
                                        log_fn=lambda _: None, validator=reject)
        self.assertFalse(list(cache.glob('*.data')))
        self.assertFalse(list(self.directory.glob('*.part')))

    def test_cache_isolates_urls_and_github_runs_and_attempts(self):
        self.enable_cache()
        calls = []
        def response(request, **kwargs):
            calls.append(request.full_url)
            return Response(package())
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=response):
            self.download()
            self.download('https://custom.example/cards.ypk')
            with patch.dict(os.environ, {'GITHUB_RUN_ID': '102'}):
                self.download()
            with patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '2'}):
                self.download()
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[1], 'https://custom.example/cards.ypk')

    def test_working_endpoint_is_remembered_for_other_inputs_in_same_run(self):
        self.enable_cache()
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=[TimeoutError(), Response(package())]):
            self.download()
        with patch.object(downloads.urllib.request, 'urlopen', return_value=Response(b'[]')) as request:
            downloads.fetch_release_json(downloads.JSON_URL_DEFAULT, user_agent='test', log_fn=lambda _: None)
        self.assertEqual(request.call_args.args[0].full_url,
                         downloads.UPSTREAM_BASES[1] + 'data/test-release.json')
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '102'}), patch.object(
                downloads.urllib.request, 'urlopen', return_value=Response(b'[]')) as request:
            downloads.fetch_release_json(downloads.JSON_URL_DEFAULT, user_agent='test', log_fn=lambda _: None)
        self.assertEqual(request.call_args.args[0].full_url, downloads.JSON_URL_DEFAULT)

    def test_failed_download_is_not_cached_or_remembered(self):
        cache = self.enable_cache()
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=lambda *a, **k: Response(b'bad')):
            with self.assertRaises(downloads.DownloadError):
                self.download()
        self.assertFalse(cache.exists())
        self.assertFalse(list(self.directory.glob('*.part')))

    def test_trickled_transfer_stops_at_budget_without_unbounded_read(self):
        clock = [0.0]
        class Trickle(Response):
            def read1(self, size):
                self.assert_sized = size > 0
                clock[0] += downloads.DOWNLOAD_BUDGET / 2
                return b'x'
            def read(self, *args):
                raise AssertionError('Use one bounded underlying read')
        with patch.object(downloads.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(
                downloads.urllib.request, 'urlopen', side_effect=lambda *a, **k: Trickle(
                    b'', headers={'Content-Type': 'application/zip'})) as request:
            with self.assertRaisesRegex(downloads.DownloadError, 'budget'):
                self.download()
        self.assertEqual(request.call_count, downloads.DOWNLOAD_ATTEMPTS)
        self.assertEqual(clock[0], downloads.DOWNLOAD_ATTEMPTS * downloads.DOWNLOAD_BUDGET)
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.directory.iterdir()))

    def test_exhausted_first_attempt_still_tries_and_validates_backup(self):
        clock = [0.0]
        payload = package()
        self.target.write_bytes(b'last good')
        class Slow(Response):
            def read1(self, size):
                clock[0] += downloads.DOWNLOAD_BUDGET / 2
                return b'x'
        class Backup(Response):
            def read1(inner_self, size):
                # The old target survives until the complete backup validates.
                self.assertEqual(self.target.read_bytes(), b'last good')
                clock[0] += 1
                return super().read1(size)
        with patch.object(downloads.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(
                downloads.urllib.request, 'urlopen', side_effect=[
                    Slow(b'', headers={'Content-Type': 'application/zip'}), Backup(payload)
                ]) as request:
            self.download()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].args[0].full_url,
                         downloads.UPSTREAM_BASES[1] + 'archive/ygopro-super-pre.ypk')
        self.assertEqual(self.target.read_bytes(), payload)
        self.assertEqual(list(self.directory.iterdir()), [self.target])

    def test_slow_download_reports_progress_with_and_without_content_length(self):
        payload = package()
        for headers in ({'Content-Length': str(len(payload))}, {'Content-Type': 'application/zip'}):
            with self.subTest(headers=headers):
                clock = [0.0]
                messages = []
                class Slow(Response):
                    def read1(self, size):
                        clock[0] += 11
                        return super().read1(size)
                with patch.object(downloads.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(
                        downloads.urllib.request, 'urlopen', return_value=Slow(payload, headers=headers)):
                    downloads.download_file(downloads.YPK_URL_DEFAULT, self.target,
                                            user_agent='test', log_fn=messages.append)
                progress = [message for message in messages if message.startswith('Download progress:')]
                self.assertEqual(len(progress), 1)
                self.assertIn(f'{len(payload):,}', progress[0])
                self.assertIn('11.0s (attempt 1/3)', progress[0])
                self.assertEqual('100%' in progress[0], 'Content-Length' in headers)
                self.assertEqual(self.target.read_bytes(), payload)

    def test_tls_failure_uses_official_alternative_without_disabling_validation(self):
        url = downloads.UPSTREAM_BASES[1] + 'archive/ygopro-super-pre.ypk'
        payload = package()
        error = urllib.error.URLError(ssl.SSLCertVerificationError('hostname mismatch'))
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=[error, Response(payload)]) as request:
            self.download(url)
        self.assertEqual(self.target.read_bytes(), payload)
        self.assertEqual(request.call_args_list[1].args[0].full_url, downloads.YPK_URL_DEFAULT)
        self.assertTrue(all('context' not in call.kwargs for call in request.call_args_list))

    def test_incomplete_response_is_retried_not_published(self):
        payload = package()
        self.target.write_bytes(b'previous package')
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=[
            Response(payload[:15], length=len(payload)), Response(payload)
        ]) as request:
            self.download()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(self.target.read_bytes(), payload)
        self.assertEqual(list(self.directory.iterdir()), [self.target])

    def test_bad_archive_or_http_range_response_keeps_existing_file(self):
        for response in (lambda: Response(b'<html>outage</html>'),
                         lambda: Response(package(), status=206)):
            self.target.write_bytes(b'last good')
            with patch.object(downloads.urllib.request, 'urlopen', side_effect=lambda *a, **k: response()) as request:
                with self.assertRaises(downloads.DownloadError):
                    self.download()
            self.assertEqual(self.target.read_bytes(), b'last good')
            self.assertEqual(request.call_count, downloads.DOWNLOAD_ATTEMPTS)
            self.assertEqual(list(self.directory.iterdir()), [self.target])

    def test_temporary_filename_still_validates_ypk_from_url(self):
        self.target = self.directory / 'ygopro-super-pre.download.tmp'
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=lambda *a, **k: Response(b'bad')):
            with self.assertRaises(downloads.DownloadError):
                self.download()
        self.assertFalse(self.target.exists())

    def test_custom_url_never_silently_substitutes_a_different_source(self):
        url = 'https://custom.example/cards.ypk'
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=TimeoutError) as request:
            with self.assertRaises(downloads.DownloadError):
                self.download(url)
        self.assertEqual({call.args[0].full_url for call in request.call_args_list}, {url})

    def test_metadata_retries_invalid_or_non_list_json(self):
        value = [{'name': 'Card', 'desc': 'Effect'}]
        with patch.object(downloads.urllib.request, 'urlopen', side_effect=[
            Response(b'<html>outage</html>'), Response(b'{}'), Response(json.dumps(value).encode())
        ]) as request:
            result = downloads.fetch_release_json(downloads.JSON_URL_DEFAULT, user_agent='test', log_fn=lambda _: None)
        self.assertEqual(result, value)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args_list[1].args[0].full_url,
                         downloads.UPSTREAM_BASES[1] + 'data/test-release.json')

    def test_daily_guard_uses_package_endpoint_fallback(self):
        with patch.object(guard.urllib.request, 'urlopen', side_effect=[
            urllib.error.URLError('certificate mismatch'),
            Response(b'', headers={'Last-Modified': 'Fri, 09 Oct 2026 14:44:56 GMT'})
        ]) as request:
            self.assertIsNotNone(guard.upstream_modified(downloads.YPK_URL_DEFAULT))
        self.assertEqual(request.call_count, 2)


if __name__ == '__main__':
    unittest.main()
