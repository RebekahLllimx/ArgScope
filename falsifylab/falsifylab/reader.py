"""Remeasure completed trajectories without NumPy, SciPy or model access."""
import argparse
import json
from pathlib import Path
import sys

from .records import read_measurement
from .result_store import open_store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", choices=("file", "sqlite", "kv"), required=True)
    parser.add_argument("--store-path", type=Path)
    parser.add_argument("--kv-port", type=int, default=6379)
    parser.add_argument("--namespace", default="research")
    parser.add_argument("--control", required=True)
    parser.add_argument("--treated", required=True)
    parser.add_argument("--window", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        store = open_store(
            args.store,
            path=args.store_path,
            port=args.kv_port,
            namespace=args.namespace,
        )
        print(
            json.dumps(
                read_measurement(store, args.control, args.treated, args.window),
                allow_nan=False,
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
