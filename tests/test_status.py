import argparse, io, json, os, re, shutil, tempfile, time, unittest
from contextlib import redirect_stdout
from unittest import mock
from relaylib import commands, freshness, gitops, localtests, merged, scancache, sessions, state, status, waiting
from relaylib.errors import RelayError
from tests import helpers


class StatusFixture(unittest.TestCase):
    """Temp projects root, homes and a fake gh; helpers for repos, features and session records."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.projects = os.path.join(self.tmp, "projects")
        os.makedirs(self.projects)
        self.gh_json = os.path.join(self.tmp, "pr.json")
        helpers.write(self.gh_json, '{"state": "OPEN", "baseRefName": "develop"}')
        p = mock.patch.dict(os.environ, {
            "RELAY_ROOT": self.projects, "RELAY_HOME": os.path.join(self.tmp, "home"),
            "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude"), "CODEX_HOME": os.path.join(self.tmp, "codex"),
            "RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH), "FAKE_GH_JSON": self.gh_json})
        p.start()
        self.addCleanup(p.stop)
        helpers.name_test_repos(self)

    def put(self, root, slug, **over):
        st = state.new_state(slug, os.path.basename(root), {"provider": "codex", "session": "x"}, f"feat/{slug}")
        st.update(over)
        state.write_state(state.state_path(root, slug), st)

    def repo(self, name, features, commit=False):
        root = os.path.join(self.projects, name)
        os.makedirs(root)
        helpers.sh(root, "git", "init", "-q", "-b", "develop")
        if commit:
            helpers.sh(root, "git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q",
                       "--allow-empty", "-m", "init")
        for slug, over in features.items():
            self.put(root, slug, **over)
        return root

    def run_cmd(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            commands.main(list(argv))
        return out.getvalue()

    def session(self, sid, state_="waiting", age_s=16 * 3600, said=None, provider="claude", pending=(), cwd=None):
        """A hook record for session sid, and a Claude transcript ending with `said`."""
        os.makedirs(sessions.folder(), exist_ok=True)
        at = time.time() - age_s
        with open(sessions.record_path(provider, sid), "w") as f:
            json.dump({"provider": provider, "session_id": sid, "cwd": cwd, "state": state_, "since": at, "at": at,
                       "event": "Stop", "pending": [{"tool_use_id": None, "tool_name": t, "input_sha1": "x"}
                                                     for t in pending]}, f)
        if said:
            helpers.write(os.path.join(self.tmp, "claude", "projects", "-w", sid + ".jsonl"), json.dumps(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": said}]}}) + "\n")

    def held(self, name, slug, sid, **over):
        return self.repo(name, {slug: dict({"stage": "build", "status": "drafting",
                                            "owner": {"provider": "claude", "session": sid}}, **over)}, commit=True)


class StatusTest(StatusFixture):
    def test_rows_and_waiting_first(self):
        self.repo("alpha", {"one": {"stage": "spec", "status": "in-review", "rounds": {"spec": 1}}})
        self.repo("beta", {"two": {"stage": "plan", "status": "waiting-owner"}})
        os.makedirs(os.path.join(self.projects, "not-a-repo"))
        rows = status.scan(self.projects)
        self.assertEqual([r["feature"] for r in rows], ["two", "one"])
        self.assertTrue(rows[0]["waiting_on_owner"])
        self.assertEqual((rows[1]["round"], rows[1]["owner"]), (1, "codex"))

    def settings(self, mode, command="make test"):
        from relaylib import cimode
        return cimode.Settings(mode, mode, command, 60, "develop")

    def test_status_tests_flag_each_text(self):  # R15
        st = {"stage": "build", "pr": 5}
        info = {"baseRefName": "develop", "headRefOid": "abcdef1234567890"}
        cases = [("pr", "make test", None, "tests: GitHub CI"),
                 ("none", "", None, "tests: not gated (ci = none)"),
                 ("local", "", None, "tests: local, no test command"),
                 ("local", "make test", {"status": "pass"}, "tests: local, green at abcdef12"),
                 ("local", "make test", {"status": "running", "started": 0}, None),
                 ("local", "make test", {"status": "fail"}, "tests: local, failed at abcdef12"),
                 ("local", "make test", {"status": "stopped"}, "tests: local, stopped at abcdef12"),
                 ("local", "make test", None, "tests: local, not run for abcdef12")]
        for mode, command, rec, want in cases:
            with mock.patch("relaylib.cimode.settings", return_value=self.settings(mode, command)), \
                    mock.patch("relaylib.gitops.code_sha", return_value="abcdef1234567890"), \
                    mock.patch("relaylib.localtests.read", return_value=rec):
                got = status.tests_flag("/x", st, info)
            if want is None:
                self.assertRegex(got, r"^tests: local, running since \d\d:\d\d$")
            else:
                self.assertEqual(got, want)

    def test_status_tests_flag_is_unknown_on_error_and_absent_outside_build(self):
        self.assertIsNone(status.tests_flag("/x", {"stage": "spec", "pr": 5}))
        self.assertIsNone(status.tests_flag("/x", {"stage": "build"}))
        with mock.patch("relaylib.cimode.settings", side_effect=status.RelayError("bad base config")):
            self.assertEqual(status.tests_flag("/x", {"stage": "build", "pr": 5}, {"baseRefName": "develop"}),
                             "tests: unknown (bad base config)")

    def test_checkouts_skip_test_runs(self):  # R14
        root = self.repo("alpha", {}, commit=True)
        helpers.sh(root, "git", "worktree", "add", "-q", "--detach", os.path.join(self.tmp, "real-wt"))
        run = os.path.join(localtests.runs_dir(root), "abc-1")
        os.makedirs(os.path.dirname(run), exist_ok=True)
        helpers.sh(root, "git", "worktree", "add", "-q", "--detach", run)
        legacy = os.path.join(root, ".git", "relay", "test-runs", "abc-2")
        helpers.sh(root, "git", "worktree", "add", "-q", "--detach", legacy)
        found = [os.path.realpath(p) for p in status.checkouts(self.projects)]
        self.assertIn(os.path.realpath(os.path.join(self.tmp, "real-wt")), found)
        self.assertNotIn(os.path.realpath(run), found)
        self.assertNotIn(os.path.realpath(legacy), found)

    def test_running_tests_leave_the_row_not_waiting_on_the_owner(self):  # N5
        row = {"repo": "r", "feature": "f", "stage": "build", "status": "ready-to-merge", "pr": 5,
               "flags": ["tests: local, running since 10:02"], "checkout": self.tmp}
        st = {"stage": "build", "status": "ready-to-merge", "updated": "2026-10-07T10:00:00-04:00"}
        out = status.with_asks(row, st, "HEAD", [], time.time())
        self.assertEqual(out["asks"], [])
        self.assertFalse(out["waiting_on_owner"])

    def test_merged_pr_is_done_and_hidden(self):
        self.repo("alpha", {"one": {"stage": "build", "status": "ready-to-merge", "pr": 5,
                                    "owner_actions": [{"action": "override go"}]}})
        helpers.write(self.gh_json, '{"state": "MERGED"}')
        rows = status.scan(self.projects)
        self.assertEqual((rows[0]["stage"], rows[0]["owner_actions"]), ("done", 2))
        self.assertIn("No relay features", self.run_cmd("status"))
        self.assertEqual(json.loads(self.run_cmd("status", "--all", "--json"))[0]["status"], "done")

    def test_a_copied_feature_whose_pr_is_not_here_is_hidden_with_why(self):  # merge-and-tidy D11, D12
        root = self.repo("upstream-studio", {"clip-studio": {"repo": "UpstreamPartners", "stage": "build",
                                                             "status": "ready-to-merge", "pr": 57}})
        helpers.sh(root, "git", "remote", "add", "origin", "https://github.com/MCrockett/upstream-studio.git")
        gone = helpers.fake_bin(self.tmp, "gh404", "#!/bin/sh\necho 'GraphQL: Could not resolve to a PullRequest "
                                                    "with the number of 57.' >&2\nexit 1\n")
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": gone}):
            rows = status.scan(self.projects)
            out = self.run_cmd("status")
        self.assertEqual((rows[0]["stage"], rows[0]["waiting_on_owner"]), ("done", False))
        self.assertIn("No relay features", out)
        self.assertIn("Hidden: upstream-studio/clip-studio: PR #57 is not in MCrockett/upstream-studio; this feature "
                      "was copied from UpstreamPartners", out)

    def test_a_closed_pr_is_hidden_with_why_and_a_merged_one_without_a_line(self):
        self.repo("alpha", {"one": {"stage": "build", "status": "ready-to-merge", "pr": 5},
                            "two": {"stage": "build", "status": "ready-to-merge", "pr": 6}})
        helpers.write(self.gh_json, '{"state": "CLOSED"}')
        out = self.run_cmd("status")
        self.assertEqual(out.count("Hidden: alpha/"), 2)
        self.assertIn("Hidden: alpha/one: PR #5 was closed without merging", out)
        helpers.write(self.gh_json, '{"state": "MERGED", "baseRefName": "develop"}')
        self.assertNotIn("Hidden", self.run_cmd("status"))

    def test_an_unreadable_pr_stays_visible(self):
        self.repo("alpha", {"one": {"stage": "build", "status": "ready-to-merge", "pr": 5}})
        os.remove(self.gh_json)                                   # gh fails: not known to be finished
        rows = status.scan(self.projects)
        self.assertEqual(rows[0]["stage"], "build")
        self.assertIsNone(rows[0]["hidden"])

    def test_failed_fetch_never_reports_fresh(self):
        self.repo("alpha", {"one": {"stage": "build", "status": "ready-to-merge", "pr": 5}})  # no origin
        with mock.patch("relaylib.freshness.check", return_value=(True, "code and merge result unchanged")):
            row = status.scan(self.projects)[0]
        self.assertTrue(any(f.startswith("stale GO: cannot confirm") for f in row["flags"]), row["flags"])
        self.assertFalse(row["waiting_on_owner"])

    def test_upstream_stale_flag(self):
        self.repo("alpha", {"one": {"stage": "plan", "status": "drafting", "verdicts": {"spec": "GO"},
                                    "reviewed": {"spec": {"inputs": {"spec": "old-hash"}}, "plan": None,
                                                 "build": None}}})
        self.assertIn("stale spec GO: re-review before going on", status.scan(self.projects)[0]["flags"])

    def test_worktree_outside_projects_folder(self):
        root = self.repo("alpha", {}, commit=True)
        elsewhere = os.path.join(self.tmp, "elsewhere-wt")
        helpers.sh(root, "git", "worktree", "add", "-q", "-b", "feat/far", elsewhere)
        self.put(elsewhere, "far", status="in-review")
        self.assertEqual([r["feature"] for r in status.scan(self.projects)], ["far"])

    def test_unreadable_state_is_a_row_not_a_crash(self):
        root = self.repo("alpha", {})
        helpers.write(os.path.join(root, "docs/relay/bad/state.md"), "garbage")
        self.assertIn("missing frontmatter", status.scan(self.projects)[0]["flags"][0])

    def test_a_checkout_git_cannot_read_does_not_hide_the_others(self):
        # A worktree whose main repo is elsewhere (another machine, or the host outside a container).
        self.repo("alpha", {"one": {"stage": "spec", "status": "in-review", "rounds": {"spec": 1}}}, commit=True)
        broken = os.path.join(self.projects, "alpha-worktree")
        os.makedirs(broken)
        helpers.write(os.path.join(broken, ".git"), "gitdir: /nowhere/alpha/.git/worktrees/alpha-worktree\n")
        rows = status.scan(self.projects)
        self.assertIn(("alpha", "one"), [(r["repo"], r["feature"]) for r in rows])

    def test_render(self):
        self.repo("alpha", {"one": {"status": "waiting-owner"}})
        text = status.render(status.scan(self.projects))
        self.assertIn("REPO", text)
        self.assertIn("* ", text)
        self.assertIn("1 waiting on you", text)


    def test_a_waiting_session_is_waiting_on_the_owner_with_its_words(self):
        question = "How do you want to treat the pi window: clarification, or exploratory? " + "More. " * 50
        self.held("bottomsup", "nba", "S1")
        self.session("S1", said=question)
        fetches = []
        with mock.patch.object(gitops, "fetch", side_effect=lambda repo: fetches.append(repo)):
            rows = status.scan(self.projects)
            text = self.run_cmd("status")
            data = json.loads(self.run_cmd("status", "--json"))
        self.assertEqual(fetches, [])                                  # R7: no new fetch
        self.assertEqual([a["kind"] for a in rows[0]["asks"]], ["answer"])
        self.assertTrue(rows[0]["waiting_on_owner"])
        self.assertIn("1 waiting on you", text)
        self.assertIn("\n    Answer the claude session · waiting 16h\n", text)
        self.assertIn("\n    Agent: How do you want to treat the pi window", text)
        self.assertEqual(data[0]["excerpt"]["source"], "agent")
        self.assertEqual(len(data[0]["excerpt"]["text"]), 161)          # the excerpt line stays clipped
        self.assertEqual(data[0]["excerpt"]["full"], question)          # --json adds the full text (status-json-detail R1)
        self.assertNotIn(question.strip(), text)

    def test_scan_with_claims_names_the_sessions_features_account_for(self):
        alpha = self.held("alpha", "drafting", "S1")
        helpers.sh(alpha, "git", "switch", "-q", "-c", "feat/drafting")          # health matches checkouts on the branch
        self.held("beta", "merging", "S2", status="ready-to-merge", pr=5)          # health does not watch it
        self.session("S3", said="hi")
        rec = json.load(open(sessions.record_path("claude", "S3")))
        rec["cwd"] = os.path.realpath(os.path.join(self.projects, "alpha"))
        json.dump(rec, open(sessions.record_path("claude", "S3"), "w"))       # matched to alpha by its folder
        self.repo("gamma", {"shipped": {"stage": "done", "status": "done",
                                        "owner": {"provider": "claude", "session": "S4"}}})
        rows, claimed = status.scan_with_claims(self.projects)
        self.assertEqual(claimed, {"S1", "S2", "S3"})
        self.assertEqual(rows, status.scan(self.projects))

    def test_other_sessions_follow_the_features(self):
        self.held("bottomsup", "nba", "S1", status="waiting-owner", stage="spec")
        pd = self.repo("proteindiary", {}, commit=True)
        self.session("P1", age_s=2 * 3600, said="I still need the publisher JSON key path.", cwd=os.path.realpath(pd))
        x = os.path.join(self.tmp, "home", "x")
        os.makedirs(x)
        with mock.patch.dict(os.environ, {"HOME": os.path.join(self.tmp, "home")}):
            self.session("C1", "permission", age_s=300, provider="codex", pending=("Bash",), cwd=os.path.realpath(x))
            before = json.loads(self.run_cmd("status", "--json"))
            text = self.run_cmd("status")
            everything = self.run_cmd("status", "--all")
        self.assertIn("\nOther sessions waiting on you\n* ~/x · codex · needs approval: Bash · 5m\n"
                      "* proteindiary · claude · waiting 2h\n    Agent: I still need the publisher JSON key path.\n",
                      text)
        self.assertLess(text.index("nba"), text.index("Other sessions"))
        self.assertIn("3 waiting on you (*)", text)
        self.assertIn("Other sessions waiting on you\n* ~/x", everything)
        self.assertEqual([r["feature"] for r in before], ["nba"])                 # --json: feature rows only
        self.assertNotIn("P1", json.dumps(before))

    def request(self, repo="thinkling", text="fix the thing\nmore", age_s=7200):
        from relaylib import dispatch
        return dispatch.create({"repo": {"name": repo, "path": "/p/" + repo}, "request": text, "owner_said": None,
                                "slug": None, "requested_by": "owner", "asked_by": None, "relayed_by": None},
                               now=time.time() - age_s)

    def test_waiting_requests_show_in_a_block_and_the_total(self):  # dispatch R10, R11
        self.held("bottomsup", "nba", "S1", status="waiting-owner", stage="spec")
        r = self.request()
        started = self.request(repo="gone")
        from relaylib import dispatch
        dispatch.mark_started(started["id"], {"slug": "s", "branch": "b", "provider": "claude", "session_id": "Z"})
        text = self.run_cmd("status")
        self.assertIn(f"\nRequests waiting for a session\n* thinkling · request {r['id']} · waiting 2h\n"
                      "    fix the thing\n", text)
        self.assertNotIn(started["id"], text)
        self.assertIn("2 waiting on you (*)", text)
        self.assertNotIn("Requests waiting", self.run_cmd("status", "--json"))

    def test_requests_with_no_features_and_an_unreadable_record(self):
        from relaylib import dispatch
        r = self.request()
        with open(os.path.join(dispatch.folder(), "qbad.json"), "w") as f:
            f.write("{")
        text = self.run_cmd("status")
        self.assertTrue(text.startswith("No relay features."), text)
        self.assertIn(f"request {r['id']}", text)
        self.assertIn("1 waiting on you (*)", text)

    def test_other_sessions_without_features(self):
        self.session("P1", age_s=2 * 3600, said="Which key?")
        text = self.run_cmd("status")
        self.assertTrue(text.startswith("No relay features."), text)
        self.assertIn("* Unknown folder · claude · waiting 2h\n    Agent: Which key?", text)
        self.assertIn("1 waiting on you (*)", text)

    def test_no_other_sessions_leaves_status_as_it_was(self):
        self.repo("alpha", {"one": {"stage": "spec", "status": "waiting-owner"}})
        clock = time.time()
        with mock.patch("relaylib.status.time.time", return_value=clock):  # wait ages print to the second
            text = self.run_cmd("status")
            again = status.render(sorted(status.scan(self.projects), key=status.waiting.sort_key)) + "\n"
        self.assertNotIn("Other sessions", text)
        self.assertEqual(text, again)

    def test_global_settings_choose_other_sessions(self):
        pd = self.repo("proteindiary", {}, commit=True)
        helpers.write(os.path.join(state.relay_dir(pd), "config.toml"),
                      "[ui]\nhealth_grace_minutes = 60\nother_sessions_hours = 1\n")
        self.session("A", age_s=600, said="a", cwd=os.path.realpath(pd))
        self.session("B", age_s=5 * 3600, said="b", cwd=os.path.realpath(pd))
        text = self.run_cmd("status")
        self.assertIn("· waiting 10m", text)
        self.assertIn("· waiting 5h", text)

    def test_waits_print_newest_first_and_json_keeps_oldest_first(self):
        self.held("alpha", "newer", "S1")
        self.held("beta", "older", "S2")
        self.session("S1", age_s=600)
        self.session("S2", age_s=7200)
        self.assertEqual([r["feature"] for r in status.scan(self.projects)], ["newer", "older"])
        text = self.run_cmd("status")
        self.assertLess(text.index("newer"), text.index("older"))  # newest wait first (running-now D8)
        self.assertEqual([r["feature"] for r in json.loads(self.run_cmd("status", "--json"))], ["older", "newer"])

    def test_an_unreadable_checkout_asks_to_be_fixed_after_dated_waits(self):
        self.held("alpha", "one", "S1")
        self.session("S1", age_s=600)
        broken = self.repo("broken", {})
        helpers.write(os.path.join(broken, "docs/relay/bad/state.md"), "garbage")
        rows = status.scan(self.projects)
        text = self.run_cmd("status")
        bad = next(r for r in rows if r["feature"] == "?")
        self.assertEqual([a["kind"] for a in bad["asks"]], ["error"])
        self.assertTrue(bad["asks"][0]["text"].startswith("Fix: ") and "missing frontmatter" in bad["asks"][0]["text"])
        self.assertIsNone(bad["asks"][0]["since"])
        self.assertIsNone(bad["wait_since"])
        self.assertIn("2 waiting on you", text)
        self.assertLess(text.index("one"), text.index("    Fix: "))
        self.assertNotIn("Fix: ", text.split("    Fix: ")[1].split("\n")[0])   # no age on the error line

    def test_one_session_holding_two_features_waits_once(self):
        self.held("alpha", "one", "S1")
        self.held("beta", "two", "S1")
        self.session("S1")
        raw = status.scan(self.projects)
        self.assertEqual(sum(r["waiting_on_owner"] for r in raw), 2)  # the scan keeps both for the dashboard
        rows = {r["feature"]: r for r in waiting.one_ask_per_session(raw)}
        waiting_rows = [f for f, r in rows.items() if r["waiting_on_owner"]]
        self.assertEqual(len(waiting_rows), 1)
        kept, dropped = waiting_rows[0], ({"one", "two"} - set(waiting_rows)).pop()
        self.assertEqual([a["text"] for a in rows[kept]["asks"]], [f"Answer the claude session · also for {dropped}"])
        self.assertEqual(rows[dropped]["asks"], [])
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            status.cmd_status(argparse.Namespace(all=False, json=False))
        self.assertEqual(out.getvalue().count("Answer the claude session"), 1)

    def test_session_health_failing_for_one_row_keeps_its_state_asks(self):
        self.held("alpha", "one", "S1", status="waiting-owner")
        self.held("beta", "two", "S2")
        self.session("S1")
        self.session("S2")
        real = status.health.session_health
        with mock.patch.object(status.health, "session_health", side_effect=lambda repo, st, *a: (
                (_ for _ in ()).throw(RuntimeError("boom")) if st["owner"]["session"] == "S1" else real(repo, st, *a))):
            rows = {r["feature"]: r for r in status.scan(self.projects)}
        self.assertEqual([a["kind"] for a in rows["one"]["asks"]], ["decide"])
        self.assertEqual([a["kind"] for a in rows["two"]["asks"]], ["answer"])

    def test_missing_or_bad_hook_records_keep_state_and_activity_asks(self):
        self.held("alpha", "one", "S1", status="waiting-owner")
        self.held("beta", "two", "S2")
        os.makedirs(sessions.folder(), exist_ok=True)
        helpers.write(sessions.record_path("claude", "S2"), "{not json")
        old = time.time() - 3 * 86400                                   # the checkout went quiet days ago
        for root in (os.path.join(self.projects, "alpha"), os.path.join(self.projects, "beta")):
            helpers.sh(root, "git", "switch", "-q", "-c", "feat/" + ("one" if root.endswith("alpha") else "two"))
            for dirpath, _, files in os.walk(root):
                for name in files:
                    os.utime(os.path.join(dirpath, name), (old, old))
        with mock.patch.object(status.health, "activity", return_value={"last_activity": old, "unpushed": None,
                                                                        "origin": None}):
            rows = {r["feature"]: r for r in status.scan(self.projects)}
        self.assertEqual([a["kind"] for a in rows["one"]["asks"]], ["decide", "check"])
        self.assertEqual([a["kind"] for a in rows["two"]["asks"]], ["check"])
        self.assertIn("no activity 3d", rows["two"]["asks"][0]["text"])

    def test_same_repo_name_different_origins_are_separate_rows(self):
        a = self.repo("api-a", {"one": {"repo": "api", "status": "in-review"}})
        b = self.repo("api-b", {"one": {"repo": "api", "status": "in-review"}})
        helpers.sh(a, "git", "remote", "add", "origin", "https://github.com/org-a/api.git")
        helpers.sh(b, "git", "remote", "add", "origin", "https://github.com/org-b/api.git")
        self.assertEqual(len(status.scan(self.projects)), 2)

    def feature_with_handoff(self, commit, push):
        origin, work = helpers.make_repo(self.projects)  # projects/work plus a bare projects/origin.git
        helpers.sh(work, "git", "switch", "-q", "-c", "feat/one")
        self.put(work, "one", stage="build", status="drafting")
        helpers.write(os.path.join(state.feature_dir(work, "one"), "handoff.md"), "paused\n")
        if commit:
            helpers.sh(work, "git", "add", "-A")
            helpers.sh(work, "git", "commit", "-q", "-m", "relay: handoff one")
        if push:
            helpers.sh(work, "git", "push", "-q", "-u", "origin", "feat/one")
        return work

    def test_a_pushed_handoff_is_waiting_on_the_owner(self):
        self.feature_with_handoff(commit=True, push=True)
        row = status.scan(self.projects)[0]
        self.assertTrue(row["waiting_on_owner"])
        self.assertIn("handoff: open a session and run `relay take`", row["flags"])

    def test_an_unpushed_handoff_is_not_waiting_yet(self):
        for commit in (False, True):   # skeleton being filled in, or a push that failed
            with self.subTest(commit=commit):
                shutil.rmtree(self.projects)
                os.makedirs(self.projects)
                self.feature_with_handoff(commit=commit, push=False)
                row = status.scan(self.projects)[0]
                self.assertFalse(row["waiting_on_owner"])
                self.assertIn("handoff drafted, not pushed yet", row["flags"])

    def test_an_old_pushed_handoff_says_so(self):
        work = self.feature_with_handoff(commit=True, push=True)
        path = os.path.join(state.feature_dir(work, "one"), "handoff.md")
        old = os.path.getmtime(path) - 3 * 86400
        os.utime(path, (old, old))
        self.assertIn("handoff (over 24h old): open a session and run `relay take`",
                      status.scan(self.projects)[0]["flags"])

    def test_features_on_branches_not_checked_out_are_listed(self):
        origin, work = helpers.make_repo(self.projects)
        helpers.sh(work, "git", "switch", "-q", "-c", "feat/away")
        self.put(work, "away", stage="build", status="ready-to-merge", pr=9)
        helpers.sh(work, "git", "add", "-A")
        helpers.sh(work, "git", "commit", "-q", "-m", "relay: away")
        helpers.sh(work, "git", "push", "-q", "-u", "origin", "feat/away")
        helpers.sh(work, "git", "switch", "-q", "develop")
        rows = status.scan(self.projects)
        self.assertEqual([r["feature"] for r in rows], ["away"])
        self.assertIn("on origin/feat/away (not checked out)", rows[0]["flags"])

    def test_a_go_on_a_branch_not_checked_out_is_still_checked_for_freshness(self):
        origin, work = helpers.make_repo(self.projects)
        helpers.sh(work, "git", "switch", "-q", "-c", "feat/away")
        helpers.write(os.path.join(work, "app.py"), "print(1)\n")
        helpers.write(os.path.join(state.feature_dir(work, "away"), "idea.md"), "fix\n")
        st = state.new_state("away", "work", {"provider": "codex", "session": "x"}, "feat/away", small=True)
        st.update(status="ready-to-merge", pr=9, verdicts={"build": "GO"})
        state.write_state(state.state_path(work, "away"), st)
        helpers.sh(work, "git", "add", "-A")
        helpers.sh(work, "git", "commit", "-q", "-m", "code and state")
        helpers.sh(work, "git", "fetch", "-q", "origin")
        st["reviewed"]["build"] = freshness.snapshot(work, st, "origin/develop")
        state.write_state(state.state_path(work, "away"), st)
        helpers.sh(work, "git", "commit", "-qam", "relay: build GO")
        helpers.sh(work, "git", "push", "-q", "-u", "origin", "feat/away")
        helpers.sh(work, "git", "switch", "-q", "develop")

        row = status.scan(self.projects)[0]
        self.assertFalse(any(f.startswith("stale") for f in row["flags"]), row["flags"])
        self.assertTrue(row["waiting_on_owner"])

        helpers.sh(work, "git", "remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        row = status.scan(self.projects)[0]
        self.assertTrue(any(f.startswith("stale GO: cannot confirm") for f in row["flags"]), row["flags"])
        self.assertFalse(row["waiting_on_owner"])
        helpers.sh(work, "git", "remote", "set-url", "origin", origin)

        other = helpers.clone(origin, os.path.join(self.tmp, "other"))
        helpers.sh(other, "git", "switch", "-q", "feat/away")
        helpers.write(os.path.join(other, "app.py"), "print(2)\n")
        helpers.sh(other, "git", "commit", "-qam", "code after the GO")
        helpers.sh(other, "git", "push", "-q")
        row = status.scan(self.projects)[0]
        self.assertIn("stale GO: code changed since the GO", row["flags"])
        self.assertFalse(row["waiting_on_owner"])

    def test_a_stacked_checkout_does_not_make_lower_features_look_stale(self):
        origin, work = helpers.make_repo(self.projects)
        helpers.sh(work, "git", "switch", "-q", "-c", "feat/one")
        helpers.write(os.path.join(work, "app.py"), "print(1)\n")
        helpers.write(os.path.join(state.feature_dir(work, "one"), "idea.md"), "fix\n")
        st = state.new_state("one", "work", {"provider": "codex", "session": "x"}, "feat/one", small=True)
        st.update(status="ready-to-merge", pr=9, verdicts={"build": "GO"})
        state.write_state(state.state_path(work, "one"), st)
        helpers.sh(work, "git", "add", "-A")
        helpers.sh(work, "git", "commit", "-q", "-m", "one")
        helpers.sh(work, "git", "fetch", "-q", "origin")
        st["reviewed"]["build"] = freshness.snapshot(work, st, "origin/develop")
        state.write_state(state.state_path(work, "one"), st)
        helpers.sh(work, "git", "commit", "-qam", "relay: build GO")
        helpers.sh(work, "git", "push", "-q", "-u", "origin", "feat/one")
        helpers.sh(work, "git", "switch", "-q", "-c", "feat/two")          # stacked, with more code
        helpers.write(os.path.join(work, "more.py"), "print(2)\n")
        helpers.sh(work, "git", "add", "-A")
        helpers.sh(work, "git", "commit", "-q", "-m", "two's code")
        helpers.sh(work, "git", "push", "-q", "-u", "origin", "feat/two")
        row = [r for r in status.scan(self.projects) if r["feature"] == "one"][0]
        self.assertFalse(any(f.startswith("stale") for f in row["flags"]), row["flags"])
        self.assertTrue(row["waiting_on_owner"])

    def test_a_fallback_review_is_flagged(self):
        self.repo("alpha", {"one": {"stage": "plan", "status": "drafting",
                                    "review_notes": {"plan": "fallback: claude:claude-fable-5-2 because codex out"}}})
        self.assertIn("plan review: fallback: claude:claude-fable-5-2 because codex out",
                      status.scan(self.projects)[0]["flags"])

    def test_an_earlier_stages_fallback_review_stays_visible(self):
        self.repo("alpha", {"one": {"stage": "plan", "status": "drafting",
                                    "review_notes": {"spec": "fallback: claude:claude-fable-5-2 because codex out"}}})
        self.assertIn("spec review: fallback: claude:claude-fable-5-2 because codex out",
                      status.scan(self.projects)[0]["flags"])

if __name__ == "__main__":
    unittest.main()


class JsonDetailTest(StatusFixture):
    """status-json-detail: excerpt.full, owner_action_items and one store per relay status run."""

    def pushed(self, work, slug, base="develop", **over):
        helpers.sh(work, "git", "switch", "-q", base)
        helpers.sh(work, "git", "switch", "-q", "-c", f"feat/{slug}")
        self.put(work, slug, stage="build", **over)
        helpers.sh(work, "git", "add", "-A")
        helpers.sh(work, "git", "commit", "-q", "-m", f"relay: {slug}")
        helpers.sh(work, "git", "push", "-q", "-u", "origin", f"feat/{slug}")

    def tree(self):
        """A local feature, origin-only features (one merged, one stacked under another), all on one repo."""
        origin, work = helpers.make_repo(self.projects)
        self.pushed(work, "away", status="drafting", pr=7,
                    owner_actions=[{"at": "2026-11-27T10:00:00-05:00", "action": "owner started"}])
        self.pushed(work, "landed", status="ready-to-merge", pr=8)
        self.pushed(work, "base", status="drafting")
        self.pushed(work, "stacked", base="feat/base", status="drafting")  # carries base's state.md blob too
        helpers.sh(work, "git", "switch", "-q", "develop")
        self.put(work, "here", status="drafting", owner_actions=[
            {"at": "2026-11-26T09:00:00-05:00", "action": "owner joined idea", "relayed_by": "hub"},
            {"at": "2026-11-26T10:00:00-05:00", "action": "owner submitted spec", "via": {"channel": "x"}}])
        return work

    def merged_pr(self, pr):
        real = merged.resolve
        return mock.patch.object(merged, "resolve",
                                 side_effect=lambda repo, st: ("merged", None) if st.get("pr") == pr else real(repo, st))

    def json_rows(self, *extra):
        return {r["feature"]: r for r in json.loads(self.run_cmd("status", "--json", "--all", *extra))}

    def test_owner_action_items_list_each_action_with_its_time_only(self):
        self.tree()
        with self.merged_pr(8):
            rows = self.json_rows()
        self.assertEqual(rows["here"]["owner_action_items"], [
            {"action": "owner joined idea", "at": "2026-11-26T09:00:00-05:00"},
            {"action": "owner submitted spec", "at": "2026-11-26T10:00:00-05:00"}])
        self.assertEqual(rows["away"]["owner_action_items"],
                         [{"action": "owner started", "at": "2026-11-27T10:00:00-05:00"}])  # origin-only row
        self.assertEqual(rows["landed"]["owner_action_items"], [{"action": "PR #8 merged on GitHub", "at": None}])
        for row in rows.values():
            self.assertEqual(len(row["owner_action_items"]), row["owner_actions"], row["feature"])

    def test_a_merged_local_feature_ends_with_the_merge_item(self):
        root = self.repo("alpha", {"one": {"stage": "build", "status": "ready-to-merge", "pr": 5, "owner_actions": [
            {"at": "2026-11-26T09:00:00-05:00", "action": "owner started"}]}})
        with self.merged_pr(5):
            row = self.json_rows()["one"]
        self.assertEqual(row["owner_action_items"][-1], {"action": "PR #5 merged on GitHub", "at": None})
        self.assertEqual(row["owner_actions"], 2)
        self.assertTrue(root)

    def test_malformed_owner_actions_are_skipped_and_a_bad_time_is_null(self):
        self.repo("alpha", {"one": {"owner_actions": [
            {"action": "no time"}, {"action": "odd time", "at": 5}, "not a dict", {"at": "2026-11-26"},
            {"action": 3, "at": "2026-11-26"}]}})
        row = self.json_rows()["one"]
        self.assertEqual(row["owner_action_items"], [{"action": "no time", "at": None},
                                                     {"action": "odd time", "at": None}])

    def test_an_unreadable_state_row_has_no_items(self):
        root = self.repo("alpha", {})
        helpers.write(state.state_path(root, "broken"), "not a state file\n")
        rows = json.loads(self.run_cmd("status", "--json"))
        bad = [r for r in rows if r["feature"] == "?"]
        self.assertTrue(bad)
        self.assertEqual((bad[0]["owner_action_items"], bad[0]["owner_actions"]), ([], 0))

    def test_excerpt_full_keeps_the_whole_message_up_to_the_limit(self):
        from relaylib import agentask
        said = "First line of the question.\nSecond line.\n" + "word " * 2000
        self.held("bottomsup", "nba", "S1")
        self.session("S1", said=said)
        data = json.loads(self.run_cmd("status", "--json"))
        text = self.run_cmd("status")
        excerpt = data[0]["excerpt"]
        self.assertEqual(excerpt["full"], agentask.full(said))
        self.assertEqual(len(excerpt["full"]), agentask.FULL)
        self.assertIn("\nSecond line.", excerpt["full"])                   # line breaks kept
        self.assertNotIn("\n", excerpt["text"])                            # text stays flattened
        self.assertEqual(len(excerpt["text"]), 161)
        self.assertNotIn("Second line.\nword", text)                       # the text output shows the excerpt only

    def test_no_transcript_means_no_excerpt(self):
        self.held("bottomsup", "nba", "S1")
        self.session("S1")
        self.assertIsNone(json.loads(self.run_cmd("status", "--json"))[0]["excerpt"])

    def test_rows_are_the_same_with_and_without_the_run_store(self):
        self.tree()
        norm = lambda rows: sorted(json.dumps(r, sort_keys=True) for r in rows)
        with self.merged_pr(8):
            plain = norm(status.scan(self.projects))
            with gitops.read_memo(), scancache.use(scancache.Store()):
                stored = norm(status.scan(self.projects))
        self.assertEqual(plain, stored)
        self.assertEqual(len(plain), 5)

    def test_status_reads_each_state_blob_once_and_never_by_git_show(self):
        work = self.tree()
        stacked_blob = helpers.sh(work, "git", "rev-parse", "origin/feat/stacked:docs/relay/base/state.md").strip()
        self.assertEqual(stacked_blob, helpers.sh(work, "git", "rev-parse",
                                                  "origin/feat/base:docs/relay/base/state.md").strip())
        calls, asked = [], []
        real_run, real_cat = gitops.run, gitops.cat_files

        def run(args, cwd, *a, **k):
            calls.append(list(args))
            return real_run(args, cwd, *a, **k)

        def cat(root, specs):
            asked.extend(specs)
            return real_cat(root, specs)
        with mock.patch.object(gitops, "run", side_effect=run), mock.patch.object(gitops, "cat_files", side_effect=cat):
            self.run_cmd("status", "--json")
        shows = [c for c in calls if c[:2] == ["git", "show"] and any("state.md" in x for x in c)]
        self.assertEqual(shows, [])
        blobs = [s for s in asked if re.fullmatch(r"[0-9a-f]{40}", s)]
        self.assertIn(stacked_blob, blobs)
        self.assertEqual(len(blobs), len(set(blobs)))

    def test_status_runs_its_scan_inside_a_store_and_memo(self):
        self.repo("alpha", {"one": {}})
        seen, real = [], status.scan_with_claims

        def scan(*a, **k):
            seen.append((scancache.active() is not None, gitops.memo_active()))
            return real(*a, **k)
        with mock.patch.object(status, "scan_with_claims", side_effect=scan):
            self.run_cmd("status")
            self.run_cmd("status", "--json")
        self.assertEqual(seen, [(True, True), (True, True)])
        self.assertIsNone(scancache.active())
        self.assertFalse(gitops.memo_active())

    def _broken(self, **patch):
        """Rows of one status --json run with patch applied, then of a run without it."""
        real = gitops.run

        def run(args, cwd, *a, **k):  # git ls-tree fails, as on an unreadable object store
            if list(args[:2]) == ["git", "ls-tree"]:
                raise RelayError("git ls-tree failed")
            return real(args, cwd, *a, **k)
        with mock.patch.multiple(gitops, **patch) if patch else mock.patch.object(gitops, "run", side_effect=run):
            broken = json.loads(self.run_cmd("status", "--json"))
        return broken, json.loads(self.run_cmd("status", "--json"))

    def test_a_failed_blob_read_flags_the_repo_for_that_run_only(self):
        self.tree()
        broken, after = self._broken(cat_files=mock.Mock(side_effect=RelayError("cat-file failed")))
        self.assertTrue(all(r.get("scan_error") for r in broken if r["repo"] == "work"))
        self.assertTrue(any("cannot read origin branches" in f for r in broken for f in r["flags"]))
        self.assertFalse(any(r.get("scan_error") for r in after))
        self.assertIn("away", [r["feature"] for r in after])

    def test_a_failed_tree_listing_flags_the_repo_for_that_run_only(self):
        self.tree()
        broken, after = self._broken()
        self.assertTrue(all(r.get("scan_error") for r in broken if r["repo"] == "work"))
        self.assertFalse(any(r.get("scan_error") for r in after))

    def test_a_repo_left_with_no_rows_shows_a_waiting_question_row(self):
        origin, work = helpers.make_repo(self.projects)
        self.pushed(work, "away", status="drafting")
        helpers.sh(work, "git", "switch", "-q", "develop")
        broken, after = self._broken(cat_files=mock.Mock(side_effect=RelayError("cat-file failed")))
        self.assertEqual([(r["feature"], r["waiting_on_owner"]) for r in broken], [("?", True)])
        self.assertEqual([r["feature"] for r in after], ["away"])


class JsonDetailHubTest(unittest.TestCase):
    def test_the_hub_item_ignores_the_new_row_fields(self):
        from relaylib import hub
        row = {"repo": "alpha", "feature": "one", "stage": "build", "status": "waiting-owner", "waiting_on_owner": True,
               "asks": [{"kind": "answer", "text": "Answer", "session": "claude:S1"}], "flags": [], "pr": None,
               "excerpt": {"source": "agent", "text": "short"}, "owner_actions": 1}
        plain = hub._feature_item(dict(row))
        richer = hub._feature_item(dict(row, excerpt=dict(row["excerpt"], full="short and the rest"),
                                        owner_action_items=[{"action": "owner started", "at": None}]))
        self.assertEqual(plain, richer)
