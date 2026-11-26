"""Compatibility shim; the maintained legacy implementation lives in legacy/."""
from .legacy.prototype import *  # noqa: F401,F403


if __name__ == "__main__":
    raise SystemExit(main())
