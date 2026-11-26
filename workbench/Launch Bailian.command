#!/bin/zsh
set -e
cd -- "${0:A:h}"
bailian_config="${ARGSCOPE_BAILIAN_CONFIG:-$PWD/../.local/bailian.env}"
exec "$PWD/Launch MVP.command" --provider bailian --model-config "$bailian_config" --data-dir "$PWD/outputs/bailian-data" "$@"
