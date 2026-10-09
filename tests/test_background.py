import threading
import time
import unittest

from chrome_history_mcp.background import BackgroundIndexer


class BackgroundIndexingTests(unittest.TestCase):
    def wait_for_state(self, worker, expected):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if worker.status()['state'] == expected:
                return worker.status()
            time.sleep(.005)
        self.fail(f'Expected {expected}, got {worker.status()}')

    def test_request_is_nonblocking_and_duplicate_searches_share_worker(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def batch(**kwargs):
            calls.append(kwargs)
            started.set()
            release.wait(2)
            return {'indexed': 2, 'failed': 1, 'remaining_candidates': 0}
        worker = BackgroundIndexer(batch)
        self.addCleanup(worker.close)
        self.addCleanup(release.set)
        start = time.monotonic()
        worker.request(3)
        self.assertLess(time.monotonic() - start, .25)
        self.assertTrue(started.wait(1))
        for _ in range(5):
            self.assertEqual(worker.request(3)['state'], 'running')
        self.assertEqual(len(calls), 1)
        release.set()
        state = self.wait_for_state(worker, 'completed')
        self.assertEqual(state['indexed'], 2)
        self.assertEqual(state['failed'], 1)
        worker.request(3)
        self.assertEqual(len(calls), 1)

    def test_batches_continue_without_followup_requests(self):
        calls = []
        def batch(**kwargs):
            calls.append(kwargs)
            return {'indexed': 5 if len(calls)==1 else 2, 'failed': 0, 'remaining_candidates': 2 if len(calls)==1 else 0}
        worker = BackgroundIndexer(batch)
        self.addCleanup(worker.close)
        worker.request(3)
        state = self.wait_for_state(worker, 'completed')
        self.assertEqual(state['indexed'], 7)
        self.assertEqual(len(calls), 2)

    def test_wider_window_is_coalesced(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def batch(**kwargs):
            calls.append(kwargs['days_back'])
            if len(calls)==1:
                started.set()
                release.wait(2)
            return {'indexed': 1, 'failed': 0, 'remaining_candidates': 0}
        worker = BackgroundIndexer(batch)
        self.addCleanup(worker.close)
        self.addCleanup(release.set)
        worker.request(3)
        self.assertTrue(started.wait(1))
        worker.request(7)
        release.set()
        self.wait_for_state(worker, 'completed')
        self.assertEqual(calls, [3, 7])

    def test_failures_and_batch_limit_are_visible(self):
        def fail(**kwargs):
            raise OSError('Offline')
        worker = BackgroundIndexer(fail)
        self.addCleanup(worker.close)
        worker.request(3)
        self.assertIn('Offline', self.wait_for_state(worker, 'failed')['error'])
        limited = BackgroundIndexer(lambda **kw: {'indexed': 5, 'failed': 0, 'remaining_candidates': 10}, max_batches=1)
        self.addCleanup(limited.close)
        limited.request(3)
        state = self.wait_for_state(limited, 'paused')
        self.assertEqual(state['remaining_candidates'], 10)

    def test_shutdown_stops_new_batches(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def batch(**kwargs):
            calls.append(kwargs)
            started.set()
            release.wait(2)
            return {'indexed': 5, 'failed': 0, 'remaining_candidates': 20}
        worker = BackgroundIndexer(batch)
        worker.request(3)
        self.assertTrue(started.wait(1))
        release.set()
        worker.close()
        self.assertEqual(worker.status()['state'], 'stopped')
        worker.request(7)
        self.assertEqual(worker.status()['state'], 'stopped')
