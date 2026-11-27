"""Run an isolated loopback instance of a pinned KV checkout.

Only the selected AOF is written. The server source stays read-only, and Python
bytecode writes are disabled before importing it. The KV runtime owns commit
ordering, persistence failure handling and shutdown drain.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path


async def serve(args):
    root = args.kv_root.resolve()
    args.aof.parent.mkdir(parents=True, exist_ok=True)
    if args.aof.resolve().is_relative_to(root):
        raise ValueError("Place the session AOF outside the KV source checkout")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from aof import AOF
    from main import run_service
    from server import handle_client
    from store import Store

    store = Store()
    failure_event = asyncio.Event()
    failure_reason = {"error": None}

    def on_failure(error):
        if failure_reason["error"] is None:
            failure_reason["error"] = error
        failure_event.set()

    log = AOF(args.aof, sync_policy="always", on_failure=on_failure)
    log.load(store)
    store.attach_aof(log)
    clients = set()

    async def tracked_client(reader, writer):
        task = asyncio.current_task()
        clients.add(task)
        try:
            await handle_client(reader, writer, store)
        finally:
            clients.discard(task)

    try:
        async with await asyncio.start_server(
            tracked_client,
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
                                *(
                                    ["aof_lock.py"]
                                    if (root / "aof_lock.py").is_file()
                                    else []
                                ),
                                "main.py",
                                "commit.py",
                            )
                        },
                    }
                ),
                flush=True,
            )
            await run_service(
                server, store, log, failure_event, failure_reason, clients
            )
    finally:
        await log.close_async()


async def run(args):
    sys.path.insert(0, str(args.kv_root.resolve()))
    from main import run_until_signal

    await run_until_signal(serve(args))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kv-root", required=True, type=Path)
    parser.add_argument("--aof", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.port != 0 and not 1024 <= args.port <= 65535:
        parser.error("Use zero or a port in [1024,65535]")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
