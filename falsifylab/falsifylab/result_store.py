"""Replaceable stores for disposable, completed trajectory records.

RESP uses the existing KV server over loopback TCP. No server modules are
imported here, and no locks, leases, TTL guarantees or miss coalescing are assumed.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import tempfile

MAX_RECORD_BYTES = 512 * 1024


class StoreError(OSError):
    """A disposable store is unavailable; computation may continue uncached."""


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def check_key(key):
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Expected a SHA-256 trajectory key")
    return key


def check_value(data):
    if not isinstance(data, bytes) or len(data) > MAX_RECORD_BYTES:
        raise StoreError("Trajectory exceeds the 512 KiB store limit")
    return data


class FileStore:
    backend = "file"

    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def get(self, key):
        path = self.path / (check_key(key) + ".json")
        try:
            with path.open("rb") as stream:
                return check_value(stream.read(MAX_RECORD_BYTES + 1))
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StoreError("File trajectory store is unavailable") from error

    def put(self, key, data):
        path = self.path / (check_key(key) + ".json")
        check_value(data)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as error:
            raise StoreError("File trajectory write failed") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def delete(self, key):
        (self.path / (check_key(key) + ".json")).unlink(missing_ok=True)


class SQLiteStore:
    backend = "sqlite"

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if tables and tables != {"trajectory_records", "trajectory_meta"}:
                raise ValueError("Use a separate trajectory cache database")
            if not tables:
                with db:
                    db.execute("CREATE TABLE trajectory_meta(version INTEGER NOT NULL)")
                    db.execute("INSERT INTO trajectory_meta VALUES(1)")
                    db.execute(
                        "CREATE TABLE trajectory_records(key TEXT PRIMARY KEY, payload BLOB NOT NULL)"
                    )
            if db.execute("SELECT version FROM trajectory_meta").fetchall() != [(1,)]:
                raise ValueError("Unsupported trajectory cache schema")

    def connect(self):
        try:
            db = sqlite3.connect(self.path, timeout=2)
            db.execute("PRAGMA synchronous=FULL")
            return db
        except sqlite3.Error as error:
            raise StoreError("SQLite trajectory store is unavailable") from error

    def get(self, key):
        check_key(key)
        try:
            with closing(self.connect()) as db:
                row = db.execute(
                    "SELECT payload FROM trajectory_records WHERE key=?", (key,)
                ).fetchone()
                return check_value(row[0]) if row is not None else None
        except sqlite3.Error as error:
            raise StoreError("SQLite trajectory read failed") from error

    def put(self, key, data):
        check_key(key)
        check_value(data)
        try:
            with closing(self.connect()) as db, db:
                db.execute(
                    "INSERT OR REPLACE INTO trajectory_records VALUES(?,?)", (key, data)
                )
        except sqlite3.Error as error:
            raise StoreError("SQLite trajectory write failed") from error

    def delete(self, key):
        check_key(key)
        try:
            with closing(self.connect()) as db, db:
                db.execute("DELETE FROM trajectory_records WHERE key=?", (key,))
        except sqlite3.Error as error:
            raise StoreError("SQLite trajectory delete failed") from error


class KVStore:
    backend = "kv"

    def __init__(self, port=6379, namespace="research", *, timeout=0.5):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Use a loopback KV port in [1024,65535]")
        if not isinstance(namespace, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", namespace
        ):
            raise ValueError("Use a 1-64 character trajectory namespace")
        self.port, self.namespace, self.timeout = port, namespace, timeout

    def wire_key(self, key):
        return (
            "falsifylab:trajectory-v1:" + self.namespace + ":" + check_key(key)
        ).encode()

    def command(self, *parts):
        request = b"*" + str(len(parts)).encode() + b"\r\n"
        for part in parts:
            request += b"$" + str(len(part)).encode() + b"\r\n" + part + b"\r\n"
        try:
            with socket.create_connection(
                ("127.0.0.1", self.port), timeout=self.timeout
            ) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(request)
                with sock.makefile("rb") as stream:
                    line = stream.readline(256)
                    if not line.endswith(b"\r\n"):
                        raise StoreError("Incomplete RESP response")
                    if parts[0] == b"GET" and not line.startswith(b"$"):
                        raise StoreError("GET requires a bulk RESP response")
                    if line in (b"+OK\r\n", b"+PONG\r\n"):
                        return line[1:-2]
                    if line.startswith(b"-"):
                        raise StoreError("KV rejected the command")
                    if line.startswith(b":"):
                        return int(line[1:-2])
                    if not line.startswith(b"$"):
                        raise StoreError("Unexpected RESP response")
                    size = int(line[1:-2])
                    if size == -1:
                        return None
                    if not 0 <= size <= MAX_RECORD_BYTES:
                        raise StoreError("KV response exceeds the record limit")
                    data = stream.read(size + 2)
                    if len(data) != size + 2 or not data.endswith(b"\r\n"):
                        raise StoreError("Truncated RESP bulk value")
                    return data[:-2]
        except (OSError, ValueError) as error:
            raise StoreError("Loopback KV request failed") from error

    def get(self, key):
        data = self.command(b"GET", self.wire_key(key))
        if data is not None:
            check_value(data)
        return data

    def put(self, key, data):
        if self.command(b"SET", self.wire_key(key), check_value(data)) != b"OK":
            raise StoreError("KV did not acknowledge the completed record")

    def delete(self, key):
        self.command(b"DEL", self.wire_key(key))


def open_store(backend, *, path=None, port=6379, namespace="research"):
    if backend == "kv":
        return KVStore(port, namespace)
    if backend not in ("file", "sqlite") or path is None:
        raise ValueError("Choose file/sqlite with a path, or kv with a loopback port")
    return FileStore(path) if backend == "file" else SQLiteStore(path)
