"""One bounded, coalescing background indexing worker per MCP process."""
import threading
import time
from datetime import datetime, timezone


class BackgroundIndexer:
    def __init__(self, index_batch, max_batches=40):
        self.index_batch = index_batch
        self.max_batches = max_batches
        self.condition = threading.Condition()
        self.thread = None
        self.stopping = False
        self.pending_days = None
        self.target_days = 0
        self.finished_at = 0
        self.state = {
            'state': 'idle', 'days_back': 0, 'indexed': 0, 'failed': 0,
            'remaining_candidates': None, 'error': None, 'updated_at': None,
        }

    def _update(self, **values):
        self.state.update(values)
        self.state['updated_at'] = datetime.now(timezone.utc).isoformat()

    def request(self, days_back):
        with self.condition:
            if self.stopping:
                return dict(self.state)
            if self.state['state'] in {'queued', 'running'}:
                if days_back > self.target_days:
                    self.target_days = days_back
                    self._update(days_back=days_back)
                return dict(self.state)
            # Repeated questions reuse a recently completed run; new/wider windows
            # or requests after the cooldown can pick up newly visited pages.
            if self.state['state'] == 'completed' and days_back <= self.target_days and time.monotonic() - self.finished_at < 60:
                return dict(self.state)
            self.target_days = days_back
            self.pending_days = days_back
            self._update(state='queued', days_back=days_back, indexed=0, failed=0,
                         remaining_candidates=None, error=None)
            if self.thread is None:
                self.thread = threading.Thread(target=self._work, name='history-content-indexer', daemon=True)
                self.thread.start()
            self.condition.notify_all()
            return dict(self.state)

    def status(self):
        with self.condition:
            return dict(self.state)

    def _work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopping or self.pending_days is not None)
                if self.stopping:
                    return
                self.pending_days = None
                self._update(state='running')
            for batch_number in range(self.max_batches):
                with self.condition:
                    if self.stopping:
                        return
                    days = self.target_days
                try:
                    batch = self.index_batch(days_back=days, max_pages=5)
                except Exception as error:
                    with self.condition:
                        self._update(state='failed', error=f'{type(error).__name__}: {str(error)[:160]}')
                    break
                with self.condition:
                    if self.stopping:
                        return
                    self._update(
                        indexed=self.state['indexed'] + batch['indexed'],
                        failed=self.state['failed'] + batch['failed'],
                        remaining_candidates=batch['remaining_candidates'],
                    )
                    # A wider request arrived while the batch was running.
                    wider = self.target_days > days
                    if not wider and batch['remaining_candidates'] == 0:
                        self.finished_at = time.monotonic()
                        self._update(state='completed')
                        break
                    if batch_number + 1 == self.max_batches:
                        self._update(state='paused', error='Background batch limit reached; another search can resume indexing.')

    def close(self):
        with self.condition:
            self.stopping = True
            self.pending_days = None
            self._update(state='stopped')
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=1)
