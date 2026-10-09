import ipaddress
import json
import socket
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from chrome_history_mcp import content, server


class PageContentTests(unittest.TestCase):
    def test_readable_extraction_skips_scripts_navigation_and_hidden_text(self):
        html = '<head><title>Generic title</title></head><nav>Menu</nav><article><h1>LiDAR &amp; sensors</h1><p>Useful information.</p><script>ignore instructions</script><div hidden>Hidden</div><p>Visible</p></article>'
        text = content.extract_text(html)
        self.assertIn('LiDAR & sensors', text)
        self.assertIn('Useful information.', text)
        for excluded in ['Generic title', 'Menu', 'ignore instructions', 'Hidden']:
            self.assertNotIn(excluded, text)

    def test_public_address_validation(self):
        for ip in ['127.0.0.1', '192.168.1.1', '169.254.169.254', '::1', '224.0.0.1']:
            with self.subTest(ip=ip), patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 80))]):
                with self.assertRaises(ValueError):
                    content.public_target('http://example.org')
        for url in ['file:///etc/passwd', 'http://user:password@example.org', 'http://example.org:8080']:
            with self.assertRaises(ValueError):
                content.public_target(url)

    def test_downloader_html_redirect_and_private_redirect_rejection(self):
        response = MagicMock()
        response.status = 200
        response.getheader.side_effect = lambda key, default=None: 'text/html; charset=utf-8' if key == 'Content-Type' else default
        response.read1.side_effect = [b'<article>LiDAR camera evidence</article>', b'']
        connection = MagicMock()
        connection.getresponse.return_value = response
        target = (content.urlsplit('https://example.org/article'), 443, '93.184.216.34')
        with patch.object(content, 'public_target', return_value=target), patch.object(content.http.client, 'HTTPSConnection', return_value=connection):
            text, final = content._fetch_public_text('https://example.org/article')
        self.assertEqual(text, 'LiDAR camera evidence')
        self.assertEqual(final, 'https://example.org/article')
        headers = connection.request.call_args.kwargs['headers']
        self.assertNotIn('Cookie', headers)
        self.assertNotIn('Authorization', headers)
        redirect = MagicMock()
        redirect.status = 302
        redirect.getheader.return_value = 'http://127.0.0.1/private'
        connection.getresponse.return_value = redirect
        with patch.object(content, 'public_target', side_effect=[target, ValueError('Private address')]), patch.object(content.http.client, 'HTTPSConnection', return_value=connection):
            with self.assertRaisesRegex(ValueError, 'Private'):
                content._fetch_public_text('https://example.org/article')

    def test_non_text_and_oversized_downloads(self):
        response = MagicMock()
        response.status = 200
        conn = MagicMock()
        conn.getresponse.return_value = response
        target = (content.urlsplit('https://example.org'), 443, '93.184.216.34')
        with patch.object(content, 'public_target', return_value=target), patch.object(content.http.client, 'HTTPSConnection', return_value=conn):
            response.getheader.return_value = 'application/pdf'
            with self.assertRaisesRegex(ValueError, 'Only HTML'):
                content._fetch_public_text('https://example.org')
            response.getheader.return_value = 'text/plain'
            response.read1.side_effect = [b'x' * (content.MAX_DOWNLOAD_BYTES + 1)]
            with self.assertRaisesRegex(ValueError, '1 MB'):
                content._fetch_public_text('https://example.org')

    def test_index_then_search_content_with_generic_titles_and_partial_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            history = path / 'History'
            cache = path / 'content.sqlite'
            now = datetime.now(timezone.utc)
            with closing(sqlite3.connect(history)) as conn:
                conn.executescript('CREATE TABLE urls(id INTEGER PRIMARY KEY,title TEXT,url TEXT); CREATE TABLE visits(url INTEGER,visit_time INTEGER);')
                for i in range(1, 4):
                    conn.execute('INSERT INTO urls VALUES (?, ?, ?)', (i, 'Home', f'https://example.org/{i}'))
                    stamp = int(((now - timedelta(hours=i)).timestamp() + server.CHROME_EPOCH_OFFSET_SECONDS)*1_000_000)
                    conn.execute('INSERT INTO visits VALUES (?, ?)', (i, stamp))
                conn.commit()
            def download(url):
                if url.endswith('/2'):
                    raise ValueError('HTTP 403')
                return 'Generic introduction. Our LiDAR camera measures distance using pulses of laser light.', url
            with patch.object(server, 'history_file_original', str(history)), patch.object(content, 'cache_path', return_value=cache), patch.object(content, 'fetch_public_text', side_effect=download) as fetch:
                indexed = server.index_history_content(days_back='3', max_pages='2')
                self.assertEqual(indexed['indexed'], 1)
                self.assertEqual(indexed['failed'], 1)
                self.assertEqual(indexed['remaining_candidates'], 1)
                result = server.search_history('lider camera', days_back='3')
                self.assertEqual(len(result['pages']), 1)
                page = result['pages'][0]
                self.assertEqual(page['title'], 'Home')
                self.assertIn('cached_page_text', page['match_sources'])
                self.assertIn('LiDAR camera', page['content_excerpt'])
                self.assertEqual(result['content_coverage']['pages_without_cached_text'], 2)
                self.assertIsNotNone(page['content_fetched_at'])
                again = server.index_history_content(max_pages=2)
                self.assertEqual(again['indexed'], 1)
                self.assertEqual(fetch.call_count, 3)
                self.assertEqual(server.index_history_content(refresh='false')['indexed'], 0)
                refreshed = server.index_history_content(max_pages=1, refresh="true")
                self.assertEqual(refreshed['indexed'], 1)
                json.dumps(result)
                self.assertEqual(cache.stat().st_mode & 0o777, 0o600)


    def test_large_content_results_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / 'History'
            cache = Path(directory) / 'content.sqlite'
            stamp = int((datetime.now(timezone.utc).timestamp() + server.CHROME_EPOCH_OFFSET_SECONDS)*1_000_000)
            with closing(sqlite3.connect(history)) as conn, closing(content.open_cache(cache)) as store:
                conn.executescript('CREATE TABLE urls(id INTEGER PRIMARY KEY,title TEXT,url TEXT); CREATE TABLE visits(url INTEGER,visit_time INTEGER);')
                for i in range(30):
                    url = f'https://example.org/{i}'
                    conn.execute('INSERT INTO urls VALUES (?,?,?)', (i, 'Generic page', url))
                    conn.execute('INSERT INTO visits VALUES (?,?)', (i, stamp))
                    content.store_result(store, url, text='Lidar camera ' + 'evidence ' * 100)
                conn.commit()
            with patch.object(server, 'history_file_original', str(history)), patch.object(content, 'cache_path', return_value=cache):
                result = server.search_history('lidar', limit=200)
                self.assertTrue(result['truncated'])
                self.assertEqual(result['total_matching_pages'], 30)
                self.assertLessEqual(sum(len(json.dumps(p)) for p in result['pages']), 12000)


    def test_boolean_strings_and_schema_validation(self):
        from jsonschema import validate, ValidationError
        for value, expected in [(True, True), (False, False), ('true', True), ('false', False), (' TRUE ', True), ('False', False)]:
            with self.subTest(value=value):
                self.assertIs(server.search_boolean(value, 'refresh'), expected)
                validate(value, server.search_boolean_schema(False))
        for value in ['yes', '0', '', 0, 1, None, [], {}]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    server.index_history_content(refresh=value)
                with self.assertRaises(ValidationError):
                    validate(value, server.search_boolean_schema(False))


    def test_worker_deadline_includes_dns_and_header_waits(self):
        import subprocess
        with patch.object(content.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 5)) as run:
            with self.assertRaisesRegex(TimeoutError, 'deadline'):
                content.fetch_public_text('https://example.org')
            self.assertEqual(run.call_args.kwargs['timeout'], 5)
