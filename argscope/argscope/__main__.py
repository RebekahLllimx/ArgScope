"""Read a JSON argument document and emit a checked, readable report."""

import argparse
import html
import json
from pathlib import Path
import sys
from .graph import analyze_document, affected_steps, DocumentError
from .logic import FormulaError


def render_html(report: dict) -> str:
    rows = []
    for step in report["steps"]:
        detail = {
            key: value
            for key, value in step.items()
            if key not in {"id", "formula", "status"}
        }
        rows.append(
            "<tr><td>"
            + html.escape(step["id"])
            + "</td><td><code>"
            + html.escape(step["formula"])
            + "</code></td><td>"
            + html.escape(step["status"])
            + "</td><td><pre>"
            + html.escape(json.dumps(detail, indent=2))
            + "</pre></td></tr>"
        )
    return (
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>ArgScope report</title><style>body{font:16px system-ui;margin:40px auto;max-width:1100px;padding:0 20px;color:#17202a}"
        "table{border-collapse:collapse;width:100%}td,th{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #ccd1d1}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}code{overflow-wrap:anywhere}</style><h1>"
        + html.escape(str(report["title"]))
        + "</h1><p>Each step is checked against its declared dependencies. Premises are assumptions; their real-world truth is not evaluated.</p>"
        "<table><thead><tr><th>Step</th><th>Conclusion</th><th>Result</th><th>Explanation</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></html>"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--max-variables", type=int)
    parser.add_argument(
        "--backend", choices=("exhaustive", "dpll"), default="exhaustive"
    )
    parser.add_argument("--changed", help="List steps affected by a changed node")
    parser.add_argument("--html", type=Path, help="Write an HTML report to a new file")
    args = parser.parse_args(argv)
    try:
        if args.document.stat().st_size > 100_000:
            raise DocumentError("Document exceeds 100 KB")
        document = json.loads(args.document.read_text(encoding="utf-8"))
        variable_limit = (
            args.max_variables
            if args.max_variables is not None
            else (64 if args.backend == "dpll" else 12)
        )
        report = analyze_document(
            document, max_variables=variable_limit, backend=args.backend
        )
        if args.changed:
            report["affected_steps"] = affected_steps(document, args.changed)
        if args.html:
            args.html.parent.mkdir(parents=True, exist_ok=True)
            with args.html.open("x", encoding="utf-8") as destination:
                destination.write(render_html(report))
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
