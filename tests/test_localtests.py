import json, os, signal, tempfile, threading, time, unittest
from unittest import mock
from relaylib import gitops, localtests, state
from tests import helpers
from tests.helpers import sh, write


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class LocalTestsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        notify = helpers.fake_bin(self.tmp, "osascript", "#!/bin/sh\nexit 0\n")
        p = mock.patch.dict(os.environ, {"RELAY_CONFIG": os.path.join(self.tmp, "config.toml"),
                                         "RELAY_HOME": os.path.join(self.tmp, "home"),
                                         "RELAY_NOTIFY_BIN": notify})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.stop_runners)
        self.origin, self.repo = helpers.make_repo(self.tmp)
        write(os.path.join(self.repo, "only_in_commit"), "x\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "code")
        self.commit = sh(self.repo, "git", "rev-parse", "HEAD").strip()
        self.code = gitops.code_sha(self.repo, "docs/relay/", "HEAD")

    def stop_runners(self):
        for rec in localtests.records(self.repo):
            if rec["status"] == "running":
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.kill(rec["pid"], sig)
                    except (ProcessLookupError, TypeError):
                        break
                    time.sleep(0.2)

    def run_cmd(self, command, timeout=5, commit=None, code=None, slug="feat"):
        return localtests.run(self.repo, commit or self.commit, code or self.code, command, timeout, slug, "feat/x")

    def test_key_changes_with_code_and_command_not_bookkeeping(self):
        k = localtests.key_id(self.code, "true")
        write(os.path.join(self.repo, "docs/relay/x/state.md"), "s\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "bookkeeping")
        same = gitops.code_sha(self.repo, "docs/relay/", "HEAD")
        self.assertEqual(localtests.key_id(same, "true"), k)
        write(os.path.join(self.repo, "more"), "y\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "more code")
        self.assertNotEqual(localtests.key_id(gitops.code_sha(self.repo, "docs/relay/", "HEAD"), "true"), k)
        self.assertNotEqual(localtests.key_id(self.code, "false"), k)

    def test_run_pass_and_fail(self):
        rec = self.run_cmd("true")
        self.assertEqual((rec["status"], rec["exit"]), ("pass", 0))
        rec = self.run_cmd("exit 3")
        self.assertEqual((rec["status"], rec["exit"]), ("fail", 3))
        self.assertEqual(localtests.read(self.repo, self.code, "exit 3")["status"], "fail")
        runs = os.path.join(gitops.common_dir(self.repo), "relay", "test-runs")
        self.assertEqual(os.listdir(runs), [])
        self.assertNotIn("test-runs", sh(self.repo, "git", "worktree", "list"))

    def test_run_uses_clean_checkout_of_commit(self):
        write(os.path.join(self.repo, "untracked_file"), "u\n")
        rec = self.run_cmd("test -f only_in_commit && ! test -f untracked_file")
        self.assertEqual(rec["status"], "pass")

    def test_killed_run_reads_stopped(self):
        self.assertEqual(localtests.start(self.repo, self.commit, self.code, "sleep 30", 5, "feat", "feat/x"),
                         "started")
        rec = localtests.read(self.repo, self.code, "sleep 30")
        self.assertEqual(rec["status"], "running")
        os.killpg(rec["pid"], signal.SIGKILL)  # the runner is its own session leader
        self.assertTrue(wait_for(lambda: localtests.read(self.repo, self.code, "sleep 30")["status"] == "stopped"))

    def test_timeout_records_fail(self):
        rec = self.run_cmd("sleep 30", timeout=0.02)
        self.assertEqual(rec["status"], "fail")
        self.assertEqual(rec["reason"], "timed out after 0.02 min")

    def test_worktree_failure_is_fail(self):
        rec = self.run_cmd("true", commit="0" * 40)
        self.assertEqual(rec["status"], "fail")
        with open(rec["log"]) as f:
            self.assertIn("fatal", f.read())

    def test_start_stops_older_run_of_same_feature(self):
        localtests.start(self.repo, self.commit, self.code, "sleep 30", 5, "feat", "feat/x")
        localtests.start(self.repo, self.commit, self.code, "sleep 31", 5, "feat", "feat/x")
        self.assertTrue(wait_for(lambda: localtests.read(self.repo, self.code, "sleep 30")["status"] == "stopped"))
        self.assertEqual(localtests.read(self.repo, self.code, "sleep 31")["status"], "running")

    def test_start_same_key_twice_runs_once(self):
        a = localtests.start(self.repo, self.commit, self.code, "sleep 30", 5, "feat", "feat/x")
        b = localtests.start(self.repo, self.commit, self.code, "sleep 30", 5, "feat", "feat/x")
        self.assertEqual((a, b), ("started", "running"))
        out = os.popen(f"pgrep -f 'relaylib.localtests run {self.repo}' | wc -l").read().strip()
        self.assertEqual(int(out), 1)

    def test_ensure_pass_running_and_rerun_of_fail(self):
        self.assertEqual(localtests.ensure(self.repo, self.commit, self.code, "false", 5, "feat", "feat/x"), "started")
        self.assertTrue(wait_for(lambda: (localtests.read(self.repo, self.code, "false") or {}).get("status") == "fail"))
        self.assertEqual(localtests.ensure(self.repo, self.commit, self.code, "false", 5, "feat", "feat/x"), "started")
        self.run_cmd("true")
        self.assertEqual(localtests.ensure(self.repo, self.commit, self.code, "true", 5, "feat", "feat/x"), "pass")

    def test_leftover_worktree_removed_and_live_record_survives(self):
        runs = os.path.join(gitops.common_dir(self.repo), "relay", "test-runs")
        left = os.path.join(runs, "deadbeefdeadbeef-1")
        sh(self.repo, "git", "worktree", "add", "--detach", left, self.commit)
        localtests.start(self.repo, self.commit, self.code, "sleep 30", 5, "feat", "feat/x")
        localtests.cleanup(self.repo)
        self.assertFalse(os.path.exists(left))
        self.assertEqual(localtests.read(self.repo, self.code, "sleep 30")["status"], "running")
        self.assertEqual(len(os.listdir(runs)), 1)  # the live run's own worktree stays

    def test_cleanup_keeps_live_and_open_features(self):
        # a feature on origin whose state is not done keeps its newest record even when it is the oldest
        sh(self.repo, "git", "checkout", "-q", "-b", "feat/open")
        st = state.new_state("open", "r", "claude", "feat/open")
        st["status"] = "ready-to-merge"
        st["stage"] = "build"
        state.write_state(state.state_path(self.repo, "open"), st)
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "state")
        sh(self.repo, "git", "push", "-q", "origin", "feat/open")
        sh(self.repo, "git", "checkout", "-q", "develop")
        sh(self.repo, "git", "fetch", "-q", "origin")
        for i in range(25):
            slug, branch = ("open", "feat/open") if i == 0 else (f"s{i}", f"feat/s{i}")
            command = f"true # {i}"
            localtests.write(self.repo, {"code": self.code, "command": command, "commit": self.commit,
                                         "status": "pass", "exit": 0, "started": 1000 + i, "finished": 1001 + i,
                                         "slug": slug, "branch": branch, "pid": 1,
                                         "log": "", "lock": "", "reason": ""})
            write(localtests.paths(self.repo, localtests.key_id(self.code, command))["log"], "log\n")
        localtests.cleanup(self.repo)
        left = {r["command"] for r in localtests.records(self.repo)}
        self.assertEqual(len(left), 21)
        self.assertIn("true # 0", left)
        self.assertNotIn("true # 1", left)

    def test_flag_texts(self):
        c = "abcdef1234567890"
        self.assertEqual(localtests.flag({"status": "pass"}, c), "tests: local, green at abcdef12")
        self.assertRegex(localtests.flag({"status": "running", "started": 0}, c), r"^tests: local, running since \d\d:\d\d$")
        self.assertEqual(localtests.flag({"status": "fail"}, c), "tests: local, failed at abcdef12")
        self.assertEqual(localtests.flag({"status": "stopped"}, c), "tests: local, stopped at abcdef12")
        self.assertEqual(localtests.flag(None, c), "tests: local, not run for abcdef12")

    def test_write_is_atomic(self):
        rec = {"code": self.code, "command": "true", "status": "pass"}
        localtests.write(self.repo, rec)
        errors = []
        done = threading.Event()

        def reader():
            path = localtests.paths(self.repo, localtests.key_id(self.code, "true"))["record"]
            while not done.is_set():
                try:
                    with open(path) as f:
                        json.load(f)
                except Exception as e:
                    errors.append(e)
        t = threading.Thread(target=reader)
        t.start()
        for i in range(200):
            localtests.write(self.repo, dict(rec, n=i))
        done.set()
        t.join()
        self.assertEqual(errors, [])
        folder = os.path.dirname(localtests.paths(self.repo, "x")["record"])
        self.assertEqual([n for n in os.listdir(folder) if n.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
