#!/bin/zsh
set -e
set -o pipefail
cd -- "${0:A:h}/.."
export PYTHONPATH="$PWD/workbench:$PWD/argscope:$PWD/falsifylab:$PWD/sourcelens"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$PWD/workbench/outputs"
launch_log="$PWD/workbench/outputs/startup.log"
TRAPZERR() {
  local launch_status=$?
  print "启动失败（退出码 $launch_status）。日志：$launch_log"
  if [[ -t 0 ]]; then
    read -r '?按回车保留信息后退出…'
  fi
  return $launch_status
}
mvp_runtime="$PWD/.venv/bin/python"
if [[ ! -x "$mvp_runtime" ]]; then
  print '找不到项目 Python 环境；请查看 workbench/docs/MVP_GUIDE.md。' | /usr/bin/tee -a "$launch_log"
  false
fi
mvp_kv_root="${ARGSCOPE_KV_ROOT:-$PWD/vendor/kv_server}"
print "启动 ArgScope；请保留此终端窗口，按 Control+C 停止。" | /usr/bin/tee -a "$launch_log"
if [[ -f "$mvp_kv_root/server.py" ]]; then
  "$mvp_runtime" -m workbench.review_app --data-dir "$PWD/workbench/outputs/mvp-data" --port "${ARGSCOPE_PORT:-8767}" --namespace review-mvp --kv-root "$mvp_kv_root" --open-browser "$@" 2>&1 | /usr/bin/tee -a "$launch_log"
else
  "$mvp_runtime" -m workbench.review_app --data-dir "$PWD/workbench/outputs/mvp-data" --port "${ARGSCOPE_PORT:-8767}" --namespace review-mvp --kv-port "${ARGSCOPE_KV_PORT:-6379}" --open-browser "$@" 2>&1 | /usr/bin/tee -a "$launch_log"
fi
