#!/bin/sh
set -eu
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
if [ ! -x .venv/bin/python ]; then
  "${SECTOR_PYTHON:-python3}" -c 'import sys; assert sys.version_info >= (3, 11), "需要 Python 3.11+；请设置 SECTOR_PYTHON 为可用解释器路径"'
  "${SECTOR_PYTHON:-python3}" -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, uvicorn, akshare, pandas' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm ci)
fi
if [ ! -f frontend/dist/index.html ] || find frontend/src frontend/index.html frontend/vite.config.ts frontend/package.json -type f -newer frontend/dist/index.html | read -r newer; then
  (cd frontend && npm run build)
fi
news_pid=''
api_pid=''
cleanup() {
  [ -z "$news_pid" ] || kill "$news_pid" 2>/dev/null || true
  [ -z "$api_pid" ] || kill "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
if [ "${SECTOR_DISABLE_NEWS:-0}" != 1 ]; then
  .venv/bin/python -m backend.news_worker &
  news_pid=$!
fi
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port "${SECTOR_PORT:-8788}" &
api_pid=$!
wait "$api_pid"
