"""Start the packaged application using only this release's source and KV."""
import os
from pathlib import Path
import sys
root = Path(__file__).resolve().parent
paths = [str(root / name) for name in ("workbench", "argscope", "falsifylab", "sourcelens")]
sys.path[:0] = paths
os.environ["PYTHONPATH"] = os.pathsep.join(paths)
os.environ["PYTHONNOUSERSITE"] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
from workbench.review_app import main
args = sys.argv[1:]
if not args or args[0] != "verify-report":
    def supplied(option):
        return any(a == option or a.startswith(option + "=") for a in args)
    if not supplied("--kv-root") and not supplied("--kv-port"):
        args = ["--kv-root", str(root / "vendor/kv_server"), *args]
    if not supplied("--data-dir"):
        args = ["--data-dir", str(root / "workbench/outputs/package-data"), *args]
raise SystemExit(main(args))
