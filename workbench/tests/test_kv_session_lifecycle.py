"""Real managed-session restart and persistence-failure supervision."""

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from falsifylab.result_store import KVStore, StoreError, canonical, fingerprint

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "managed_kv_session", ROOT / "falsifylab/tools/evaluate_kv.py"
)
sessions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sessions)


class ManagedKVTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.kv_root = Path(
            os.environ.get("REVIEW_TEST_KV_ROOT", ROOT / "vendor/kv_server")
        )

    def session(self, root=None):
        session = sessions.SessionServer(
            root or self.kv_root, self.folder / "session.aof"
        )
        self.addCleanup(session.stop)
        return session

    def test_pinned_commit_source_and_restart_keep_confirmed_record(self):
        session = self.session()
        before = sessions.snapshot_source(self.kv_root)
        ready = session.start()
        self.assertIn("commit.py", ready["source_sha256"])
        client = KVStore(session.port, "lifecycle-test")
        key, value = fingerprint({"test": "restart"}), canonical({"value": 1})
        client.put(key, value)
        process = session.process
        session.stop()
        self.assertEqual(process.returncode, 0)
        session.start()
        self.assertEqual(KVStore(session.port, "lifecycle-test").get(key), value)
        self.assertEqual(sessions.snapshot_source(self.kv_root), before)

    def test_fsync_failure_stops_managed_service_without_success_response(self):
        damaged = self.folder / "fault-injected-runtime"
        damaged.mkdir()
        for source in self.kv_root.glob("*.py"):
            shutil.copy2(source, damaged / source.name)
        aof = damaged / "aof.py"
        original = aof.read_text()
        needle = "os.fsync(self._file.fileno())"
        self.assertEqual(original.count(needle), 1)
        aof.write_text(
            original.replace(needle, 'raise OSError("injected fsync failure")')
        )
        session = self.session(damaged)
        session.start()
        client = KVStore(session.port, "failure-test")
        with self.assertRaises(StoreError):
            client.put(fingerprint({"test": "fault"}), canonical({"value": 1}))
        self.assertNotEqual(session.process.wait(timeout=5), 0)
        session.stop()
        error = (self.folder / "session.stderr").read_text()
        self.assertIn("AOF failed; stopping service", error)
        self.assertNotIn("Task was destroyed", error)
        self.assertNotIn("Future exception was never retrieved", error)

    def test_stop_timeout_warns_and_waits_instead_of_killing_owner(self):
        import subprocess

        session = sessions.SessionServer(self.kv_root, self.folder / "unused.aof")
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("kv", 5), 0]
        session.process, session.stderr = process, Mock()
        with patch.object(
            sessions.logging.getLogger(sessions.__name__), "warning"
        ) as warning:
            session.stop()
        warning.assert_called_once()
        self.assertEqual(process.wait.call_args_list[0].kwargs, {"timeout": 5})
        self.assertEqual(process.wait.call_args_list[1].kwargs, {})
        process.kill.assert_not_called()
        self.assertIsNone(session.process)
