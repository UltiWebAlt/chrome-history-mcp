import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import anyio
from chrome_history_mcp import server


class SearchWaitTests(unittest.TestCase):
    def answer(self, found=False):
        return {'summary': 'Found' if found else 'No matches', 'pages': [{'title': 'LiDAR'}] if found else [], 'notice': ''}

    def test_new_matches_return_in_original_request_with_progress(self):
        context = SimpleNamespace(meta=SimpleNamespace(progressToken='test'), session=SimpleNamespace(send_progress_notification=AsyncMock()))
        with patch.object(server, 'answer_history', side_effect=[self.answer(), self.answer(), self.answer(True)]), patch.object(server, 'indexing_status', side_effect=[{'state': 'running', 'updated_at': '1'}, {'state': 'running', 'updated_at': '2'}]):
            result = anyio.run(lambda: server.wait_for_history_answer({'query': 'lidar'}, context, wait_seconds=1, poll_seconds=.001))
        self.assertTrue(result['pages'])
        context.session.send_progress_notification.assert_awaited_once()

    def test_existing_matches_return_immediately(self):
        with patch.object(server, 'answer_history', return_value=self.answer(True)), patch.object(server, 'indexing_status') as status:
            result = anyio.run(lambda: server.wait_for_history_answer({'query': 'lidar'}))
        self.assertTrue(result['pages'])
        status.assert_not_called()

    def test_deadline_does_not_claim_finished_search(self):
        with patch.object(server, 'answer_history', return_value=self.answer()), patch.object(server, 'indexing_status', return_value={'state': 'running'}):
            result = anyio.run(lambda: server.wait_for_history_answer({'query': 'lidar'}, wait_seconds=0))
        self.assertIn('still in progress', result['summary'])

    def test_completed_empty_search_stops_waiting(self):
        with patch.object(server, 'answer_history', return_value=self.answer()) as search, patch.object(server, 'indexing_status', return_value={'state': 'completed', 'updated_at': '1'}):
            result = anyio.run(lambda: server.wait_for_history_answer({'query': 'lidar'}, wait_seconds=1))
        self.assertFalse(result['pages'])
        self.assertEqual(search.call_count, 3)

    def test_paused_batches_resume_without_user_question(self):
        worker = Mock()
        with patch.object(server, 'answer_history', side_effect=[self.answer(), self.answer(), self.answer(True)]), patch.object(server, 'get_background_indexer', return_value=worker), patch.object(server, 'indexing_status', side_effect=[{'state': 'paused', 'updated_at': '1'}, {'state': 'running', 'updated_at': '2'}]):
            result = anyio.run(lambda: server.wait_for_history_answer({'query': 'lidar', 'days_back': '3'}, wait_seconds=1, poll_seconds=.001))
        self.assertTrue(result['pages'])
        worker.request.assert_called_once_with(3)
