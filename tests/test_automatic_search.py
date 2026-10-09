import unittest
from unittest.mock import patch

import anyio
import mcp.types as types
from click.testing import CliRunner

from chrome_history_mcp import server


def search_result(with_match=False, covered=False):
    return {
        'query': 'lidar', 'days_back': 3,
        'content_coverage': {'pages_in_window': 1, 'pages_with_cached_text': int(covered), 'pages_without_cached_text': int(not covered)},
        'total_matching_pages': int(with_match), 'truncated': False,
        'pages': [{
            'title': 'Home', 'url': 'https://example.org/article',
            'last_visited_at': '2026-10-08T20:00:00+00:00', 'visits_in_window': 1,
            'match_sources': ['cached_page_text'],
            'match_reasons': [{'match_type': 'exact'}],
            'content_excerpt': 'Our LiDAR camera captures terrain.',
            'content_fetched_at': '2026-10-09T02:00:00+00:00',
        }] if with_match else [],
    }


class AutomaticSearchTests(unittest.TestCase):
    def test_search_returns_existing_evidence_and_queues_indexing(self):
        worker = unittest.mock.Mock()
        worker.request.return_value = {'state': 'queued', 'indexed': 0, 'failed': 0}
        with patch.object(server, 'search_history', return_value=search_result(True)) as search, patch.object(server, 'get_background_indexer', return_value=worker):
            result = server.answer_history('lidar', days_back='3')
            worker.request.assert_called_once_with(3)
            search.assert_called_once()
            self.assertIn('Found 1 matching pages', result['summary'])
            self.assertIn('background', result['notice'])
            self.assertIn('LiDAR camera', result['pages'][0]['excerpt'])
            self.assertEqual(set(result), {'summary', 'pages', 'notice'})

    def test_failed_background_job_does_not_discard_search_matches(self):
        worker = unittest.mock.Mock()
        worker.request.return_value = {'state': 'failed', 'error': 'Network unavailable'}
        with patch.object(server, 'search_history', return_value=search_result(True)), patch.object(server, 'get_background_indexer', return_value=worker):
            result = server.answer_history('lidar')
            self.assertEqual(len(result['pages']), 1)
            self.assertIn('incomplete', result['notice'])
            self.assertNotIn('Network unavailable', str(result))

    def test_full_cache_skips_network_and_empty_results_qualify_coverage(self):
        with patch.object(server, 'search_history', return_value=search_result(False, True)), patch.object(server, 'index_history_content') as index:
            result = server.answer_history('lidar')
            index.assert_not_called()
            self.assertIn('No matching pages', result['summary'])
            self.assertEqual(result['pages'], [])

    def test_invalid_arguments_do_not_index(self):
        with patch.object(server, 'index_history_content') as index:
            with self.assertRaises(ValueError):
                server.answer_history('', days_back=3)
            index.assert_not_called()

    def test_default_discovery_exposes_only_simple_search(self):
        for advanced in (False, True):
            created = []
            original = server.Server
            def capture(*args, **kwargs):
                app = original(*args, **kwargs)
                created.append(app)
                return app
            with patch.object(server, 'Server', side_effect=capture), patch.object(server.anyio, 'run'):
                args = ['--browser', 'brave'] + (['--advanced-tools'] if advanced else [])
                result = CliRunner().invoke(server.main, args)
                self.assertEqual(result.exit_code, 0, result.exception)
            async def inspect():
                result = await created[0].request_handlers[types.ListToolsRequest](types.ListToolsRequest(method='tools/list'))
                return result.root.tools
            tools = anyio.run(inspect)
            names = [t.name for t in tools]
            if advanced:
                self.assertIn('index_history_content', names)
                self.assertIn('fetch-urls-from-sqlite', names)
            else:
                self.assertEqual(set(names), {'search_history', 'indexing_status'})
                self.assertEqual(set(next(t for t in tools if t.name == 'search_history').inputSchema['properties']), {'query', 'days_back', 'limit'})


    def test_normal_response_omits_diagnostics_and_preserves_excerpts(self):
        worker = unittest.mock.Mock()
        worker.request.return_value = {'state': 'running', 'indexed': 7, 'failed': 13, 'updated_at': 'timestamp'}
        with patch.object(server, 'search_history', return_value=search_result(True)), patch.object(server, 'get_background_indexer', return_value=worker):
            result = server.answer_history('lidar')
        self.assertEqual(set(result), {'summary', 'pages', 'notice'})
        self.assertEqual(set(result['pages'][0]), {'title', 'url', 'last_visited_at', 'excerpt'})
        self.assertIn('LiDAR', result['pages'][0]['excerpt'])
        for field in ['content_coverage', 'indexing', 'indexing_error', 'truncated', 'matched_in', 'updated_at']:
            self.assertNotIn(field, result)
