#!/bin/zsh
set -e
cd -- "${0:A:h}"
if (( $# == 0 )); then
  if PYTHONPATH="$PWD" PYTHONNOUSERSITE=1 "$PWD/../.venv/bin/python" -m workbench.open_running "${ARGSCOPE_CODEX_PORT:-8768}" codex 2>/dev/null; then
    exit 0
  fi
fi
exec "$PWD/Launch MVP.command" --provider codex --port "${ARGSCOPE_CODEX_PORT:-8768}" --data-dir "$PWD/outputs/codex-data" "$@"
