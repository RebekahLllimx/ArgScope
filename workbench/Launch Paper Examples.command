#!/bin/zsh
set -e
cd -- "${0:A:h}"
if (( $# == 0 )); then
  if PYTHONPATH="$PWD" PYTHONNOUSERSITE=1 "$PWD/../.venv/bin/python" -m workbench.open_running "${ARGSCOPE_PORT:-8767}" 2>/dev/null; then
    exit 0
  fi
fi
exec "$PWD/Launch MVP.command" --provider papers --data-dir "$PWD/outputs/paper-examples-data" "$@"
