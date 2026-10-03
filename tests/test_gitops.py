import json, os, shutil, tempfile, unittest
from unittest import mock
from relaylib import gitops
from relaylib.errors import RelayError
from tests import helpers


class GitopsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.work = helpers.make_repo(self.tmp)

    def test_basics(self):
        self.assertEqual(os.path.realpath(gitops.repo_root(self.work)), os.path.realpath(self.work))
        self.assertEqual(gitops.current_branch(self.work), "develop")
        self.assertEqual(gitops.repo_name(self.work), "origin")
        self.assertEqual(len(gitops.head_sha(self.work)), 40)

    def test_not_a_repo(self):
        with self.assertRaises(RelayError):
            gitops.repo_root(self.tmp)

    def test_commit_only_prefix(self):
        helpers.write(os.path.join(self.work, "docs/relay/a/state.md"), "x")
        helpers.write(os.path.join(self.work, "other.txt"), "y")
        self.assertTrue(gitops.commit_paths_under(self.work, "docs/relay/a", "relay: test"))
        self.assertIn("other.txt", helpers.sh(self.work, "git", "status", "--short"))
        self.assertIn("docs/relay/a/state.md", helpers.sh(self.work, "git", "show", "--name-only", "HEAD"))
        self.assertFalse(gitops.commit_paths_under(self.work, "docs/relay/a", "nothing"))

    def test_commit_leaves_other_staged_files_alone(self):
        helpers.write(os.path.join(self.work, "staged.txt"), "s")
        helpers.sh(self.work, "git", "add", "staged.txt")
        helpers.write(os.path.join(self.work, "docs/relay/a/state.md"), "x")
        gitops.commit_paths_under(self.work, "docs/relay/a", "relay: test")
        self.assertNotIn("staged.txt", helpers.sh(self.work, "git", "show", "--name-only", "HEAD"))
        self.assertIn("A  staged.txt", helpers.sh(self.work, "git", "status", "--short"))

    def test_changed_paths_case_insensitive(self):
        helpers.write(os.path.join(self.work, "Docs/relay/a/state.md"), "x")
        helpers.sh(self.work, "git", "add", ".")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "tracked as Docs")
        helpers.write(os.path.join(self.work, "Docs/relay/a/state.md"), "changed")
        paths = gitops.changed_paths_under(self.work, "docs/relay/a")
        self.assertEqual(paths, ["Docs/relay/a/state.md"])
        self.assertTrue(gitops.commit_paths_under(self.work, "docs/relay/a", "relay: case"))
        self.assertEqual(helpers.sh(self.work, "git", "status", "--short"), "")

    def test_push_fetch_remote_files(self):
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/x")
        helpers.write(os.path.join(self.work, "docs/relay/x/state.md"), "---\n{}\n---\n")
        gitops.commit_paths_under(self.work, "docs/relay/x", "relay: x")
        gitops.push(self.work, "feat/x")
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        gitops.fetch(other)
        self.assertIn("origin/feat/x", gitops.remote_branches(other))
        self.assertEqual(gitops.ls_files(other, "origin/feat/x", "docs/relay"), ["docs/relay/x/state.md"])
        self.assertEqual(gitops.show(other, "origin/feat/x", "docs/relay/x/state.md"), "---\n{}\n---\n")
        self.assertIsNone(gitops.show(other, "origin/feat/x", "missing.md"))

    def test_fetch_without_origin_or_offline(self):
        helpers.sh(self.work, "git", "remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        with self.assertRaisesRegex(RelayError, "cannot fetch"):
            gitops.fetch(self.work)
        helpers.sh(self.work, "git", "remote", "remove", "origin")
        with self.assertRaisesRegex(RelayError, "no 'origin'"):
            gitops.fetch(self.work)

    def test_worktrees(self):
        wt = os.path.join(self.tmp, "wt")
        helpers.sh(self.work, "git", "worktree", "add", "-q", "-b", "feat/wt", wt)
        self.assertEqual({os.path.realpath(p) for p in gitops.worktrees(self.work)},
                         {os.path.realpath(self.work), os.path.realpath(wt)})

    def test_ci_state(self):
        self.assertEqual(gitops.ci_state({"statusCheckRollup": []}), "none")
        ok = {"status": "COMPLETED", "conclusion": "SUCCESS"}
        self.assertEqual(gitops.ci_state({"statusCheckRollup": [ok, {"state": "SUCCESS"}]}), "green")
        self.assertEqual(gitops.ci_state({"statusCheckRollup": [ok, {"status": "IN_PROGRESS", "conclusion": ""}]}), "pending")
        self.assertEqual(gitops.ci_state({"statusCheckRollup": [{"status": "COMPLETED", "conclusion": "FAILURE"}]}), "failing")
        pending_then_failed = [{"status": "IN_PROGRESS", "conclusion": ""}, {"status": "COMPLETED", "conclusion": "FAILURE"}]
        self.assertEqual(gitops.ci_state({"statusCheckRollup": pending_then_failed}), "failing")

    def test_code_sha_skips_bookkeeping_commits(self):
        code = gitops.head_sha(self.work)
        helpers.write(os.path.join(self.work, "docs/relay/a/state.md"), "x")
        gitops.commit_paths_under(self.work, "docs/relay/a", "relay: bookkeeping")
        self.assertEqual(gitops.code_sha(self.work, "docs/relay/a/"), code)

    def test_ci_for_code_uses_newest_completed_run_across_bookkeeping(self):
        gh = helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH)
        st, table = os.path.join(self.tmp, "status.json"), os.path.join(self.tmp, "by_sha.json")
        helpers.write(st, '{"state": "pending", "total_count": 0}')
        helpers.write(os.path.join(self.work, "app.py"), "code\n")
        helpers.sh(self.work, "git", "add", "app.py")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "code")
        code = gitops.head_sha(self.work)
        helpers.write(os.path.join(self.work, "docs/relay/a/state.md"), "x")
        gitops.commit_paths_under(self.work, "docs/relay/a", "relay: bookkeeping")
        tip = gitops.head_sha(self.work)

        def run(status, conclusion=None):
            return {"total_count": 1, "check_runs": [{"status": status, "conclusion": conclusion}]}

        cases = [
            ({tip: run("completed", "success")}, "green"),                             # CI ran only on the pushed tip
            ({code: run("completed", "success"), tip: run("in_progress")}, "green"),  # tip rerun pending, same code
            ({code: run("completed", "success"), tip: run("completed", "failure")}, "failing"),
            ({tip: run("in_progress")}, "pending"),
            ({}, "none"),
        ]
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": gh, "FAKE_GH_STATUS": st, "FAKE_GH_RUNS_BY_SHA": table}):
            for runs, expected in cases:
                helpers.write(table, json.dumps(runs))
                self.assertEqual(gitops.ci_for_code(self.work, tip, "docs/relay/a/"), expected, runs)

    def test_ci_for_code_ignores_a_merged_side_branch_with_other_code(self):
        gh = helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH)
        st, table = os.path.join(self.tmp, "status.json"), os.path.join(self.tmp, "by_sha.json")
        helpers.write(st, '{"state": "pending", "total_count": 0}')
        helpers.write(os.path.join(self.work, "app.py"), "v1\n")
        helpers.sh(self.work, "git", "add", "app.py")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "code")
        c = gitops.head_sha(self.work)
        helpers.sh(self.work, "git", "switch", "-q", "-c", "side")
        helpers.write(os.path.join(self.work, "app.py"), "v2\n")
        helpers.sh(self.work, "git", "commit", "-qam", "different code")
        b = gitops.head_sha(self.work)
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        helpers.write(os.path.join(self.work, "docs/relay/a/state.md"), "x")
        gitops.commit_paths_under(self.work, "docs/relay/a", "relay: bookkeeping")
        helpers.sh(self.work, "git", "merge", "-q", "-s", "ours", "--no-edit", "side")  # keeps v1, discards b
        m = gitops.head_sha(self.work)

        def run(status, conclusion=None):
            return {"total_count": 1, "check_runs": [{"status": status, "conclusion": conclusion}]}

        helpers.write(table, json.dumps({c: run("completed", "failure"), b: run("completed", "success"),
                                         m: run("in_progress")}))
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": gh, "FAKE_GH_STATUS": st, "FAKE_GH_RUNS_BY_SHA": table}):
            self.assertEqual(gitops.ci_for_code(self.work, m, "docs/relay/a/"), "failing")

    def test_commit_ci_state_reads_runs_and_statuses(self):
        gh = helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH)
        runs, st = os.path.join(self.tmp, "runs.json"), os.path.join(self.tmp, "status.json")
        helpers.write(runs, '{"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "success"}]}')
        helpers.write(st, '{"state": "failure", "total_count": 1}')
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": gh, "FAKE_GH_RUNS": runs, "FAKE_GH_STATUS": st}):
            self.assertEqual(gitops.commit_ci_state(self.work, "abc"), "failing")
            helpers.write(st, '{"state": "pending", "total_count": 0}')
            self.assertEqual(gitops.commit_ci_state(self.work, "abc"), "green")

    def test_pr_info_uses_fake_gh(self):
        gh = helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH)
        data = os.path.join(self.tmp, "pr.json")
        helpers.write(data, '{"number": 7, "state": "OPEN", "baseRefName": "develop"}')
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": gh, "FAKE_GH_JSON": data}):
            self.assertEqual(gitops.pr_info(self.work)["number"], 7)


    def test_commit_ci_state_reads_every_page_and_distrusts_gaps(self):
        gh = helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH)
        runs, st = os.path.join(self.tmp, "runs.json"), os.path.join(self.tmp, "status.json")
        helpers.write(st, '{"state": "pending", "total_count": 0}')
        ok = {"status": "completed", "conclusion": "success"}
        bad = {"status": "completed", "conclusion": "failure"}
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": gh, "FAKE_GH_RUNS": runs, "FAKE_GH_STATUS": st}):
            helpers.write(runs, json.dumps([{"total_count": 2, "check_runs": [ok]},
                                            {"total_count": 2, "check_runs": [bad]}]))
            self.assertEqual(gitops.commit_ci_state(self.work, "abc"), "failing")
            helpers.write(runs, json.dumps({"total_count": 31, "check_runs": [ok] * 30}))
            self.assertEqual(gitops.commit_ci_state(self.work, "abc"), "pending")

