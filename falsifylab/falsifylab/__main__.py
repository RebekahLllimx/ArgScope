"""Run a budgeted falsification experiment against a local model checkout."""

import argparse
import json
from pathlib import Path
import sys

from .experiment import Config, run_experiment
from .model import ModelSpec


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", default=1, type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--trajectory-store", choices=("file", "sqlite", "kv"))
    parser.add_argument("--store-path", type=Path)
    parser.add_argument("--kv-port", type=int, default=6379)
    parser.add_argument("--namespace", default="research")
    args = parser.parse_args(argv)
    try:
        config = Config.from_dict(json.loads(args.config.read_text()))
        store = None
        if args.trajectory_store is not None:
            from .result_store import open_store

            store = open_store(
                args.trajectory_store,
                path=args.store_path,
                port=args.kv_port,
                namespace=args.namespace,
            )
        elif args.store_path is not None:
            raise ValueError("--store-path requires --trajectory-store")
        report = run_experiment(
            config,
            ModelSpec.from_root(args.model_root),
            args.output,
            workers=args.workers,
            resume=args.resume,
            trajectory_store=store,
        )
        print(
            json.dumps(
                {
                    "computed_pairs": report["computed_pairs"],
                    "cached_pairs": report["cached_pairs"],
                    **(
                        {"trajectory_store": report["trajectory_store"]}
                        if store is not None
                        else {}
                    ),
                    "methods": [
                        {
                            "method": row["method"],
                            "selected": row["selected"],
                            "confirmation": row["confirmation"],
                        }
                        for row in report["methods"]
                    ],
                },
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
