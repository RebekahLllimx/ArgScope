"""Start a single local SourceLens process."""

import argparse
from pathlib import Path

import uvicorn

from .api import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, default=Path("var/sourcelens.sqlite3"))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use a port in [1024,65535]")
    uvicorn.run(create_app(args.database), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
