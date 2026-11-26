"""Run the legacy source-linked CLI without network access."""
import argparse
import json
from pathlib import Path
import sys
import sqlite3
from .sources import bind
from .runner import run, replay


def read_json(path):
    if path.stat().st_size > 100000:
        raise ValueError("Input exceeds 100 KB")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject(value):
        raise ValueError("Nonfinite JSON number")

    result = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique,
        parse_constant=reject,
    )
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    execute = sub.add_parser("run")
    execute.add_argument("input", type=Path)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--source-db", type=Path)
    execute.add_argument(
        "--backend", choices=("exhaustive", "dpll"), default="exhaustive"
    )
    link = sub.add_parser("bind")
    link.add_argument("input", type=Path)
    link.add_argument("--source-db", required=True, type=Path)
    link.add_argument("--version", required=True)
    link.add_argument("--chunk", required=True)
    link.add_argument("--start", required=True, type=int)
    link.add_argument("--end", required=True, type=int)
    link.add_argument("--expect-text", required=True)
    link.add_argument("--output", required=True, type=Path)
    verify = sub.add_parser("replay")
    verify.add_argument("input", type=Path)
    verify.add_argument("--source-db", type=Path)
    upgrade = sub.add_parser("upgrade")
    upgrade.add_argument("input", type=Path)
    upgrade.add_argument("--id", required=True)
    upgrade.add_argument("--output", required=True, type=Path)
    revise = sub.add_parser("revise")
    revise.add_argument("input", type=Path)
    revise.add_argument("--previous", required=True, type=Path)
    revise.add_argument("--output", required=True, type=Path)
    review = sub.add_parser("review")
    review.add_argument("input", type=Path)
    review.add_argument("--reading", required=True)
    review.add_argument(
        "--aspect", choices=("fidelity", "attribution", "interpretation"), required=True
    )
    review.add_argument(
        "--decision", choices=("self_reviewed", "disputed"), required=True
    )
    review.add_argument("--reviewer", required=True)
    review.add_argument("--rationale", required=True)
    review.add_argument("--output", required=True, type=Path)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("input", type=Path)
    analyze.add_argument("--source-db", type=Path)
    analyze.add_argument("--output", required=True, type=Path)
    analyze.add_argument(
        "--backend", choices=("exhaustive", "dpll"), default="exhaustive"
    )
    save = sub.add_parser("save")
    save.add_argument("input", type=Path)
    save.add_argument("--history-db", required=True, type=Path)
    save.add_argument("--expected-head")
    load = sub.add_parser("load")
    load.add_argument("--history-db", required=True, type=Path)
    load.add_argument("--snapshot", required=True)
    load.add_argument("--output", required=True, type=Path)
    annotate = sub.add_parser("annotate")
    annotate.add_argument("input", type=Path)
    annotate.add_argument("--output", required=True, type=Path)
    diff = sub.add_parser("diff")
    diff.add_argument("input", type=Path)
    diff.add_argument("--previous", required=True, type=Path)
    report = sub.add_parser("report")
    report.add_argument("input", type=Path)
    report.add_argument("--source-db", type=Path)
    report.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        from . import reconstruction, history

        value = read_json(args.input) if args.command != "load" else None
        if args.command == "report":
            from .report import render

            html = render(value, args.source_db)
            if len(html.encode("utf-8")) > 1_000_000:
                raise ValueError("Review report exceeds 1 MB")
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(html)
            print(json.dumps({"status": "report_created", "output": str(args.output)}))
            return 0
        if args.command == "annotate":
            from .semantic_ir import annotate

            result = annotate(value)
        elif args.command == "diff":
            from .semantic_ir import changes

            result = changes(read_json(args.previous), value)
        elif args.command == "upgrade":
            result = reconstruction.upgrade(value, args.id)
        elif args.command == "revise":
            result = reconstruction.revise(read_json(args.previous), value)
        elif args.command == "review":
            result = reconstruction.review(
                value,
                args.reading,
                args.aspect,
                args.decision,
                args.reviewer,
                args.rationale,
            )
        elif args.command == "analyze":
            result = reconstruction.execute(value, args.source_db, backend=args.backend)
        elif args.command == "save":
            result = history.save(value, args.history_db, args.expected_head)
        elif args.command == "load":
            result = history.load(args.history_db, args.snapshot)
        elif args.command == "bind":
            result = bind(
                value,
                args.source_db,
                args.version,
                args.chunk,
                args.start,
                args.end,
                args.expect_text,
            )
        elif args.command == "run":
            result = run(value, args.source_db, backend=args.backend)
        else:
            if value.get("schema_version") in (
                "reconstruction-bundle-v1",
                "reconstruction-bundle-v2",
                "reconstruction-bundle-v3",
            ):
                result = reconstruction.replay_reconstruction(value, args.source_db)
            else:
                result = replay(value, args.source_db)
        if args.command in (
            "run",
            "bind",
            "upgrade",
            "review",
            "analyze",
            "load",
            "revise",
            "annotate",
        ):
            encoded = (
                json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            )
            if len(encoded.encode("utf-8")) > 100000:
                raise ValueError("Output exceeds the replay input limit")
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        sqlite3.Error,
    ) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
