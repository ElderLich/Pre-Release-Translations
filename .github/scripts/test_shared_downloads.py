import io
import json
from pathlib import Path
import ssl
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


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory / 'cards.ypk'
        sleeper = patch.object(downloads.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def download(self, url=downloads.YPK_URL_DEFAULT):
        downloads.download_file(url, self.target, user_agent='test', log_fn=lambda _: None)

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
