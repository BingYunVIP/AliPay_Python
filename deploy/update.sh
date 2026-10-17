#!/usr/bin/env bash
# Code update behind ByPay.sh: fetch, fast-forward only, redeploy, and roll back when the new version does not come up.
# Sourced by ByPay.sh. It never relies on `set -e`: every step is checked, so it behaves the same in any calling context.
# The caller supplies four steps, so this file installs and restarts nothing by itself:
#   bypay_update_ready    succeeds when the install can be updated, otherwise says why
#   bypay_update_backup   snapshots the database, the keys and the settings
#   bypay_update_deploy   builds and starts the code that is checked out now, without asking questions
#   bypay_update_restart  starts the code that is checked out now again, after a rollback
# bypay_update PROJECT returns 0 when updated, 3 when already up to date and 1 when nothing was updated;
# the message tells whether it was cancelled, refused or rolled back.

bypay_update_label() {
  git --no-pager log -1 --date=short --format='%h · %cd · %s' "$1" 2>/dev/null | LC_ALL=C tr -d '\000-\011\013-\037\177'
}

# Puts back the local changes that were saved as a patch before the update, if there were any
bypay_update_reapply() {
  local patch="$1"
  [[ -n "$patch" ]] || return 0
  if git apply --binary "$patch"; then echo '本机原有的改动已还原'; else echo "本机原有的改动未能自动还原，补丁保存在：$patch"; fi
}

bypay_update_rollback() {
  local old="$1" patch="$2" back=0
  echo '部署未成功，正在恢复到更新前的版本'
  # the failed deployment may have touched tracked files, which makes the gentle reset refuse; the patch and the backups already exist
  if ! git reset --keep --quiet "$old" && ! git reset --hard --quiet "$old"; then
    back=1
    echo '代码未能恢复到更新前的版本，请联系维护者核对当前版本'
  fi
  if [[ -d frontend/dist.update-backup ]]; then
    rm -rf -- frontend/dist
    mv -- frontend/dist.update-backup frontend/dist
  fi
  if [[ "$back" == 0 ]]; then bypay_update_reapply "$patch"
  elif [[ -n "$patch" ]]; then echo "本机原有改动的补丁保存在：$patch"; fi
  # the service was stopped for the deployment, so it must be started again whatever happened to the code
  if bypay_update_restart 8>&-; then
    if [[ "$back" == 0 ]]; then echo "已恢复到更新前的版本并重新启动：$(bypay_update_label HEAD)"
    else echo "服务已重新启动，但代码仍是新版本：$(bypay_update_label HEAD)"; fi
  elif [[ "$back" != 0 ]]; then
    echo '服务未能启动，请在菜单选择 1 重新部署，或联系维护者；数据备份在 data/backups'
  else
    echo '直接启动未成功，正在按旧版本重新部署'
    if bypay_update_deploy 8>&-; then
      echo "已恢复到更新前的版本并重新部署：$(bypay_update_label HEAD)"
    else
      echo '旧版本也未能启动，请联系维护者；数据备份在 data/backups'
    fi
  fi
  return 1
}

# Everything that changes the install: backups, the merge, the deployment and the rollback
bypay_update_apply() {
  local old="$1" new="$2" dirty="$3" patch=''
  echo '正在备份数据，更新期间请勿中断'
  bypay_update_backup 8>&- || { echo '数据备份未成功，本次未做任何更改'; return 1; }
  rm -rf -- frontend/dist.update-backup
  if [[ -d frontend/dist ]] && ! cp -a frontend/dist frontend/dist.update-backup; then
    rm -rf -- frontend/dist.update-backup
    echo '网页文件备份未成功，本次未做任何更改'
    return 1
  fi
  if [[ -n "$dirty" ]]; then
    patch="data/update-patches/local-changes-$(date -u +%Y%m%dT%H%M%SZ).patch"
    if ! mkdir -p data/update-patches || ! chmod 700 data/update-patches || ! git diff --binary HEAD > "$patch" || [[ ! -s "$patch" ]] || ! git reset --hard --quiet HEAD; then
      rm -rf -- frontend/dist.update-backup
      echo '本机改动未能另存，本次未做任何更改'
      return 1
    fi
    chmod 600 "$patch"
    echo "本机改动已另存为：$patch"
  fi
  if ! git merge --ff-only --quiet "$new" || [[ "$(git rev-parse HEAD)" != "$new" ]]; then
    rm -rf -- frontend/dist.update-backup
    bypay_update_reapply "$patch"
    echo '最新代码未能合并，本机代码没有被改动'
    return 1
  fi
  echo "最新代码已就位，正在部署：$(bypay_update_label HEAD)"
  if bypay_update_deploy 8>&-; then
    rm -rf -- frontend/dist.update-backup
    echo "更新完成：$(bypay_update_label "$old") → $(bypay_update_label "$new")"
    return 0
  fi
  bypay_update_rollback "$old" "$patch"
}

