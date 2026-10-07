import io, json, os, shutil, tempfile, time, unittest
from contextlib import redirect_stdout
from unittest import mock
from relaylib import commands, freshness, gitops, sessions, state, status
from tests import helpers


class StatusTest(unittest.TestCase):
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

    def test_rows_and_waiting_first(self):
        self.repo("alpha", {"one": {"stage": "spec", "status": "in-review", "rounds": {"spec": 1}}})
        self.repo("beta", {"two": {"stage": "plan", "status": "waiting-owner"}})
        os.makedirs(os.path.join(self.projects, "not-a-repo"))
        rows = status.scan(self.projects)
        self.assertEqual([r["feature"] for r in rows], ["two", "one"])
        self.assertTrue(rows[0]["waiting_on_owner"])
        self.assertEqual((rows[1]["round"], rows[1]["owner"]), (1, "codex"))

    def test_merged_pr_is_done_and_hidden(self):
        self.repo("alpha", {"one": {"stage": "build", "status": "ready-to-merge", "pr": 5,
                                    "owner_actions": [{"action": "override go"}]}})
        helpers.write(self.gh_json, '{"state": "MERGED"}')
        rows = status.scan(self.projects)
        self.assertEqual((rows[0]["stage"], rows[0]["owner_actions"]), ("done", 2))
        self.assertIn("No relay features", self.run_cmd("status"))
        self.assertEqual(json.loads(self.run_cmd("status", "--all", "--json"))[0]["status"], "done")

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


    def session(self, sid, state_="waiting", age_s=16 * 3600, said=None, provider="claude", pending=()):
        """A hook record for session sid, and a Claude transcript ending with `said`."""
        os.makedirs(sessions.folder(), exist_ok=True)
        at = time.time() - age_s
        with open(sessions.record_path(provider, sid), "w") as f:
            json.dump({"provider": provider, "session_id": sid, "cwd": None, "state": state_, "since": at, "at": at,
                       "event": "Stop", "pending": [{"tool_use_id": None, "tool_name": t, "input_sha1": "x"}
                                                     for t in pending]}, f)
        if said:
            helpers.write(os.path.join(self.tmp, "claude", "projects", "-w", sid + ".jsonl"), json.dumps(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": said}]}}) + "\n")

    def held(self, name, slug, sid, **over):
        return self.repo(name, {slug: dict({"stage": "build", "status": "drafting",
                                            "owner": {"provider": "claude", "session": sid}}, **over)}, commit=True)

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
        self.assertEqual(len(data[0]["excerpt"]["text"]), 161)          # the excerpt only, never the full text
        self.assertNotIn(question.strip(), json.dumps(data))

    def test_waits_print_oldest_first_but_scan_keeps_its_order(self):
        self.held("alpha", "newer", "S1")
        self.held("beta", "older", "S2")
        self.session("S1", age_s=600)
        self.session("S2", age_s=7200)
        self.assertEqual([r["feature"] for r in status.scan(self.projects)], ["newer", "older"])
        text = self.run_cmd("status")
        self.assertLess(text.index("older"), text.index("newer"))
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
                                    "review_notes": {"plan": "fallback: claude:claude-fable-5-1 because codex out"}}})
        self.assertIn("plan review: fallback: claude:claude-fable-5-1 because codex out",
                      status.scan(self.projects)[0]["flags"])

    def test_an_earlier_stages_fallback_review_stays_visible(self):
        self.repo("alpha", {"one": {"stage": "plan", "status": "drafting",
                                    "review_notes": {"spec": "fallback: claude:claude-fable-5-1 because codex out"}}})
        self.assertIn("spec review: fallback: claude:claude-fable-5-1 because codex out",
                      status.scan(self.projects)[0]["flags"])

if __name__ == "__main__":
    unittest.main()
