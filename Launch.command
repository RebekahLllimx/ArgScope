#!/bin/zsh
set -e
cd -- "${0:A:h}"
exec "$PWD/workbench/Launch MVP.command" "$@"