bypay_update_locked() {
  local branch old new answer dirty count
  trap '' HUP  # a dropped SSH session must not kill the update
  [[ "$(git rev-parse --show-toplevel 2>/dev/null)" == "$(pwd -P)" ]] || { echo '这份代码不是用 git 获取的，无法自动更新'; return 1; }
  branch="$(git symbolic-ref --quiet --short HEAD)" || { echo '当前代码不在分支上，无法自动更新'; return 1; }
  git remote get-url origin >/dev/null 2>&1 || { echo '没有配置代码仓库地址（origin），无法自动更新'; return 1; }
  if ! ( flock -n 7 ) 7>data/deploy.lock; then echo '已有部署任务正在执行，请稍后重试'; return 1; fi
  bypay_update_ready || return 1

  echo '正在获取最新版本'
  GIT_TERMINAL_PROMPT=0 timeout --foreground 180 git fetch --quiet origin "+refs/heads/$branch:refs/remotes/origin/$branch" \
    || { echo '无法获取最新版本，请检查服务器的网络和仓库访问权限，本次未做任何更改'; return 1; }
  old="$(git rev-parse HEAD)" || return 1
  new="$(git rev-parse "refs/remotes/origin/$branch")" || return 1
  if [[ "$old" == "$new" ]]; then echo "已是最新版本：$(bypay_update_label HEAD)"; return 3; fi
  if git merge-base --is-ancestor "$new" "$old"; then echo '本机代码已包含线上的全部更新，无需更新'; return 3; fi
  if ! git merge-base --is-ancestor "$old" "$new"; then echo '本机代码与线上版本已分叉，无法自动更新，请联系维护者处理'; return 1; fi

  count="$(git rev-list --count "$old..$new")" || return 1
  printf '当前版本：%s\n最新版本：%s\n将更新 %s 项内容：\n' "$(bypay_update_label "$old")" "$(bypay_update_label "$new")" "$count"
  git --no-pager log --format='  · %s' --max-count=15 "$old..$new" | LC_ALL=C tr -d '\000-\011\013-\037\177'
  if (( count > 15 )); then printf '  … 另有 %s 项\n' "$((count - 15))"; fi

  dirty="$(git status --porcelain --untracked-files=no)" || return 1
  if [[ -n "$dirty" ]]; then
    echo '检测到本机改动过的程序文件：'
    printf '%s\n' "$dirty" | sed -n '1,10s/^/  /p'
    read -r -p '更新前把这些改动另存为补丁并还原？[y/N] ' answer || answer=n
    case "$answer" in y|Y|yes|YES) ;; *) echo '已取消，本次未做任何更改'; return 1 ;; esac
  else
    read -r -p '现在更新？[Y/n] ' answer || answer=n
    case "$answer" in ''|y|Y|yes|YES) ;; *) echo '已取消，本次未做任何更改'; return 1 ;; esac
  fi

  # From here on the update runs to its end: Ctrl+C is ignored, and when the terminal goes away the update goes on, because
  # its output reaches the screen through tee (which keeps writing the log) instead of failing every command that prints
  trap '' INT
  local tee_options=(--output-error=warn)
  tee "${tee_options[@]}" /dev/null </dev/null >/dev/null 2>&1 || tee_options=()
  [[ ! -f data/update.log ]] || mv -f data/update.log data/update.log.1
  bypay_update_apply "$old" "$new" "$dirty" 2>&1 | tee ${tee_options[@]+"${tee_options[@]}"} data/update.log
  return "${PIPESTATUS[0]}"
}

bypay_update() {
  local project="$1" result=0
  command -v git >/dev/null || { echo '未安装 git，无法更新代码'; return 1; }
  command -v flock >/dev/null || { echo '缺少 flock 工具，无法更新代码'; return 1; }
  mkdir -p "$project/data" || return 1
  (
    cd "$project" || exit 1
    flock -n 8 || { echo '已有更新任务正在执行，请稍后重试'; exit 1; }
    bypay_update_locked
  ) 8>"$project/data/update.lock" || result=$?
  return "$result"
}
