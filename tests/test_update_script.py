"""Code update from ByPay.sh (menu 5): deploy/update.sh and its wiring, against throw-away git repositories.

Git, ByPay.sh and deploy/*.sh are the real files. Only what would touch the machine (python, npm, systemd, apt) is replaced
by a prelude that writes down what it was asked to do and in which state the checkout was, so the order of the steps is visible.
"""
import fcntl
import os
import pty
import re
import select
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GIT_ENV = {"GIT_AUTHOR_NAME": "Maintainer", "GIT_AUTHOR_EMAIL": "maintainer@example.test", "GIT_COMMITTER_NAME": "Maintainer",
           "GIT_COMMITTER_EMAIL": "maintainer@example.test", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

# ByPay.sh as shipped, plus one line that loads the prelude right before the menu would start
LOADER = '[[ -z "${BYPAY_TEST_PRELUDE:-}" ]] || source "$BYPAY_TEST_PRELUDE"\n'
GUARD = "# Sourcing this file defines the functions"

PRELUDE = r'''
tool_status() { :; }
bypay_base_tools() { :; }
bypay_ensure_python() { :; }
bypay_ensure_venv() { :; }
bypay_ensure_node() { if [[ -n "${PTY_STOP:-}" ]]; then : > "$LOG.paused"; sleep 2; fi; }  # the terminal is closed during this pause
bypay_has_systemd() { [[ -z "${NO_SYSTEMD:-}" ]]; }
say() { printf '%s | head=%s marker=%s\n' "$*" "$(git rev-parse --short HEAD)" "$(if declare -F bypay_marker >/dev/null; then bypay_marker; else echo none; fi)" >> "$LOG"; }
count() { local n; n=$(( $(cat "$LOG.$1" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$LOG.$1"; echo "$n"; }
bypay_root() { say "root $*"; }
npm() {
  say "npm $*"
  case " $* " in
    *" ci "*) mkdir -p frontend/node_modules/.bin; : > frontend/node_modules/.bin/vite; chmod +x frontend/node_modules/.bin/vite ;;
    *" run build "*)
      rm -rf frontend/dist; mkdir -p frontend/dist; echo "built from $(git rev-parse --short HEAD)" > frontend/dist/index.html
      if [[ -n "${LEAK:-}" ]]; then sleep 4 >/dev/null 2>&1 & fi  # a daemon the deployment leaves behind
      if [[ -n "${TOUCH_TRACKED:-}" ]]; then echo 'changed by the deployment' > app.txt; fi
      if [[ -n "${LOCK_INDEX:-}" ]]; then : > .git/index.lock; fi
      if [[ -n "${BUILD_FAILS:-}" ]]; then echo 'build failed'; return 1; fi ;;
  esac
}
python_command() {
  say "python $*"
  case "$1" in
    unit) echo '[Unit]' ;;
    prepare)
      if [[ -n "${HANGUP:-}" ]]; then kill -HUP "$PPID"; sleep 0.3; fi
      if [[ -n "${INTERRUPT:-}" ]]; then kill -INT 0; sleep 0.3; fi ;;  # Ctrl+C reaches every process of the terminal's foreground group
    backup) if [[ -n "${BACKUP_FAILS:-}" ]]; then echo 'backup failed'; return 1; fi ;;
    ready) if [[ -n "${NOT_READY:-}" ]]; then echo '尚未部署，请先选择 1'; return 1; fi ;;
    wait) if (( $(count wait) <= ${WAIT_FAILS:-0} )); then echo '网页未能正常启动'; return 1; fi ;;
  esac
}
'''

UPDATE_DRIVER = r'''
set -Eeuo pipefail
source "$PROJECT/ByPay.sh"
case "$STYLE" in
  or) code=0; bypay_update "$PROJECT" || code=$? ;;
  if) if bypay_update "$PROJECT"; then code=0; else code=$?; fi ;;
  plain) set +e; bypay_update "$PROJECT"; code=$?; set -e ;;
esac
echo "RESULT=$code"
'''

DEPLOY_DRIVER = r'''
set -Eeuo pipefail
source "$PROJECT/ByPay.sh"
set +e; (set -e; deploy); code=$?; set -e
echo "RESULT=$code"
'''


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    if done.returncode: raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
    return done.stdout.strip()


class Result:
    def __init__(self, output, code):
        self.output, self.code = output, code


class World:
    """A GitHub stand-in, the maintainer's checkout, and the install on the server."""

    def __init__(self, case):
        self.tmp = Path(tempfile.mkdtemp(prefix="bypay-update-"))
        case.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.seed, self.project = (self.tmp / name for name in ("origin.git", "seed", "project"))
        self.log, self.prelude = self.tmp / "calls.log", self.tmp / "prelude.sh"
        self.prelude.write_text(PRELUDE)
        git(self.tmp, "init", "--quiet", "--bare", str(self.origin))
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        git(self.tmp, "init", "--quiet", str(self.seed))
        git(self.seed, "symbolic-ref", "HEAD", "refs/heads/main")
        git(self.seed, "remote", "add", "origin", str(self.origin))
        script = (ROOT / "ByPay.sh").read_text()
        assert GUARD in script
        for name, text, mode in (("ByPay.sh", script.replace(GUARD, LOADER + GUARD, 1), 0o755), ("deploy/bootstrap.sh", None, 0o755), ("deploy/update.sh", None, 0o755),
                                 (".gitignore", None, 0o644), ("frontend/package-lock.json", "{}\n", 0o644), ("app.txt", "v1\n", 0o644)):
            target = self.seed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if text is None: shutil.copy(ROOT / name, target)
            else: target.write_text(text)
            target.chmod(mode)
        git(self.seed, "add", "-A")
        git(self.seed, "commit", "--quiet", "-m", "First version")
        git(self.seed, "push", "--quiet", "origin", "main")
        git(self.tmp, "clone", "--quiet", str(self.origin), str(self.project))
        python = self.project / ".venv/bin/python"  # what a finished deployment leaves behind
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o755)
        (self.project / "frontend/dist").mkdir()
        (self.project / "frontend/dist/index.html").write_text("original build of the first version\n")

    def publish(self, message, files):
        for name, content in files.items():
            path = self.seed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content(path.read_text()) if callable(content) else content)
        git(self.seed, "add", "-A")
        git(self.seed, "commit", "--quiet", "-m", message)
        git(self.seed, "push", "--quiet", "origin", "main")
        return git(self.seed, "rev-parse", "HEAD")

    def head(self): return git(self.project, "rev-parse", "HEAD")

    def read(self, name): return (self.project / name).read_text()

    def actions(self):
        """What the stubbed machine was asked to do, in order."""
        return [line.split(" | ")[0] for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def states(self):
        """The same, with the commit that was checked out and the script generation seen at that moment."""
        return self.log.read_text().splitlines() if self.log.exists() else []

    def execute(self, command, stdin, project, **env):
        for leftover in self.tmp.glob("calls.log*"): leftover.unlink()
        done = subprocess.run(command, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=self.tmp, timeout=120, start_new_session=True,
                              env={**os.environ, **GIT_ENV, "PROJECT": str(project or self.project), "LOG": str(self.log), "BYPAY_TEST_PRELUDE": str(self.prelude), "STYLE": "or", **env})
        return done

    def update(self, answers="\n", project=None, **env):
        done = self.execute(["bash", "-c", UPDATE_DRIVER], answers, project, **env)
        found = re.search(r"^RESULT=(\d+)$", done.stdout, re.M)
        assert found, done.stdout
        return Result(done.stdout, int(found.group(1)))

    def deploy(self, answers="y\n", **env):
        done = self.execute(["bash", "-c", DEPLOY_DRIVER], answers, None, **env)
        return Result(done.stdout, int(re.search(r"^RESULT=(\d+)$", done.stdout, re.M).group(1)))

    def menu(self, answers, *arguments, **env):
        done = self.execute(["bash", str(self.project / "ByPay.sh"), *arguments], answers, None, **env)
        return Result(done.stdout, done.returncode)

    def clean(self): return git(self.project, "status", "--porcelain")

    def update_until_the_terminal_hangs_up(self, **env):
        """Runs the update on a pseudo terminal, answers the question, and closes the terminal while the new version is being deployed."""
        try: probe = pty.openpty()
        except OSError: raise unittest.SkipTest("no pseudo terminal in this environment")
        for descriptor in probe: os.close(descriptor)
        for leftover in self.tmp.glob("calls.log*"): leftover.unlink()
        environment = {**os.environ, **GIT_ENV, "PROJECT": str(self.project), "LOG": str(self.log), "BYPAY_TEST_PRELUDE": str(self.prelude), "STYLE": "or", "PTY_STOP": "1", **env}
        pid, master = pty.fork()
        if pid == 0:
            try:
                os.chdir(self.tmp)
                os.execvpe("bash", ["bash", "-c", UPDATE_DRIVER], environment)
            finally: os._exit(127)
        shown, deadline, answered = b"", time.monotonic() + 60, False
        paused = Path(str(self.log) + ".paused")
        while not paused.exists():
            assert time.monotonic() < deadline, shown.decode(errors="replace")
            if select.select([master], [], [], 0.1)[0]:
                try: shown += os.read(master, 4096)
                except OSError: break
            if not answered and b"[Y/n]" in shown:
                os.write(master, b"\n"); answered = True
        os.close(master)  # the SSH session is gone
        deadline = time.monotonic() + 60
        record = self.project / "data/update.log"
        while time.monotonic() < deadline:
            text = record.read_text() if record.exists() else ""
            if ("更新完成" in text or "部署未成功" in text) and not self.update_lock_held(): break
            time.sleep(0.2)
        try: os.waitpid(pid, 0)
        except ChildProcessError: pass
        return record.read_text() if record.exists() else ""

    def update_lock_held(self):
        with open(self.project / "data/update.lock", "a") as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return True
        return False


FULL_UPDATE = ["python ready", "python backup", "python stop", "npm --prefix frontend ci --no-fund --no-audit", "npm --prefix frontend run build", "python prepare", "python unit",
               "root install -m 0644 data/bypay.service /etc/systemd/system/bypay.service", "root systemctl daemon-reload", "root systemctl enable --now bypay.service", "python wait", "python status"]


class UpdateTests(unittest.TestCase):
    def setUp(self): self.w = World(self)

    # ------------------------------------------------------------------ the normal course
    def test_up_to_date_install_asks_nothing_and_changes_nothing(self):
        before = self.w.head()
        result = self.w.update("")
        self.assertEqual(result.code, 3)
        self.assertIn("已是最新版本", result.output)
        self.assertEqual((self.w.head(), self.w.actions(), self.w.clean()), (before, ["python ready"], ""))

    def test_update_backs_up_first_then_fast_forwards_then_deploys_the_new_code_and_cleans_up(self):
        old = self.w.head()
        self.w.publish("Show the new payment page", {"app.txt": "v2\n"})
        new = self.w.publish("Add the marker", {"ByPay.sh": lambda text: text + "\nbypay_marker() { echo v2; }\n"})
        result = self.w.update("\n")  # Enter takes the default answer
        self.assertEqual(result.code, 0, result.output)
        for text in (f"当前版本：{old[:7]}", f"最新版本：{new[:7]}", "将更新 2 项内容", "· Show the new payment page", "· Add the marker", "更新完成"):
            self.assertIn(text, result.output)
        self.assertEqual((self.w.head(), self.w.read("app.txt")), (new, "v2\n"))
        self.assertEqual(self.w.actions(), FULL_UPDATE)  # an update restarts without asking and does not print the administrator's password
        states = self.w.states()
        self.assertIn(f"head={old[:7]}", states[1])  # the data was saved while the old code was still checked out
        self.assertEqual(self.w.read("frontend/dist/index.html"), f"built from {new[:7]}\n")
        self.assertIn("marker=v2", next(line for line in states if line.startswith("python prepare")))  # the deployment ran the NEW scripts, not the ones loaded before
        self.assertFalse((self.w.project / "frontend/dist.update-backup").exists())
        self.assertEqual(self.w.clean(), "")

    def test_declining_or_closing_the_prompt_changes_nothing(self):
        old = self.w.head()
        self.w.publish("Next", {"app.txt": "v2\n"})
        for answers in ("n\n", "", "no\n", "x\n"):
            with self.subTest(answers=answers):
                result = self.w.update(answers)
                self.assertEqual(result.code, 1)
                self.assertIn("已取消", result.output)
                self.assertEqual((self.w.head(), self.w.actions(), self.w.clean()), (old, ["python ready"], ""))
        self.assertEqual(self.w.update("y\n").code, 0)  # an explicit yes works as well

    def test_what_is_shown_is_safe_and_short(self):
        for number in range(16): self.w.publish(f"Change {number}", {"app.txt": f"v{number}\n"})
        self.w.publish("Fix \x1b[31mred\x1b[0m title\x07", {"app.txt": "last\n"})
        result = self.w.update("n\n")
        self.assertIn("将更新 17 项内容", result.output)
        self.assertEqual(len(re.findall(r"^  · ", result.output, re.M)), 15)
        self.assertIn("另有 2 项", result.output)
        self.assertNotIn("\x1b", result.output)
        self.assertNotIn("\x07", result.output)
        self.assertIn("Fix [31mred[0m title", result.output)

    def test_an_install_without_a_built_website_is_updated_too(self):
        shutil.rmtree(self.w.project / "frontend/dist")
        new = self.w.publish("Next", {"app.txt": "v2\n"})
        result = self.w.update("\n")
        self.assertEqual((result.code, self.w.head()), (0, new))
        self.assertEqual(self.w.read("frontend/dist/index.html"), f"built from {new[:7]}\n")
        self.assertFalse((self.w.project / "frontend/dist.update-backup").exists())

    # ------------------------------------------------------------------ when the new version does not come up
    def test_a_failed_deployment_is_rolled_back_whatever_the_calling_context(self):
        for style in ("or", "if", "plain"):
            with self.subTest(style=style):
                world = World(self)
                old = world.head()
                world.publish("Broken build", {"app.txt": "v2\n"})
                result = world.update("\n", STYLE=style, BUILD_FAILS="1")  # the build fails in the middle of the deployment, where error handling matters
                self.assertEqual(result.code, 1, result.output)
                for text in ("新版本部署未完成", "部署未成功，正在恢复到更新前的版本", "已恢复到更新前的版本并重新启动"):
                    self.assertIn(text, result.output)
                self.assertNotIn("更新完成", result.output)
                self.assertEqual((world.head(), world.read("app.txt")), (old, "v1\n"))
                self.assertEqual(world.read("frontend/dist/index.html"), "original build of the first version\n")  # the half-built website is replaced by the one that was running
                self.assertFalse((world.project / "frontend/dist.update-backup").exists())
                self.assertNotIn("python prepare", world.actions())  # nothing after the failed build ran as part of the new deployment
                self.assertEqual(world.actions()[-5:], ["python unit", "root install -m 0644 data/bypay.service /etc/systemd/system/bypay.service", "root systemctl daemon-reload",
                                                       "root systemctl enable --now bypay.service", "python wait"])  # the old code is started again: stop had disabled the service
                self.assertIn(f"head={old[:7]}", world.states()[-1])
                self.assertEqual(world.clean(), "")

    def test_when_starting_the_old_code_fails_too_it_is_deployed_again(self):
        old = self.w.head()
        self.w.publish("Does not start", {"app.txt": "v2\n"})
        result = self.w.update("\n", WAIT_FAILS="2")  # the new version never answers, nor does the first restart of the old one
        self.assertEqual(result.code, 1)
        self.assertIn("正在按旧版本重新部署", result.output)
        self.assertIn("已恢复到更新前的版本并重新部署", result.output)
        self.assertEqual((self.w.head(), self.w.read("app.txt")), (old, "v1\n"))
        self.assertEqual(self.w.read("frontend/dist/index.html"), f"built from {old[:7]}\n")  # rebuilt from the old sources
        self.assertEqual(self.w.actions().count("python backup"), 1)  # the data is saved once, before anything changed

    def test_a_rollback_that_git_refuses_to_do_gently_is_forced_and_the_service_is_started_again(self):
        old = self.w.head()
        self.w.publish("Touches a file", {"app.txt": "v2\n"})
        result = self.w.update("\n", BUILD_FAILS="1", TOUCH_TRACKED="1")  # the failed deployment leaves a changed tracked file behind
        self.assertEqual((result.code, self.w.head(), self.w.read("app.txt")), (1, old, "v1\n"), result.output)
        self.assertIn("已恢复到更新前的版本并重新启动", result.output)
        self.assertNotIn("未能恢复", result.output)
        self.assertEqual(self.w.actions()[-1], "python wait")
        self.assertEqual(self.w.clean(), "")

    def test_when_the_code_cannot_be_restored_the_service_is_still_started_and_the_owner_is_told(self):
        new = self.w.publish("Next", {"app.txt": "v2\n"})
        result = self.w.update("\n", BUILD_FAILS="1", LOCK_INDEX="1")  # git cannot move the checkout back at all
        (self.w.project / ".git/index.lock").unlink()
        self.assertEqual((result.code, self.w.head()), (1, new), result.output)
        self.assertIn("代码未能恢复到更新前的版本", result.output)
        self.assertIn("服务已重新启动，但代码仍是新版本", result.output)
        self.assertEqual(self.w.actions()[-5:], ["python unit", "root install -m 0644 data/bypay.service /etc/systemd/system/bypay.service", "root systemctl daemon-reload",
                                               "root systemctl enable --now bypay.service", "python wait"])
        self.assertEqual(self.w.read("frontend/dist/index.html"), "original build of the first version\n")

    def test_when_the_code_cannot_be_restored_and_the_service_does_not_start_either_the_way_out_is_named(self):
        new = self.w.publish("Next", {"app.txt": "v2\n"})
        result = self.w.update("\n", LOCK_INDEX="1", WAIT_FAILS="2")  # the new version does not come up, git cannot go back, the restart fails
        (self.w.project / ".git/index.lock").unlink()
        self.assertEqual((result.code, self.w.head()), (1, new), result.output)
        self.assertIn("服务未能启动，请在菜单选择 1 重新部署", result.output)
        self.assertIn("data/backups", result.output)
        self.assertNotIn("已恢复到更新前的版本", result.output)

    def test_when_nothing_starts_any_more_it_says_so_and_where_the_data_backup_is(self):
        old = self.w.head()
        self.w.publish("Does not start", {"app.txt": "v2\n"})
        result = self.w.update("\n", WAIT_FAILS="3")
        self.assertEqual((result.code, self.w.head()), (1, old))
        self.assertIn("旧版本也未能启动", result.output)
        self.assertIn("data/backups", result.output)
        self.assertNotIn("已恢复到更新前的版本并重新", result.output)

    def test_a_dropped_terminal_does_not_stop_an_update_half_way(self):
        new = self.w.publish("Next", {"app.txt": "v2\n"})
        result = self.w.update("\n", HANGUP="1")  # the deployment sends the hang-up signal an SSH disconnect would
        self.assertEqual((result.code, self.w.head()), (0, new), result.output)
        self.assertIn("更新完成", result.output)

    def test_when_the_terminal_hangs_up_the_update_still_completes_and_leaves_a_log(self):
        new = self.w.publish("Next", {"app.txt": "v2\n"})
        record = self.w.update_until_the_terminal_hangs_up()
        self.assertEqual(self.w.head(), new, record)  # the screen was gone for most of the deployment, which is no reason to undo it
        self.assertIn("更新完成", record)
        self.assertNotIn("部署未成功", record)
        self.assertEqual(self.w.read("frontend/dist/index.html"), f"built from {new[:7]}\n")
        self.assertEqual(self.w.actions()[-2:], ["python wait", "python status"])
        self.assertEqual(self.w.clean(), "")

    def test_the_output_of_an_update_is_kept_in_a_private_log_and_the_previous_one_is_kept_once(self):
        self.w.publish("Next", {"app.txt": "v2\n"})
        self.assertEqual(self.w.update("\n").code, 0)
        record = self.w.project / "data/update.log"
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        first = record.read_text()
        for text in ("正在备份数据", "最新代码已就位", "更新完成", "正在准备网页"):
            self.assertIn(text, first)
        self.w.publish("Broken build", {"app.txt": "v3\n"})
        self.assertEqual(self.w.update("\n", BUILD_FAILS="1").code, 1)
        self.assertEqual((self.w.project / "data/update.log.1").read_text(), first)
        self.assertIn("部署未成功", record.read_text())
        self.assertEqual(sorted(path.name for path in (self.w.project / "data").glob("update.log*")), ["update.log", "update.log.1"])

    def test_nothing_the_deployment_leaves_running_keeps_the_update_locked(self):
        self.w.publish("Next", {"app.txt": "v2\n"})
        self.assertEqual(self.w.update("\n", LEAK="1").code, 0)
        self.assertFalse(self.w.update_lock_held())  # a daemon that kept the lock would block every later update

    def test_ctrl_c_during_the_update_does_not_leave_the_service_half_updated(self):
        new = self.w.publish("Next", {"app.txt": "v2\n"})
        result = self.w.update("\n", INTERRUPT="1")  # Ctrl+C while the new version is being deployed
        self.assertEqual((result.code, self.w.head()), (0, new), result.output)
        self.assertIn("更新完成", result.output)
        self.assertEqual(self.w.actions()[-2:], ["python wait", "python status"])
        self.assertEqual(self.w.update("n\n").code, 3)  # up to date now

    def test_ctrl_c_at_the_confirmation_prompt_changes_nothing(self):
        old = self.w.head()
        self.w.publish("Next", {"app.txt": "v2\n"})
        done = self.w.execute(["bash", "-c", 'source "$PROJECT/ByPay.sh"; read() { kill -INT 0; sleep 1; }; bypay_update "$PROJECT"; echo "RESULT=$?"'], "", None)
        self.assertNotIn("更新完成", done.stdout)
        self.assertEqual((self.w.head(), self.w.actions(), self.w.clean()), (old, ["python ready"], ""))

    # ------------------------------------------------------------------ refusals that leave everything as it was
    def test_an_install_that_is_not_ready_is_not_touched(self):
        old = self.w.head()
        self.w.publish("Next", {"app.txt": "v2\n"})
        result = self.w.update("\n", NOT_READY="1")
        self.assertEqual((result.code, self.w.head(), self.w.actions()), (1, old, ["python ready"]))
        self.assertIn("尚未部署", result.output)

    def test_a_failed_backup_stops_the_update_before_anything_changes(self):
        old = self.w.head()
        self.w.publish("Next", {"app.txt": "v2\n"})
        result = self.w.update("\n", BACKUP_FAILS="1")
        self.assertEqual((result.code, self.w.head(), self.w.actions()), (1, old, ["python ready", "python backup"]))
        self.assertIn("数据备份未成功，本次未做任何更改", result.output)
        self.assertEqual((self.w.read("frontend/dist/index.html"), (self.w.project / "frontend/dist.update-backup").exists()), ("original build of the first version\n", False))

    def test_unreachable_repository_changes_nothing(self):
        old = self.w.head()
        shutil.move(self.w.origin, self.w.tmp / "gone.git")
        result = self.w.update("\n")
        self.assertEqual((result.code, self.w.head(), self.w.actions()), (1, old, ["python ready"]))
        self.assertIn("无法获取最新版本", result.output)

    def test_local_commits_are_never_overwritten(self):
        (self.w.project / "local.txt").write_text("mine\n")
        git(self.w.project, "add", "local.txt")
        git(self.w.project, "commit", "--quiet", "-m", "Local change")
        mine = self.w.head()
        ahead = self.w.update("\n")
        self.assertEqual((ahead.code, self.w.head()), (3, mine))
        self.assertIn("已包含线上的全部更新", ahead.output)
        self.w.publish("Upstream change", {"app.txt": "v2\n"})
        diverged = self.w.update("\n")
        self.assertEqual((diverged.code, self.w.head(), self.w.actions()), (1, mine, ["python ready"]))
        self.assertIn("已分叉", diverged.output)

    def test_checkouts_that_cannot_be_updated_are_refused_with_the_reason(self):
        detached = World(self)
        git(detached.project, "checkout", "--quiet", "--detach")
        no_origin = World(self)
        git(no_origin.project, "remote", "remove", "origin")
        no_git = World(self)
        shutil.rmtree(no_git.project / ".git")
        nested = World(self)  # a project folder inside somebody else's repository
        shutil.rmtree(nested.project / ".git")
        git(nested.tmp, "init", "--quiet", "outer")
        inner = nested.tmp / "outer/inner"
        shutil.move(nested.project, inner)
        for world, expected, project in ((detached, "不在分支上", None), (no_origin, "没有配置代码仓库地址", None), (no_git, "不是用 git 获取", None), (nested, "不是用 git 获取", inner)):
            with self.subTest(expected=expected):
                result = world.update("\n", project=project)
                self.assertEqual(result.code, 1)
                self.assertIn(expected, result.output)
                self.assertEqual(world.actions(), [])

    def test_two_updates_or_an_update_during_a_deployment_cannot_overlap(self):
        self.w.publish("Next", {"app.txt": "v2\n"})
        old = self.w.head()
        (self.w.project / "data").mkdir()
        for lock, expected in (("update.lock", "已有更新任务正在执行"), ("deploy.lock", "已有部署任务正在执行")):
            with self.subTest(lock=lock), open(self.w.project / "data" / lock, "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = self.w.update("\n")
                self.assertEqual((result.code, self.w.head(), self.w.actions()), (1, old, []))
                self.assertIn(expected, result.output)
        self.assertEqual(self.w.update("\n").code, 0)  # once the other task is done it works again

    def test_a_merge_git_refuses_leaves_the_checkout_as_it_was(self):
        old = self.w.head()
        self.w.publish("Adds a file", {"new.txt": "from upstream\n"})
        (self.w.project / "new.txt").write_text("my own notes\n")  # untracked, but in the way
        result = self.w.update("\n")
        self.assertEqual((result.code, self.w.head()), (1, old))
        self.assertIn("最新代码未能合并", result.output)
        self.assertEqual(self.w.read("new.txt"), "my own notes\n")
        self.assertEqual(self.w.actions(), ["python ready", "python backup"])
        self.assertFalse((self.w.project / "frontend/dist.update-backup").exists())

    # ------------------------------------------------------------------ files the owner changed by hand
    def local_edit(self):
        (self.w.project / "app.txt").write_text("local edit\n")
        (self.w.project / "extra.txt").write_text("staged file\n")
        git(self.w.project, "add", "extra.txt")

    def test_local_changes_are_kept_unless_the_owner_agrees_to_save_them_as_a_patch(self):
        old = self.w.head()
        self.w.publish("Upstream edit of the same file", {"app.txt": "v2\n"})
        self.local_edit()
        refused = self.w.update("n\n")
        self.assertEqual((refused.code, self.w.head(), self.w.actions()), (1, old, ["python ready"]))
        for text in ("检测到本机改动过的程序文件", "app.txt", "extra.txt", "已取消"):
            self.assertIn(text, refused.output)
        self.assertEqual((self.w.read("app.txt"), self.w.read("extra.txt")), ("local edit\n", "staged file\n"))

    def test_agreeing_saves_the_changes_in_a_private_patch_and_updates(self):
        self.local_edit()
        new = self.w.publish("Upstream edit of the same file", {"app.txt": "v2\n"})
        (self.w.project / "notes.txt").write_text("untracked files are not changes\n")
        result = self.w.update("y\n")
        self.assertEqual((result.code, self.w.head(), self.w.read("app.txt")), (0, new, "v2\n"))
        self.assertFalse((self.w.project / "extra.txt").exists())
        patches = list((self.w.project / "data/update-patches").glob("local-changes-*.patch"))
        self.assertEqual(len(patches), 1)
        self.assertEqual((patches[0].stat().st_mode & 0o777, patches[0].parent.stat().st_mode & 0o777), (0o600, 0o700))
        self.assertTrue(all(text in patches[0].read_text() for text in ("local edit", "staged file")))
        self.assertIn(str(patches[0].relative_to(self.w.project)), result.output)
        self.assertEqual(self.w.read("notes.txt"), "untracked files are not changes\n")

    def test_untracked_files_alone_are_not_treated_as_changes(self):
        new = self.w.publish("Next", {"app.txt": "v2\n"})
        (self.w.project / "notes.txt").write_text("mine\n")
        result = self.w.update("\n")
        self.assertEqual((result.code, self.w.head()), (0, new))
        self.assertNotIn("检测到本机改动", result.output)
        self.assertFalse((self.w.project / "data/update-patches").exists())

    def test_a_rollback_gives_the_hand_made_changes_back(self):
        old = self.w.head()
        self.local_edit()
        self.w.publish("Broken build", {"app.txt": "v2\n"})
        result = self.w.update("y\n", BUILD_FAILS="1")
        self.assertEqual((result.code, self.w.head()), (1, old))
        self.assertIn("本机原有的改动已还原", result.output)
        self.assertEqual((self.w.read("app.txt"), self.w.read("extra.txt")), ("local edit\n", "staged file\n"))

    # ------------------------------------------------------------------ the menu
    def test_menu_lists_the_update_and_still_refuses_arguments(self):
        listed = self.w.menu("0\n")
        self.assertEqual(listed.code, 0)
        for text in ("5）更新代码", "已退出主菜单"):
            self.assertIn(text, listed.output)
        self.assertIn("请输入 0 到 5 之间的数字", self.w.menu("9\n0\n").output)
        refused = self.w.menu("0\n", "unexpected")
        self.assertEqual(refused.code, 2)
        self.assertIn("请直接运行 ./ByPay.sh", refused.output)
        self.assertNotIn("冰云支付", refused.output)

    def test_menu_update_reloads_itself_only_after_a_successful_update(self):
        up_to_date = self.w.menu("5\n0\n")
        self.assertEqual((up_to_date.code, up_to_date.output.count("冰云支付")), (0, 2))
        self.assertNotIn("正在重新载入菜单", up_to_date.output)
        self.assertNotIn("已返回主菜单", up_to_date.output)
        new = self.w.publish("A new menu", {"ByPay.sh": lambda text: text.replace("冰云支付 · ByPay", "冰云支付 · ByPay 新版")})
        updated = self.w.menu("5\n\n0\n")  # the third line is read by the menu that was started again
        self.assertEqual((updated.code, self.w.head()), (0, new), updated.output)
        self.assertEqual(updated.output.count("冰云支付"), 2)
        self.assertLess(updated.output.index("更新完成"), updated.output.index("正在重新载入菜单"))
        self.assertLess(updated.output.index("正在重新载入菜单"), updated.output.index("ByPay 新版"))  # the menu that is shown afterwards is the new file, not the one that was loaded before
        self.assertTrue(updated.output.rstrip().endswith("已退出主菜单"))
        old = self.w.head()
        self.w.publish("Broken build", {"app.txt": "v3\n"})
        failed = self.w.menu("5\n\n0\n", BUILD_FAILS="1")
        self.assertEqual((failed.code, self.w.head()), (0, old))
        self.assertIn("已返回主菜单", failed.output)
        self.assertNotIn("正在重新载入菜单", failed.output)

    def test_status_shows_the_code_version(self):
        shown = self.w.execute(["bash", "-c", 'source "$PROJECT/ByPay.sh"; code_version'], "", None)
        self.assertRegex(shown.stdout.strip(), r"^[0-9a-f]{7,} · \d{4}-\d\d-\d\d$")
        elsewhere = Path(tempfile.mkdtemp(prefix="bypay-nogit-"))
        self.addCleanup(shutil.rmtree, elsewhere, True)
        unknown = self.w.execute(["bash", "-c", f'source "$PROJECT/ByPay.sh"; BYPAY_PROJECT_ROOT={elsewhere} code_version'], "", None)
        self.assertEqual(unknown.stdout.strip(), "未知")

    # ------------------------------------------------------------------ the ordinary deployment keeps working
    def test_ordinary_deployment_still_asks_and_still_shows_the_credentials(self):
        yes = self.w.deploy("y\n")
        self.assertEqual(yes.code, 0, yes.output)
        self.assertEqual(self.w.actions(), [action for action in FULL_UPDATE if action not in ("python ready", "python backup")][:-1] + ["python status --credentials"])
        no = self.w.deploy("n\n")
        self.assertEqual(no.code, 0)
        self.assertEqual(self.w.actions()[-1], "python foreground")
        self.assertNotIn("python unit", self.w.actions())
        closed = self.w.deploy("")  # no answer means no system service, as before
        self.assertEqual(self.w.actions()[-1], "python foreground")
        self.assertEqual(closed.code, 0)
        nosystemd = self.w.deploy("y\n", NO_SYSTEMD="1")
        self.assertIn("当前环境未运行 systemd", nosystemd.output)
        self.assertEqual(self.w.actions()[-1], "python foreground")
        failing = self.w.deploy("y\n", WAIT_FAILS="1")
        self.assertNotEqual(failing.code, 0)
        self.assertIn("部署未完成，请根据上方错误修正后重新选择 1", failing.output)
        self.assertNotIn("新版本部署未完成", failing.output)


class ScriptSyntaxTests(unittest.TestCase):
    def test_scripts_parse(self):
        for name in ("ByPay.sh", "deploy/bootstrap.sh", "deploy/update.sh"):
            with self.subTest(script=name):
                done = subprocess.run(["bash", "-n", str(ROOT / name)], capture_output=True, text=True)
                self.assertEqual((done.returncode, done.stderr), (0, ""))

    def test_sourcing_the_menu_script_defines_the_functions_and_does_not_start_the_menu(self):
        done = subprocess.run(["bash", "-c", 'source ./ByPay.sh; declare -F deploy bypay_update bypay_update_deploy install_service main; echo sourced', "bash", "unexpected-argument"],
                              cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["deploy", "bypay_update", "bypay_update_deploy", "install_service", "main", "sourced"])


if __name__ == "__main__":
    unittest.main()
