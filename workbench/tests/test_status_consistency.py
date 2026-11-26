"""A completed worker must not make an earlier checkpoint look like a stable pause."""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from workbench.review_app import Service


class StatusConsistencyTests(unittest.TestCase):
    def test_worker_finishes_during_snapshot_read(self):
        service = Service.__new__(Service)
        service.lock = threading.Lock()
        service.busy = {'run'}
        service.store = Mock()
        service.store.request.return_value = {}
        service.store.events.return_value = []
        service.store.latest_event.return_value = None
        service.store.has_event.return_value = False

        def snapshot(_):
            # Worker completes after the earlier human checkpoint was read.
            service.busy.clear()
            return SimpleNamespace(values={}, next=('human',), interrupts=(object(),))

        service.flow = SimpleNamespace(snapshot=snapshot, provider=SimpleNamespace(name='fixture', live=False))
        self.assertEqual(service.view('run')['status'], 'running')