if __name__ == "__main__":
    unittest.main()


class ReadMemoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.work = helpers.make_repo(self.tmp)
        gitops._MERGED_PRS.clear()
        self.addCleanup(gitops._MERGED_PRS.clear)

    def count(self):
        patch = mock.patch.object(gitops, "run", wraps=gitops.run)
        spy = patch.start()
        self.addCleanup(patch.stop)
        return spy

    def test_reads_run_once_inside_the_memo_and_every_time_outside(self):
        spy = self.count()
        gitops.git(self.work, "rev-parse", "HEAD")
        gitops.git(self.work, "rev-parse", "HEAD")
        self.assertEqual(spy.call_count, 2)
        with gitops.read_memo():
            first = gitops.git(self.work, "rev-parse", "HEAD").stdout
            self.assertEqual(gitops.git(self.work, "rev-parse", "HEAD").stdout, first)
        self.assertEqual(spy.call_count, 3)

    def test_writes_and_failures_are_not_hidden(self):
        spy = self.count()
        with gitops.read_memo():
            gitops.git(self.work, "commit", "-q", "--allow-empty", "-m", "a")
            gitops.git(self.work, "commit", "-q", "--allow-empty", "-m", "b")
            self.assertEqual(spy.call_count, 2)
            for _ in range(2):  # a cached failing read still raises when checked
                with self.assertRaises(RelayError):
                    gitops.git(self.work, "show", "HEAD:missing.txt")
            self.assertIsNone(gitops.show(self.work, "HEAD", "missing.txt"))
        self.assertEqual(spy.call_count, 3)

    def test_memo_reaches_copied_contexts_only(self):
        import contextvars, threading
        spy = self.count()
        with gitops.read_memo():
            gitops.git(self.work, "rev-parse", "HEAD")
            worker = threading.Thread(target=contextvars.copy_context().run,
                                      args=(gitops.git, self.work, "rev-parse", "HEAD"))
            worker.start(); worker.join()
            self.assertEqual(spy.call_count, 1)
            plain = threading.Thread(target=gitops.git, args=(self.work, "rev-parse", "HEAD"))
            plain.start(); plain.join()  # an owner action's request thread never sees the memo
            self.assertEqual(spy.call_count, 2)

    def test_gh_reads_memoized_and_merged_prs_remembered(self):
        replies = {"pr": '{"state": "OPEN", "number": 3}'}
        def fake(args, cwd, check=True, env=None, timeout=None):
            return subprocess.CompletedProcess(args, 0, replies["pr"], "")
        import subprocess
        with mock.patch.object(gitops, "run", side_effect=fake) as spy:
            with gitops.read_memo():
                gitops.pr_info(self.work, 3)
                gitops.pr_info(self.work, 3)
                gitops.gh_json(self.work, ["api", "-X", "POST", "repos/{owner}/{repo}/x"])
                gitops.gh_json(self.work, ["api", "-X", "POST", "repos/{owner}/{repo}/x"])
            self.assertEqual(spy.call_count, 3)
            gitops.pr_info(self.work, 3)  # open: asked again outside a memo
            self.assertEqual(spy.call_count, 4)
            replies["pr"] = '{"state": "MERGED", "number": 3}'
            gitops.pr_info(self.work, 3)
            self.assertEqual(gitops.pr_info(self.work, 3)["state"], "MERGED")  # merged never changes
            self.assertEqual(spy.call_count, 5)


class NetworkTimeoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.work = helpers.make_repo(self.tmp)

    def test_a_timeout_stops_the_whole_process_group(self):
        import sys, time
        marker = os.path.join(self.tmp, "child.pid")
        script = ("import os, subprocess, sys, time; "
                  "p = subprocess.Popen(['sleep', '30']); open(sys.argv[1], 'w').write(str(p.pid)); time.sleep(30)")
        started = time.monotonic()
        with self.assertRaisesRegex(RelayError, "timed out after 1s"):
            gitops.run([sys.executable, "-c", script, marker], self.tmp, timeout=1)
        self.assertLess(time.monotonic() - started, 10)
        child = int(open(marker).read())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):   # the grandchild (like git's ssh) is gone too
            os.kill(child, 0)

    def test_network_env_prevents_prompts_and_respects_the_owners_ssh(self):
        env = gitops.network_env(self.work)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("ConnectTimeout", env["GIT_SSH_COMMAND"])
        self.assertIn("ServerAliveInterval", env["GIT_SSH_COMMAND"])
        self.assertEqual((env["GIT_HTTP_LOW_SPEED_LIMIT"], env["GIT_HTTP_LOW_SPEED_TIME"]), ("1000", "30"))
        with mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": "my-ssh"}):
            self.assertEqual(gitops.network_env(self.work)["GIT_SSH_COMMAND"], "my-ssh")
        with mock.patch.dict(os.environ, {"GIT_SSH": "/usr/local/bin/ssh-wrapper"}):
            env = gitops.network_env(self.work)
            self.assertNotIn("GIT_SSH_COMMAND", env)                  # it would override the owner's wrapper
            self.assertEqual(env["GIT_SSH"], "/usr/local/bin/ssh-wrapper")
        helpers.sh(self.work, "git", "config", "core.sshCommand", "configured-ssh")
        self.assertNotIn("GIT_SSH_COMMAND", gitops.network_env(self.work))   # git uses the owner's setting

    def test_a_hung_fetch_and_a_hung_gh_time_out(self):
        import sys, time
        helpers.sh(self.work, "git", "remote", "set-url", "origin", "ssh://example.invalid/repo.git")
        with mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": "sleep 30;:"}), \
                mock.patch.object(gitops, "FETCH_TIMEOUT_S", 1):
            started = time.monotonic()
            with self.assertRaisesRegex(RelayError, "cannot fetch origin.*timed out"):
                gitops.fetch(self.work)
            self.assertLess(time.monotonic() - started, 10)
        gh = helpers.fake_bin(self.tmp, "slow-gh", "#!/bin/sh\nsleep 30\n")
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": gh}), mock.patch.object(gitops, "GH_TIMEOUT_S", 1):
            with self.assertRaisesRegex(RelayError, "timed out"):
                gitops.gh_json(self.work, ["pr", "view", "1", "--json", "state"])
