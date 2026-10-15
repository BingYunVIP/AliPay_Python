#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077
if [ ! -x .venv/bin/gunicorn ]; then
  echo "请先执行：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
exec .venv/bin/gunicorn --bind 127.0.0.1:8100 --workers 2 --threads 2 --umask 0077 --no-control-socket wsgi:app
