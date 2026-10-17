#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
umask 077
BYPAY_PROJECT_ROOT="$PWD"
export PATH="$BYPAY_PROJECT_ROOT/.tools/node/bin:$PATH"
source "$BYPAY_PROJECT_ROOT/deploy/bootstrap.sh"
source "$BYPAY_PROJECT_ROOT/deploy/update.sh"
trap 'printf "\n"' INT

python_command() {
  [[ -x "$BYPAY_PROJECT_ROOT/.venv/bin/python" ]] || { echo '尚未部署，请先选择 1'; return 1; }
  "$BYPAY_PROJECT_ROOT/.venv/bin/python" -m web.management "$@"
}

code_version() { git -C "$BYPAY_PROJECT_ROOT" --no-pager log -1 --date=short --format='%h · %cd' 2>/dev/null || echo 未知; }

tool_status() {
  local tool executable version ready label
  printf '\n环境与部署状态\n'
  bypay_os_check || return
  for tool in python3 node npm curl; do
    executable="$(command -v "$tool" || true)"
    if [[ "$tool" == python3 && -x .venv/bin/python ]]; then executable="$BYPAY_PROJECT_ROOT/.venv/bin/python"; fi
    if [[ -z "$executable" ]]; then printf '%-10s %s\n' "$tool" '未安装 · 部署时自动安装'; continue; fi
    version="$("$executable" --version 2>/dev/null | head -n 1 | awk '{if (NF>1) print $2; else print $1}' || true)"
    ready=1
    case "$tool" in
      python3) bypay_python_ready "$executable" || ready=0 ;;
      node) "$executable" -e 'const v=+process.versions.node.split(".")[0];process.exit(v>=24 && v%2===0 ? 0 : 1)' >/dev/null 2>&1 || ready=0 ;;
      npm) [[ "${version%%.*}" =~ ^[0-9]+$ && "${version%%.*}" -ge 10 ]] || ready=0 ;;
      curl) [[ "$(printf '%s\n' 7.68.0 "$version" | sort -V | head -n 1)" == 7.68.0 ]] || ready=0 ;;
    esac
    label='已就绪'; [[ "$ready" == 1 ]] || label='需更新 · 部署时自动处理'
    printf '%-10s %s · %s\n' "$tool" "$version" "$label"
  done
  printf '代码版本：%s\n' "$(code_version)"
  if [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import dotenv,flask' >/dev/null 2>&1; then
    python_command status
  else
    echo '部署状态：尚未完成'
    echo '系统服务：未创建'
    echo '默认端口：888'
  fi
}

bypay_has_systemd() { [[ -d /run/systemd/system ]]; }

install_service() {
  python_command unit > data/bypay.service || return 1
  bypay_root install -m 0644 data/bypay.service /etc/systemd/system/bypay.service || return 1
  bypay_root systemctl daemon-reload || return 1
  bypay_root systemctl enable --now bypay.service || return 1
  python_command wait
}

# Steps of the code update (deploy/update.sh); deploy runs in its own bash so that its error handling holds inside the update
bypay_update_ready() {
  [[ -x .venv/bin/python ]] || { echo '尚未部署，请先选择 1'; return 1; }
  python_command ready
}
bypay_update_backup() { python_command backup; }
bypay_update_deploy() { BYPAY_UPDATING=1 bash -c 'script=$1; shift; source "$script" && deploy' bash "$BYPAY_PROJECT_ROOT/ByPay.sh"; }
bypay_update_restart() { install_service; }

deploy() (
  set -Eeuo pipefail
  if [[ "${BYPAY_UPDATING:-}" == 1 ]]; then trap 'echo "新版本部署未完成" >&2' ERR
  else trap 'echo "部署未完成，请根据上方错误修正后重新选择 1" >&2' ERR; fi
  tool_status
  bypay_base_tools
  mkdir -p data
  exec 9>data/deploy.lock
  flock -n 9 || { echo '已有部署任务正在执行，请稍后重试'; exit 1; }
  bypay_ensure_python "$BYPAY_PROJECT_ROOT"
  # Stop only this project's previous processes before replacing dependencies
  if [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import dotenv,flask' >/dev/null 2>&1; then python_command stop; fi
  bypay_ensure_venv "$BYPAY_PROJECT_ROOT" "$BYPAY_PROJECT_ROOT/requirements-web.txt"
  bypay_ensure_node "$BYPAY_PROJECT_ROOT"
  echo '正在准备网页'
  local lock_hash
  lock_hash="$(sha256sum frontend/package-lock.json | awk '{print $1}')"
  if [[ ! -x frontend/node_modules/.bin/vite || ! -f frontend/node_modules/.bypay-lock.sha256 || "$(cat frontend/node_modules/.bypay-lock.sha256)" != "$lock_hash" ]]; then
    npm --prefix frontend ci --no-fund --no-audit
    printf '%s\n' "$lock_hash" > frontend/node_modules/.bypay-lock.sha256
  fi
  npm --prefix frontend run build
  python_command prepare
  local answer
  if [[ "${BYPAY_UPDATING:-}" == 1 ]]; then answer=y  # an update restarts the existing service without asking
  else read -r -p '创建并启用 bypay 系统服务？[Y/n] ' answer || answer=n; fi
  case "$answer" in ''|y|Y|yes|YES)
    if bypay_has_systemd; then
      install_service
      if [[ "${BYPAY_UPDATING:-}" == 1 ]]; then python_command status; else python_command status --credentials; fi
    else
      echo '当前环境未运行 systemd，将以前台方式启动'
      flock -u 9
      python_command foreground
    fi ;;
    n|N|no|NO)
      flock -u 9
      python_command foreground ;;
    *) echo '未识别此选项，本次以前台方式启动'; flock -u 9; python_command foreground ;;
  esac
)

main() {
  if [[ $# != 0 ]]; then
    echo '请直接运行 ./ByPay.sh，然后从菜单选择操作'
    exit 2
  fi
  local choice result
  while true; do
    printf '\n  冰云支付 · ByPay\n\n'
    printf '  1）部署\n  2）修改账号或密码\n  3）系统状态\n  4）日志\n  5）更新代码\n  0）退出主菜单\n\n'
    read -r -p '请选择 [0-5]：' choice || break
    case "$choice" in
      1) set +e; (set -e; deploy); result=$?; set -e; if [[ "$result" != 0 ]]; then echo '已返回主菜单'; fi ;;
      2) python_command credentials || true ;;
      3) tool_status || true ;;
      4) python_command logs || true ;;
      5) result=0; bypay_update "$BYPAY_PROJECT_ROOT" || result=$?
         case "$result" in
           0) echo '正在重新载入菜单'; exec bash "$BYPAY_PROJECT_ROOT/ByPay.sh" ;;
           3) ;;
           *) echo '已返回主菜单' ;;
         esac ;;
      0) echo '已退出主菜单'; break ;;
      *) echo '请输入 0 到 5 之间的数字' ;;
    esac
  done
}

# Sourcing this file defines the functions without opening the menu; running it opens the menu
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
