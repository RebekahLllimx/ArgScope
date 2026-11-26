"""Run an isolated loopback instance of an existing, trusted KV checkout.

Only the selected AOF is written. The server source stays read-only, and Python
bytecode writes are disabled before importing it. This is a local research
session launcher, not a copied server or a distributed cache service.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys


async def expiration_loop(store, interval_seconds=1.0):
    """Reclaim expired memory; GET/TTL alone only make old entries invisible.

    This does not compact AOF history or delete durable ArgScope artifacts.
    Cancellation and purge failures propagate to the session supervisor.
    """
    if interval_seconds <= 0:
        raise ValueError("Expiration interval must be positive")
    while True:
        await asyncio.sleep(interval_seconds)
        await store.purge_expired()


async def serve(args):
    root = args.kv_root.resolve()
    args.aof.parent.mkdir(parents=True, exist_ok=True)
    if args.aof.resolve().is_relative_to(root):
        raise ValueError("Place the session AOF outside the KV source checkout")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from aof import AOF
    from server import handle_client
    from store import Store

    store = Store()
    log = AOF(args.aof, sync_policy="always")
    log.load(store)
    store.attach_aof(log)
    tasks = []
    try:
        async with await asyncio.start_server(
            lambda reader, writer: handle_client(reader, writer, store),
            "127.0.0.1",
            args.port,
        ) as server:
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "port": server.sockets[0].getsockname()[1],
                        "aof_policy": "always",
                        "source_sha256": {
                            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                            for name in (
                                "server.py",
                                "store.py",
                                "commands.py",
                                "resp.py",
                                "response.py",
                                "aof.py",
                                *(["aof_lock.py"] if (root / "aof_lock.py").is_file() else []),
                            )
                        },
                    }
                ),
                flush=True,
            )
            tasks = [
                asyncio.create_task(server.serve_forever()),
                asyncio.create_task(expiration_loop(store)),
            ]
            await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await log.close_async()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kv-root", required=True, type=Path)
    parser.add_argument("--aof", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.port != 0 and not 1024 <= args.port <= 65535:
        parser.error("Use zero or a port in [1024,65535]")
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
