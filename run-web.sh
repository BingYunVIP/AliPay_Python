#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements-web.txt
if [ ! -f frontend/dist/index.html ]; then
  npm --prefix frontend ci --no-fund
  npm --prefix frontend run build
fi
if [ "${1:-}" = "--dev" ]; then
  export PUBLIC_BASE_URL=http://localhost:8000
  export BYPAY_TRUST_PROXY=0
fi
.venv/bin/python -m web.worker &
callback_pid=$!
.venv/bin/gunicorn --bind 127.0.0.1:8000 --workers 2 --threads 4 --umask 0077 --no-control-socket web.wsgi:app &
web_pid=$!
cleanup() {
  kill "$web_pid" "$callback_pid" 2>/dev/null || true
  wait "$web_pid" "$callback_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
wait -n "$web_pid" "$callback_pid"
