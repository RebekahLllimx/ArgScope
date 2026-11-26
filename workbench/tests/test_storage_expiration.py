"""Managed-session memory reclamation without deleting persistence/history."""
import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

launcher = module("managed_kv_launcher", ROOT / "falsifylab/tools/serve_research_kv.py")
Store = module("pinned_kv_store", ROOT / "vendor/kv_server/store.py").Store


class ExpirationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unread_expired_entries_reclaimed_but_live_entries_preserved(self):
        clock = [100.0]
        store = Store(clock=lambda: clock[0], wall_clock=lambda: clock[0])
        class Log:
            def __init__(self): self.records = []
            async def append_async(self, command): self.records.append(command)
        log = Log(); store.attach_aof(log)
        await store.set(b"expired", b"old", seconds=1)
        await store.set(b"still-live", b"live", seconds=20)
        await store.set(b"permanent", b"keep")
        records = list(log.records)
        clock[0] += 2
        self.assertIsNone(store.get(b"expired"))
        self.assertIn(b"expired", store._data)  # logical expiry alone is insufficient
        task = asyncio.create_task(launcher.expiration_loop(store, .001))
        try:
            for _ in range(100):
                if b"expired" not in store._data: break
                await asyncio.sleep(.002)
            self.assertNotIn(b"expired", store._data)
            self.assertNotIn(b"expired", store._expires_at)
            self.assertEqual(store.get(b"still-live"), b"live")
            self.assertEqual(store.get(b"permanent"), b"keep")
            self.assertEqual(log.records, records)  # no DEL log or AOF rewrite
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task

    async def test_purge_failure_is_visible_to_supervisor(self):
        class Broken:
            async def purge_expired(self): raise RuntimeError("purge failed")
        with self.assertRaisesRegex(RuntimeError, "purge failed"):
            await launcher.expiration_loop(Broken(), .001)

    async def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            await launcher.expiration_loop(Store(), 0)

    def test_real_session_supervises_and_stops_expiration_task(self):
        # Actual process/RESP path, unlike the injected-clock unit cases above.
        sys.path.insert(0, str(ROOT / "falsifylab/tools"))
        from evaluate_kv import SessionServer
        import socket
        session = None
        with tempfile.TemporaryDirectory() as folder:
            session = SessionServer(ROOT / "vendor/kv_server", Path(folder) / "isolated.aof")
            try:
                session.start()
                with socket.create_connection(("127.0.0.1", session.port), timeout=2) as sock:
                    sock.sendall(b"*1\r\n$4\r\nPING\r\n")
                    self.assertEqual(sock.recv(128), b"+PONG\r\n")
                process = session.process
            finally:
                session.stop()
            self.assertIsNotNone(process.poll())
            self.assertNotIn("Task was destroyed", (Path(folder) / "isolated.stderr").read_text())


if __name__ == "__main__": unittest.main()
