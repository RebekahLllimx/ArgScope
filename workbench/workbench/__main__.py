"""Compatibility entry point for the legacy offline workbench CLI."""
from .legacy.cli import main, read_json


if __name__ == "__main__":
    raise SystemExit(main())
