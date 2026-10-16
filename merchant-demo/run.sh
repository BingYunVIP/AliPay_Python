#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
umask 077
DEMO_PROJECT_ROOT="$PWD"
source "$DEMO_PROJECT_ROOT/bootstrap.sh"
[[ $# == 0 ]] || { echo '请直接运行 ./run.sh'; exit 2; }
bypay_os_check
bypay_base_tools
bypay_ensure_python "$DEMO_PROJECT_ROOT"
bypay_ensure_venv "$DEMO_PROJECT_ROOT" "$DEMO_PROJECT_ROOT/requirements.txt"
# Retire only an older service belonging to this exact demo directory
if [[ -d /run/systemd/system ]]; then
  previous="$(systemctl show bypay-merchant-demo.service --property=WorkingDirectory --value 2>/dev/null || true)"
  if [[ "$previous" == "$DEMO_PROJECT_ROOT" ]]; then
    bypay_root systemctl disable --now bypay-merchant-demo.service
  fi
fi
"$BYPAY_PYTHON" initialize.py
exec "$BYPAY_PYTHON" serve.py
