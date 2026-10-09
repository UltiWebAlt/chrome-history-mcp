import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from chrome_history_mcp import server


class SearchHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = Path(self.temp.name) / 'History'
        cache_patch = patch.object(server.content, 'cache_path', return_value=Path(self.temp.name) / 'content.sqlite')
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        self.now = datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.history)) as conn:
            conn.executescript('CREATE TABLE urls(id INTEGER PRIMARY KEY, title TEXT, url TEXT); CREATE TABLE visits(url INTEGER, visit_time INTEGER);')
            conn.executemany('INSERT INTO urls VALUES (?, ?, ?)', [
                (1, 'LIDER research', 'https://example.org/article'),
                (2, 'Other topic', 'https://example.org/lider'),
                (3, 'lider old', 'https://example.org/old'),
                (4, 'Unrelated', 'https://example.org/other'),
                (5, "100% team's guide", 'https://example.org/literal'),
                (6, 'lider future', 'https://example.org/future'),
            ])
            for url, days in [(1, 1), (1, 2), (2, .5), (3, 4), (4, 1), (5, 1), (6, -1)]:
                stamp = int(((self.now - timedelta(days=days)).timestamp() + server.CHROME_EPOCH_OFFSET_SECONDS) * 1_000_000)
                conn.execute('INSERT INTO visits VALUES (?, ?)', (url, stamp))
            conn.commit()
        self.patch = patch.object(server, 'history_file_original', str(self.history))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_date_filter_join_deduplication_and_timestamps(self):
        result = server.search_history('LiDeR', 3)
        self.assertEqual([p['url'] for p in result['pages']], ['https://example.org/lider', 'https://example.org/article'])
        self.assertEqual(result['pages'][1]['visits_in_window'], 2)
        visited = datetime.fromisoformat(result['pages'][1]['last_visited_at'])
        self.assertLess(abs((visited - (self.now - timedelta(days=1))).total_seconds()), .001)
        self.assertFalse(result['truncated'])
        json.dumps(result)

    def test_limit_empty_results_and_literal_search(self):
        result = server.search_history('lider', 3, 1)
        self.assertEqual(len(result['pages']), 1)
        self.assertTrue(result['truncated'])
        self.assertEqual(server.search_history('missing')['pages'], [])
        self.assertEqual(len(server.search_history("100% team's")['pages']), 1)
        self.assertEqual(server.search_history("' OR 1=1 --")['pages'], [])

    def test_invalid_arguments(self):
        for kwargs in [{'query': ''}, {'query': ' '}, {'query': None}, {'query': 'x', 'days_back': 0}, {'query': 'x', 'days_back': True}, {'query': 'x', 'limit': 201}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                server.search_history(**kwargs)

    def test_live_wal_history_and_source_unchanged(self):
        with closing(sqlite3.connect(self.history)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("UPDATE urls SET title='fresh keyword' WHERE id=1")
            writer.commit()
            result = server.search_history('fresh keyword')
            self.assertEqual(len(result['pages']), 1)
            self.assertEqual(writer.execute('SELECT COUNT(*) FROM visits').fetchone()[0], 7)

    def add_page(self, page_id, title, url, days=1):
        stamp = int(((self.now - timedelta(days=days)).timestamp() + server.CHROME_EPOCH_OFFSET_SECONDS) * 1_000_000)
        with closing(sqlite3.connect(self.history)) as conn:
            conn.execute('INSERT INTO urls VALUES (?, ?, ?)', (page_id, title, url))
            conn.execute('INSERT INTO visits VALUES (?, ?)', (page_id, stamp))
            conn.commit()

    def test_typo_synonyms_and_exact_ranking(self):
        self.add_page(10, 'LiDAR camera guide', 'https://example.org/sensor', .1)
        self.add_page(11, 'Light detection and ranging', 'https://example.org/ranging', .2)
        result = server.search_history('lider')
        self.assertEqual(result['pages'][0]['url'], 'https://example.org/lider')
        approximate = [p for p in result['pages'] if p['url'] == 'https://example.org/sensor'][0]
        self.assertEqual(approximate['match_reasons'][0]['matched_term'], 'lidar')
        self.assertEqual(approximate['match_reasons'][0]['match_type'], 'fuzzy')
        self.assertIn('https://example.org/ranging', [p['url'] for p in result['pages']])
        strict = server.search_history('lider', fuzzy=False)
        self.assertNotIn('https://example.org/sensor', [p['url'] for p in strict['pages']])
        synonyms = server.search_history('lidar')
        expanded = [p for p in synonyms['pages'] if p['url'].endswith('/ranging')][0]
        self.assertEqual(expanded['match_reasons'][0]['match_type'], 'synonym')

    def test_multiword_query_requires_each_topic_and_handles_word_order(self):
        self.add_page(10, 'Camera uses LiDAR technology', 'https://example.org/sensor')
        self.add_page(11, 'LiDAR introduction', 'https://example.org/ranging')
        result = server.search_history('information about lider camera')
        self.assertEqual([p['url'] for p in result['pages']], ['https://example.org/sensor'])
        self.assertEqual(len(result['pages'][0]['match_reasons']), 2)

    def test_unicode_encoded_urls_short_words_and_transposition(self):
        self.add_page(10, 'CAFÉ résumé', 'https://example.org/unicode')
        self.add_page(11, 'A topic', 'https://example.org/lidar%20camera')
        self.add_page(12, 'Artificial intelligence tools', 'https://example.org/tools')
        self.assertEqual(len(server.search_history('cafe resume')['pages']), 1)
        self.assertEqual(len(server.search_history('lidar camera')['pages']), 1)
        self.assertEqual(len(server.search_history('liadr')['pages']), 1)
        self.assertEqual(len(server.search_history('ai')['pages']), 1)
        self.assertFalse(server.one_edit_apart('ml', 'ma'))
        self.assertFalse(server.one_edit_apart('lidar', 'radar'))
        self.assertEqual(server.search_history('!!')['pages'], [])
        with self.assertRaises(ValueError):
            server.search_history('lidar', fuzzy='yes')

    def test_synonym_phrase_query_and_date_window(self):
        self.add_page(10, 'LiDAR overview', 'https://example.org/sensor')
        self.add_page(11, 'Laser radar camera', 'https://example.org/old-camera', 10)
        result = server.search_history('light detection and ranging', fuzzy=False)
        self.assertEqual([p['url'] for p in result['pages']], ['https://example.org/sensor'])


    def test_numeric_strings_from_model(self):
        result = server.search_history('lider', days_back='3', limit='1')
        self.assertEqual(result['days_back'], 3)
        self.assertEqual(len(result['pages']), 1)
        self.assertTrue(result['truncated'])
        from jsonschema import validate
        validate('3', server.search_integer_schema(3650, 3))
        validate(3, server.search_integer_schema(3650, 3))
        for invalid in ['3.5', 'three', '-1', '0', '3651', '', '9' * 100, 3.5, True, None]:
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                server.search_history('lider', days_back=invalid)


    def test_fuzzy_boolean_strings(self):
        result = server.search_history('lider', fuzzy='false')
        self.assertIs(result['fuzzy'], False)
        self.assertEqual(len(result['pages']), 2)
        self.assertIs(server.search_history('lider', fuzzy='true')['fuzzy'], True)


    def test_locked_history_uses_private_recoverable_copy(self):
        with closing(sqlite3.connect(self.history)) as writer:
            writer.execute('BEGIN EXCLUSIVE')
            writer.execute("UPDATE urls SET title='uncommitted secret' WHERE id=1")
            result = server.search_history('lider')
            self.assertEqual(len(result['pages']), 2)
            self.assertNotIn('uncommitted secret', json.dumps(result))
            writer.rollback()
        with closing(sqlite3.connect(self.history)) as conn:
            self.assertEqual(conn.execute('SELECT title FROM urls WHERE id=1').fetchone()[0], 'LIDER research')


class NaturalQuestionTests(unittest.TestCase):
    def test_employment_question_keeps_topic_and_action(self):
        query = 'search my web history for paramedic fired from his job'
        terms = server.search_terms(query, True)
        self.assertEqual([term for term, _ in terms], ['paramedic', 'fired'])
        self.assertIsNotNone(server.match_history_page(
            'Anderson County EMS: Former paramedic fired for falsified reports before assault charge',
            'https://example.com/news', query, terms, True))
        self.assertIsNone(server.match_history_page(
            'Paramedic wins award', 'https://example.com/award', query, terms, True))
