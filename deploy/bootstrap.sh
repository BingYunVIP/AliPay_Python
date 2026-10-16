#!/usr/bin/env bash
# Shared dependency preparation for the native launchers

bypay_os_check() {
  [[ -r /etc/os-release ]] || { echo '无法识别操作系统'; return 1; }
  local os_id os_name
  os_id="$(. /etc/os-release; printf '%s' "$ID")"
  os_name="$(. /etc/os-release; printf '%s' "$PRETTY_NAME")"
  case "$os_id" in ubuntu|debian) printf '操作系统：%s\n' "$os_name" ;; *) echo '暂仅支持 Ubuntu 和 Debian'; return 1 ;; esac
}

bypay_root() {
  if [[ $(id -u) == 0 ]]; then "$@"
  elif command -v sudo >/dev/null; then sudo -- "$@"
  else echo '安装系统工具需要 root 权限，请使用 root 运行脚本' >&2; return 1
  fi
}

bypay_base_tools() {
  local missing=0 command_name
  for command_name in curl tar xz sha256sum setsid flock; do command -v "$command_name" >/dev/null || missing=1; done
  [[ -s /etc/ssl/certs/ca-certificates.crt ]] || missing=1
  if command -v curl >/dev/null; then
    local version major minor
    version="$(curl --version | head -n 1 | awk '{print $2}')"; major="${version%%.*}"; version="${version#*.}"; minor="${version%%.*}"
    [[ "$major" -gt 7 || ( "$major" -eq 7 && "$minor" -ge 68 ) ]] || missing=1
  fi
  if [[ "$missing" == 1 ]]; then
    echo '正在安装基础工具'
    bypay_root apt-get update
    bypay_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl tar xz-utils coreutils util-linux
  fi
}

bypay_download() {
  curl --fail --location --proto '=https' --tlsv1.2 --retry 3 --connect-timeout 10 --max-time 180 --silent --show-error "$1" --output "$2"
}

bypay_python_ready() {
  [[ -x "$1" ]] && "$1" -c 'import sys,venv,ensurepip; raise SystemExit(sys.version_info < (3,10))' >/dev/null 2>&1
}

bypay_ensure_python() {
  local project="$1" candidate architecture archive folder
  for candidate in "$project/.venv/bin/python" "$project/.tools/python3" "$(command -v python3 || true)" /usr/bin/python3; do
    if bypay_python_ready "$candidate"; then BYPAY_PYTHON="$candidate"; return; fi
  done
  echo '正在安装 Python 与虚拟环境工具'
  bypay_root apt-get update
  bypay_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-venv
  if bypay_python_ready /usr/bin/python3; then BYPAY_PYTHON=/usr/bin/python3; return; fi
  # Older distributions get a project-local Python instead of replacing /usr/bin/python3
  case "$(uname -m)" in x86_64) architecture=x86_64 ;; aarch64|arm64) architecture=aarch64 ;; *) echo '当前 CPU 架构暂不支持自动安装 Python'; return 1 ;; esac
  mkdir -p "$project/.tools"
  folder="$(mktemp -d "$project/.tools/python-install.XXXXXX")"
  archive="uv-${architecture}-unknown-linux-gnu.tar.gz"
  echo '系统 Python 版本较旧，正在为本项目安装 Python 3.12'
  bypay_download "https://github.com/astral-sh/uv/releases/latest/download/$archive" "$folder/$archive"
  bypay_download "https://github.com/astral-sh/uv/releases/latest/download/$archive.sha256" "$folder/$archive.sha256"
  (cd "$folder"; sha256sum --check "$archive.sha256")
  tar -xzf "$folder/$archive" -C "$folder"
  mkdir -p "$project/.tools/uv"
  cp "$folder/uv-${architecture}-unknown-linux-gnu/uv" "$project/.tools/uv/uv"
  export UV_PYTHON_INSTALL_DIR="$project/.tools/python" UV_PYTHON_BIN_DIR="$project/.tools/bin" UV_CACHE_DIR="$project/.tools/cache"
  "$project/.tools/uv/uv" python install 3.12
  candidate="$("$project/.tools/uv/uv" python find --managed-python 3.12)"
  ln -sfn "$candidate" "$project/.tools/python3"
  rm -rf -- "$folder"
  BYPAY_PYTHON="$project/.tools/python3"
  bypay_python_ready "$BYPAY_PYTHON"
}

bypay_ensure_venv() {
  local project="$1" requirements="$2" fingerprint stamp
  if ! bypay_python_ready "$project/.venv/bin/python"; then
    [[ ! -e "$project/.venv" ]] || mv "$project/.venv" "$project/.venv.previous-$(date +%s)"
    "$BYPAY_PYTHON" -m venv "$project/.venv"
  fi
  BYPAY_PYTHON="$project/.venv/bin/python"
  fingerprint="$(sha256sum "$requirements" | awk '{print $1}')"
  stamp="$project/.venv/.bypay-dependencies"
  if [[ ! -f "$stamp" || "$(cat "$stamp")" != "$fingerprint" ]] || ! "$BYPAY_PYTHON" -c 'import flask,gunicorn,cryptography,dotenv' >/dev/null 2>&1; then
    echo '正在安装或更新项目依赖'
    "$BYPAY_PYTHON" -m pip install --disable-pip-version-check -q -r "$requirements"
    "$BYPAY_PYTHON" -m pip check
    printf '%s\n' "$fingerprint" > "$stamp"
  fi
}

bypay_node_ready() {
  command -v node >/dev/null && command -v npm >/dev/null && node -e 'const v=+process.versions.node.split(".")[0];process.exit(v>=24 && v%2===0 ? 0 : 1)' >/dev/null 2>&1 && [[ "$(npm --version | cut -d. -f1)" -ge 10 ]]
}

bypay_ensure_node() {
  local project="$1" architecture folder version archive
  export PATH="$project/.tools/node/bin:$PATH"
  if bypay_node_ready; then return; fi
  case "$(uname -m)" in x86_64) architecture=x64 ;; aarch64|arm64) architecture=arm64 ;; *) echo '当前 CPU 架构暂不支持自动安装 Node.js'; return 1 ;; esac
  mkdir -p "$project/.tools"
  folder="$(mktemp -d "$project/.tools/node-install.XXXXXX")"
  echo '正在安装 Node.js LTS'
  bypay_download https://nodejs.org/dist/index.json "$folder/index.json"
  version="$("$BYPAY_PYTHON" -c 'import json,sys; print(next(v["version"] for v in json.load(open(sys.argv[1])) if v["lts"] and int(v["version"].split(".")[0][1:])>=24))' "$folder/index.json")"
  [[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo '无法确认 Node.js 版本'; return 1; }
  archive="node-${version}-linux-${architecture}.tar.xz"
  bypay_download "https://nodejs.org/dist/$version/$archive" "$folder/$archive"
  bypay_download "https://nodejs.org/dist/$version/SHASUMS256.txt" "$folder/checksums"
  (cd "$folder"; awk -v name="$archive" '$2 == name {print}' checksums > selected.sha256; test -s selected.sha256; sha256sum --check selected.sha256)
  tar -xJf "$folder/$archive" -C "$project/.tools"
  ln -sfn "node-${version}-linux-${architecture}" "$project/.tools/node"
  hash -r
  rm -rf -- "$folder"
  bypay_node_ready || { echo 'Node.js 无法运行，请检查系统版本与基础库'; return 1; }
}
