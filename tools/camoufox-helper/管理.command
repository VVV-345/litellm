#!/bin/bash
set -u
cd -- "$(dirname -- "$0")" || exit 1
node_bin=''
for candidate in "${CAMOUFOX_NODE:-}" "$(command -v node || true)" /opt/homebrew/bin/node /usr/local/bin/node /opt/homebrew/opt/node@22/bin/node /opt/homebrew/opt/node@24/bin/node "$HOME"/.nvm/versions/node/*/bin/node; do
  if [ -n "$candidate" ] && [ -x "$candidate" ] && "$candidate" -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)' >/dev/null 2>&1; then
    node_bin="$candidate"
    break
  fi
done
if [ -z "$node_bin" ]; then
  echo '需要 Node.js 22 或以上版本（包含 npm）。请从 https://nodejs.org/en/download 安装 macOS 版本后重新双击。'
  result=1
else
  "$node_bin" ./manage.mjs "$@"
  result=$?
fi
if [ "$#" -eq 0 ] && [ -t 0 ]; then
  read -r -p '按回车关闭窗口…' ignored
fi
exit "$result"
