import glob
import io
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from relaylib import commands, config, gitops, ledger, reviewjobs, sessions, state, status, usage, writerusage
from relaylib.errors import RelayError
from relaylib.ui import snapshot
from tests import helpers


class SnapshotBase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = temp.name
        _, self.work = helpers.make_repo(self.tmp)
        helpers.sh(self.work, "git", "switch", "-qc", "feat/demo")
        helpers.name_test_repos(self)
        self.st = state.new_state("demo", "project", {"provider": "codex", "session": "s1"}, "feat/demo")
        self.st.update(stage="spec", status="waiting-owner")
        self.folder = state.feature_dir(self.work, "demo")
        self.save()
        patch = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "relayhome"),
                                            "RELAY_ROOT": self.tmp, "CODEX_HOME": os.path.join(self.tmp, "codex"),
                                            "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude"), "HOME": self.tmp})
        patch.start()
        self.addCleanup(patch.stop)

    def save(self):
        state.write_state(state.state_path(self.work, "demo"), self.st)
        helpers.sh(self.work, "git", "add", ".")
        helpers.sh(self.work, "git", "commit", "-qm", "fixture")
        helpers.sh(self.work, "git", "push", "-qu", "origin", "HEAD")


class SnapshotTest(SnapshotBase):
    def test_rows_use_published_state_and_label_local_changes(self):
        self.st["status"] = "drafting"
        state.write_state(state.state_path(self.work, "demo"), self.st)
        helpers.write(os.path.join(self.folder, "handoff.md"), "Unpushed work")
        data = snapshot.build()
        row = data["rows"][0]
        self.assertEqual(row["status"], "waiting-owner")
        self.assertIn("go", row["actions"])
        self.assertIn("local changes not published", row["flags"])
        self.assertEqual(row["seen"]["status"], "waiting-owner")
        detail = snapshot.feature(self.work, "demo")
        self.assertEqual(detail["local_handoff"]["text"], "Unpushed work")
        self.assertFalse(detail["local_handoff"]["published"])

    def test_github_failures_are_per_feature(self):
        self.st.update(stage="build", status="ready-to-merge", pr=7)
        self.save()
        for error in (FileNotFoundError("gh missing"), RelayError("logged out"), RelayError("offline")):
            with mock.patch("relaylib.gitops.gh_json", side_effect=error):
                data = snapshot.build()
            row = data["rows"][0]
            self.assertEqual(row["ci"], "unknown")
            self.assertEqual(row["pr_info"]["state"], "unknown")
            self.assertNotIn("merge", row["actions"])
            self.assertIn(str(error), row["action_reasons"]["merge"])
            self.assertIn("usage", data)

    def test_unreadable_repo_survives_enrichment(self):
        os.makedirs(os.path.join(self.tmp, "bad"))
        _, bad = helpers.make_repo(os.path.join(self.tmp, "bad"))
        real = state.list_features
        def fail(repo):
            if os.path.realpath(repo) == os.path.realpath(bad):
                raise PermissionError("cannot read repo state")
            return real(repo)
        with mock.patch("relaylib.status.checkouts", return_value=[self.work, bad]), \
                mock.patch("relaylib.state.list_features", side_effect=fail):
            rows = snapshot.build()["rows"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(any("cannot read repo state" in " ".join(row["flags"]) for row in rows))
        self.assertTrue(any(row["feature"] == "demo" for row in rows))

    def test_reviews_and_files_from_branch_not_checked_out(self):
        for i, flags in enumerate(({}, {"skipped_reviewers": ["out"]}, {"same_provider": True}, {"confirmation": True}), 1):
            commands.write_md(os.path.join(self.folder, "reviews", f"spec-{i}.codex.md"),
                              {"at": f"2026-09-2{i}T12:00:00Z", "round": i, "reviewer": "codex",
                               "model": "test", "effort": "medium", **flags}, helpers.verdict_block("GO"))
        self.st["owner_actions"] = [{"action": "override go", "relayed_by": "claude session s2"}]
        self.save()
        commit = gitops.head_sha(self.work)
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        detail = snapshot.feature(self.work, "demo")
        self.assertEqual([r["round"] for r in detail["reviews"]], [4, 3, 2, 1])
        self.assertTrue(detail["reviews"][0]["confirmation"])
        self.assertTrue(detail["reviews"][1]["same_provider"])
        self.assertTrue(detail["reviews"][2]["fallback"])
        self.assertEqual(len(detail["timeline"]), 5)
        self.assertIn("relayed_by", detail["owner_actions"][0])
        path = detail["reviews"][0]["path"]
        self.assertIn("verdict: GO", snapshot.read_file(self.work, "demo", commit, path))
        for bad in ("README.md", "docs/relay/demo/../other/state.md", "/etc/passwd", "docs/relay/demo2/state.md"):
            with self.assertRaises(RelayError):
                snapshot.read_file(self.work, "demo", commit, bad)
        with self.assertRaises(RelayError):
            snapshot.read_file(self.work, "demo", "HEAD", path)

    def test_usage_and_ledger_groups(self):
        now = time.time()
        usage.append({"provider": "claude", "used_pct": 20, "sampled_at": now, "resets_at": now + 86400})
        ledger.append({"repo": "project", "model": "model", "provider": "claude", "input": 100,
                       "cached": 40, "output": 25, "duration_s": 120})
        result = snapshot.usage()
        self.assertEqual(result["providers"]["claude"]["weekly"]["used_pct"], 20)
        for days in ("7", "30"):
            total = result["ledger"][days]["repos"][0]
            self.assertEqual((total["runs"], total["cached_share"], total["minutes"]), (1, 40, 2))
            self.assertEqual(result["ledger"][days]["models"][0]["name"], "claude:model")

    def test_usage_carries_the_five_hour_window(self):  # five-hour-usage R5
        now = time.time()
        reset = int(now) + 3600
        helpers.write(os.path.join(self.tmp, "codex", "sessions", "2026", "11", "08", "rollout-x.jsonl"), json.dumps(
            {"timestamp": now, "payload": {"rate_limits": {"primary": {
                "used_percent": 40, "window_minutes": 300, "resets_at": reset}}}}) + "\n")
        helpers.write(os.path.join(self.tmp, "relayhome", "claude-usage.json"), json.dumps(
            {"sampled_at": now, "five_hour": {"used_pct": 12, "resets_at": reset},
             "seven_day": {"used_pct": 30, "resets_at": reset + 86400}}))
        providers = snapshot.usage()["providers"]
        self.assertEqual((providers["codex"]["five_hour"]["state"], providers["codex"]["five_hour"]["used_pct"]),
                         ("current", 40))
        self.assertEqual((providers["claude"]["five_hour"]["state"], providers["claude"]["five_hour"]["used_pct"]),
                         ("current", 12))
        for value in providers.values():
            self.assertEqual(set(value), {"weekly", "blocked", "reason", "budget", "five_hour"})

    def test_page_shows_the_five_hour_window(self):  # five-hour-usage D4, R6
        with open(os.path.join(os.path.dirname(snapshot.__file__), "page.html")) as f:
            page = f.read()
        for text in ("5-hour window", "of 5-hour limit", "Reset since last sample", "5-hour usage not available",
                     "Stale sample", "Reset time unknown", "Sample time unknown", "'5h '",
                     "fiveHour(value.five_hour)", "fiveShort(value.five_hour)"):
            self.assertIn(text, page)

    def test_done_feature_remains_readable_after_its_branch_is_deleted(self):
        self.st.update(stage="done", status="done")
        self.save()
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        helpers.sh(self.work, "git", "merge", "-q", "--ff-only", "feat/demo")
        helpers.sh(self.work, "git", "push", "-q", "origin", "develop")
        helpers.sh(self.work, "git", "push", "-q", "origin", "--delete", "feat/demo")
        detail = snapshot.feature(self.work, "demo")
        self.assertEqual(detail["state"]["status"], "done")
        self.assertEqual(detail["actions"], [])

    def test_merged_feature_skips_github_after_the_first_build(self):
        gitops._MERGED_PRS.clear()
        self.addCleanup(gitops._MERGED_PRS.clear)
        self.st.update(stage="build", status="ready-to-merge", pr=7)
        self.save()
        reply = {"state": "MERGED", "number": 7, "baseRefName": "develop", "headRefOid": "a" * 40,
                 "statusCheckRollup": []}
        with mock.patch("relaylib.gitops.gh_json", return_value=reply) as gh:
            first = snapshot.build()
            asked = gh.call_count
            second = snapshot.build()
        self.assertGreater(asked, 0)
        self.assertEqual(gh.call_count, asked)  # the second build asks GitHub nothing
        row = second["rows"][0]
        self.assertEqual(row["stage"], "done")
        self.assertEqual(row["pr_info"]["state"], "MERGED")
        self.assertEqual(row["actions"], [])
        self.assertEqual(first["rows"][0]["pr_info"]["state"], "MERGED")
        self.assertFalse(any(c.args[1][:1] == ["api"] for c in gh.call_args_list))  # no CI lookups once merged

    def test_linked_worktrees_share_one_fetch_and_its_result(self):
        linked = os.path.join(self.tmp, "linked")
        helpers.sh(self.work, "git", "worktree", "add", "-q", "-b", "side", linked)
        with mock.patch("relaylib.gitops.fetch", side_effect=RelayError("remote hung up")) as fetch:
            data = snapshot.build()
        self.assertEqual(fetch.call_count, 1)
        row = data["rows"][0]
        self.assertIn("remote hung up", row["flags"])
        self.assertEqual(row["actions"], [])

    def test_writing_is_the_summary_or_its_error(self):
        small = {"7": {"features": []}, "30": {"features": []}, "unreadable": [], "notes": []}
        with mock.patch.object(writerusage, "summary", return_value=small) as summary:
            data = snapshot.build()
        self.assertEqual(data["writing"], small)
        self.assertEqual(os.path.realpath(summary.call_args.args[0][0]), os.path.realpath(self.work))
        with mock.patch.object(writerusage, "summary", side_effect=OSError("x")):
            data = snapshot.build()
        self.assertEqual(data["writing"], {"error": "x"})
        self.assertEqual(len(data["rows"]), 1)                     # the rest of the snapshot is intact

    def test_writing_notes_a_failed_fetch(self):
        with mock.patch("relaylib.gitops.fetch", side_effect=RelayError("remote hung up")):
            data = snapshot.build()
        self.assertIn(f"fetch failed for {os.path.basename(self.work)}: writing sessions use the last fetched history",
                      data["writing"]["notes"])

    def test_build_reads_each_worktree_list_once(self):
        with mock.patch.object(gitops, "run", wraps=gitops.run) as spy:
            snapshot.build()
        lists = [c for c in spy.call_args_list if c.args[0][:3] == ["git", "worktree", "list"]]
        self.assertEqual(len(lists), len({os.path.realpath(c.args[1]) for c in lists}))

    def test_a_ci_run_github_never_started_is_not_green(self):                    # ci-on-submit R9
        self.st.update(stage="build", status="ready-to-merge", pr=7)
        self.save()
        head = helpers.sh(self.work, "git", "rev-parse", "HEAD").strip()
        reply = {"state": "OPEN", "number": 7, "baseRefName": "develop", "headRefOid": head, "statusCheckRollup": []}
        with mock.patch("relaylib.gitops.gh_json", return_value=reply), \
                mock.patch("relaylib.gitops.ci_for_code", return_value="not-started"), \
                mock.patch("relaylib.freshness.check", return_value=(True, "")):
            detail = snapshot.feature(self.work, "demo")
        self.assertEqual(detail["ci"], "not-started")
        self.assertNotIn("merge", detail["actions"])
        self.assertIn("billing", detail["action_reasons"]["merge"])
        with open(os.path.join(os.path.dirname(snapshot.__file__), "page.html")) as f:
            self.assertIn("not started by GitHub (billing)", f.read())

    def test_local_mode_detail_shows_the_tests_flag_not_github_ci(self):          # ci-modes N10
        self.st.update(stage="build", status="ready-to-merge", pr=7)
        self.save()
        head = helpers.sh(self.work, "git", "rev-parse", "HEAD").strip()
        reply = {"state": "OPEN", "number": 7, "baseRefName": "develop", "headRefOid": head, "statusCheckRollup": []}
        with mock.patch("relaylib.gitops.gh_json", return_value=reply), \
                mock.patch("relaylib.status.tests_flag", return_value="tests: local, failed at abc12345"), \
                mock.patch("relaylib.gitops.ci_for_code", side_effect=AssertionError("GitHub CI was read")), \
                mock.patch("relaylib.owneractions.merge_readiness", side_effect=RelayError("tests failed for abc12345")), \
                mock.patch("relaylib.freshness.check", return_value=(True, "")):
            detail = snapshot.feature(self.work, "demo")
        self.assertEqual(detail["action_reasons"]["merge"], "tests failed for abc12345")
        self.assertEqual(detail["tests"], "tests: local, failed at abc12345")
        with open(os.path.join(os.path.dirname(snapshot.__file__), "page.html")) as f:
            self.assertIn("d.tests||", f.read())

    def test_request_review_is_offered_for_an_open_ready_build_and_hidden_while_one_runs(self):
        self.st.update(stage="build", status="ready-to-merge", pr=7)
        self.save()
        head = helpers.sh(self.work, "git", "rev-parse", "HEAD").strip()
        reply = {"state": "OPEN", "number": 7, "baseRefName": "develop", "headRefOid": head, "statusCheckRollup": []}
        with mock.patch("relaylib.gitops.gh_json", return_value=reply), \
                mock.patch("relaylib.gitops.ci_for_code", return_value="green"):
            detail = snapshot.feature(self.work, "demo")
            self.assertIn("review", detail["actions"])
            self.assertIn(detail["review_default"]["id"], [c["id"] for c in detail["review_choices"]])
            self.assertIsNone(detail["review_job"])
            helpers.write(os.path.join(state.relay_dir(self.work), "config.toml"),
                          '[review.prefer]\ncodex = ["claude:claude-repo-only"]\n')
            ids = [c["id"] for c in snapshot.feature(self.work, "demo")["review_choices"]]
            self.assertIn("claude:claude-repo-only", ids)                   # this repository's own table
            lock = reviewjobs.JobLock(self.work, "demo")
            lock.acquire()
            self.addCleanup(lock.release)
            lock.write(reviewer="codex:gpt-6-astra", started_at=1.0, pid=os.getpid(), state="running", message="")
            detail = snapshot.feature(self.work, "demo")
            self.assertEqual(detail["actions"], [])
            self.assertEqual(detail["review_job"]["state"], "running")
        with mock.patch("relaylib.gitops.gh_json", return_value=dict(reply, state="CLOSED")):
            lock.release()
            self.assertNotIn("review", snapshot.feature(self.work, "demo")["actions"])

    def hook(self, name, **extra):
        sessions.capture("codex", io.StringIO(json.dumps(dict({"session_id": "s1", "hook_event_name": name,
                                                                 "cwd": self.work}, **extra))))

    def stopped(self, age_s=600, said=None):
        """Codex session s1 stopped age_s seconds ago, its rollout ending with `said`."""
        self.hook("Stop")
        record_path = sessions.record_path("codex", "s1")
        data = json.load(open(record_path))
        data["since"] = data["at"] = time.time() - age_s
        json.dump(data, open(record_path, "w"))
        if said is not None:
            helpers.write(os.path.join(self.tmp, "codex", "sessions", "2026", "10", "07", "rollout-2026-10-07T10-00-00-s1.jsonl"),
                          json.dumps({"type": "event_msg", "payload": {"type": "task_complete",
                                                                       "last_agent_message": said}}) + "\n")

    def other(self, sid, age_s=2 * 3600, said="Which key should I use?", cwd=None, state_="waiting", owner=False):
        """A codex session sid outside any feature, its rollout ending with `said`; owner=True makes it one the
        owner started in a terminal (where-i-left-off D1)."""
        os.makedirs(sessions.folder(), exist_ok=True)
        at = time.time() - age_s
        with open(sessions.record_path("codex", sid), "w") as f:
            json.dump({"provider": "codex", "session_id": sid, "cwd": cwd, "state": state_, "since": at, "at": at,
                       "event": "Stop", "pending": []}, f)
        start = json.dumps({"type": "session_meta", "payload": {"originator": "codex-tui", "source": "cli"}}) + "\n"
        helpers.write(os.path.join(self.tmp, "codex", "sessions", "2026", "10", "08", f"rollout-2026-10-08T10-00-00-{sid}.jsonl"),
                      (start if owner else "") + json.dumps({"type": "event_msg", "payload": {
                          "type": "task_complete", "last_agent_message": said}}) + "\n")

    def test_other_sessions_in_the_snapshot(self):
        self.assertEqual(snapshot.build()["other_sessions"], [])
        self.st.update(stage="build", status="drafting")
        self.save()
        self.stopped(said="the feature's own question")
        self.other("o1", cwd=os.path.realpath(self.work))
        rows_before = snapshot.build()["rows"]
        data = snapshot.build()
        self.assertEqual([(e["session_id"], e["label"]) for e in data["other_sessions"]], [("o1", "work")])
        self.assertEqual(data["other_sessions"][0]["excerpt"], {"source": "agent", "text": "Which key should I use?"})
        self.assertEqual([r["feature"] for r in data["rows"]], [r["feature"] for r in rows_before])
        helpers.write(os.path.join(os.environ["RELAY_HOME"], "config.toml"), "[ui]\nother_sessions_hours = 500\n")
        self.assertIn("ignored invalid [ui] other_sessions_hours", snapshot.build()["notes"])

    def test_a_feature_whose_details_fail_still_claims_its_session(self):
        self.st.update(stage="build", status="drafting")
        self.save()
        self.stopped(said="the feature's own question")
        with mock.patch("relaylib.ui.snapshot.feature", side_effect=RelayError("branch not published")):
            data = snapshot.build()
        self.assertEqual([r["feature"] for r in data["rows"]], ["demo"])
        self.assertEqual(data["other_sessions"], [])

    def test_other_sessions_use_the_global_settings(self):
        pd = os.path.join(self.tmp, "proteindiary")  # a repository with no feature, and its own [ui] values
        os.makedirs(pd)
        helpers.sh(pd, "git", "init", "-q", "-b", "develop")
        helpers.write(os.path.join(state.relay_dir(pd), "config.toml"),
                      "[ui]\nhealth_grace_minutes = 60\nother_sessions_hours = 1\n")
        self.other("near", age_s=600, cwd=os.path.realpath(pd))
        self.other("far", age_s=5 * 3600, cwd=os.path.realpath(pd))
        self.assertEqual([e["session_id"] for e in snapshot.build()["other_sessions"]], ["near", "far"])

    def test_one_other_session_with_its_full_message(self):
        self.other("o1", said="Long question. " * 400)
        data = snapshot.build()
        one = snapshot.other_session(data, "codex", "o1")
        self.assertEqual((one["label"], one["state"], one["pending_tools"], one["resume"], one["source"]),
                         ("Unknown folder", "waiting", [], "codex resume o1", "agent"))
        self.assertEqual(len(one["text"]), 4000)
        self.assertIsNone(snapshot.other_session(data, "codex", "s1"))
        self.assertIsNone(snapshot.other_session(data, "claude", "o1"))
        for path in glob.glob(os.path.join(self.tmp, "codex", "sessions", "*", "*", "*", "*o1.jsonl")):
            os.remove(path)
        gone = snapshot.other_session(data, "codex", "o1")
        self.assertEqual((gone["text"], gone["source"]), (None, None))

    def left(self, data):
        return [(p["project"], [(e["session_id"], e["feature"]) for e in p["sessions"]]) for p in data["left_off"]]

    def test_left_off_in_the_snapshot(self):
        self.assertEqual(snapshot.build()["left_off"], [])
        self.other("s1", age_s=600, cwd=os.path.realpath(self.work), owner=True)  # the feature's owner session
        self.other("auto", age_s=60, cwd=os.path.realpath(self.work), state_="ended")  # an automated run
        self.other("o1", age_s=1200, state_="ended", owner=True)
        data = snapshot.build()
        self.assertEqual(self.left(data), [("work", [("s1", {"slug": "demo", "done": False})]),
                                           ("Unknown folder", [("o1", None)])])
        entry = data["left_off"][0]["sessions"][0]
        self.assertEqual((entry["resume"], data["left_off"][0]["more"]),
                         (f"cd ~/{os.path.basename(self.work)} && codex resume s1", 0))
        one = snapshot.other_session(data, "codex", "o1")
        self.assertEqual((one["label"], one["state"], one["resume"]), ("Unknown folder", "ended", "codex resume o1"))
        self.assertEqual(one["text"], "Which key should I use?")
        self.assertIsNone(snapshot.other_session(data, "codex", "auto"))

    def test_a_health_matched_session_is_marked_on_the_dashboard(self):
        self.st.update(stage="build", status="drafting", owner={"provider": "codex", "session": "gone"})
        self.save()
        self.other("m1", age_s=600, cwd=os.path.realpath(self.work), owner=True)
        data = snapshot.build()
        self.assertEqual(data["features"][0]["session_record"]["session_id"], "m1")
        self.assertEqual(self.left(data), [("work", [("m1", {"slug": "demo", "done": False})])])

    def test_left_marks_from_details(self):
        def detail(slug, sid, stage="build", pr=None, updated="2026-10-01", matched=None):
            return {"feature": slug, "state": {"stage": stage, "updated": updated}, "pr_info": {"state": pr} if pr else {},
                    "owner_session": {"session": sid}, "session_record": {"session_id": matched} if matched else None}
        marks = snapshot._left_marks([detail("merged", "A", pr="MERGED"), detail("done", "B", stage="done"),
                                      detail("old-done", "C", stage="done", updated="2026-10-09"),
                                      detail("live", "C"), detail("a1", "D", stage="done"),
                                      detail("a2", "D", stage="done", updated="2026-10-02"),
                                      detail("matched", "E", matched="F")])
        self.assertEqual(marks, {"A": {"slug": "merged", "done": True}, "B": {"slug": "done", "done": True},
                                 "C": {"slug": "live", "done": False}, "D": {"slug": "a2", "done": True},
                                 "E": {"slug": "matched", "done": False}, "F": {"slug": "matched", "done": False}})

    def test_running_in_the_snapshot_and_api(self):
        self.other("s1", age_s=30, cwd=os.path.realpath(self.work), state_="working", owner=True,
                   said="Running the suite.")
        mid_turn = mock.patch.object(snapshot.leftoff.agentask, "codex_turn",  # its turn is still going
                                     return_value={"marker": "started", "mtime": time.time()})
        mid_turn.start()
        self.addCleanup(mid_turn.stop)
        self.other("o1", age_s=1200, state_="ended", owner=True)
        with mock.patch.object(sessions, "alive", return_value=None):
            data = snapshot.build()
        self.assertEqual([(e["session_id"], e["project"], e["feature"]) for e in data["running"]],
                         [("s1", "work", {"slug": "demo", "done": False})])
        self.assertEqual(self.left(data), [("Unknown folder", [("o1", None)])])  # running is not left off
        one = snapshot.other_session(data, "codex", "s1")
        self.assertEqual((one["label"], one["state"], one["text"], one["resume"]),
                         ("work", "working", "Running the suite.",
                          f"cd ~/{os.path.basename(self.work)} && codex resume s1"))
        self.assertIsNone(snapshot.other_session(data, "codex", "nobody"))
        with mock.patch.object(snapshot.leftoff, "running", side_effect=RuntimeError("boom")), \
                mock.patch.object(sessions, "alive", return_value=None):
            data = snapshot.build()
        self.assertEqual((data["running"], [r["feature"] for r in data["rows"]]), ([], ["demo"]))
        with mock.patch.object(sessions, "alive", side_effect=RuntimeError("boom")):
            self.assertEqual([e["session_id"] for e in snapshot.build()["running"]], ["s1"])

    def test_a_codex_turn_that_ended_is_left_off_not_running(self):
        self.other("s1", age_s=30, cwd=os.path.realpath(self.work), state_="working", owner=True,
                   said="Running the suite.")
        real, calls = snapshot.leftoff.codex_turns, []

        def spy(*a):
            calls.append(a)
            return real(*a)
        ended = {"marker": "ended", "mtime": time.time()}
        with mock.patch.object(sessions, "alive", return_value=None), \
                mock.patch.object(snapshot.leftoff.agentask, "codex_turn", return_value=ended), \
                mock.patch.object(snapshot.leftoff, "codex_turns", side_effect=spy):
            data = snapshot.build()
        self.assertEqual(len(calls), 1)
        self.assertEqual(data["running"], [])
        self.assertEqual(self.left(data), [("work", [("s1", {"slug": "demo", "done": False})])])
        with mock.patch.object(sessions, "alive", return_value=None), \
                mock.patch.object(snapshot.leftoff, "codex_turns", side_effect=RuntimeError("boom")):
            self.assertEqual([e["session_id"] for e in snapshot.build()["running"]], ["s1"])

    def test_listed_sessions_carry_their_notes(self):
        from relaylib import notes
        self.other("w1", age_s=600)                                               # waiting: other sessions
        self.other("o1", age_s=1200, state_="ended", owner=True)                  # left off
        notes.send("codex", "w1", "check the logs", None)
        notes.send("codex", "o1", "when you are back", None)
        with mock.patch.object(sessions, "alive", return_value=None):
            data = snapshot.build()
        [waiting] = [e for e in data["other_sessions"] if e["session_id"] == "w1"]
        self.assertEqual([n["text"] for n in waiting["notes"]], ["check the logs"])
        [left] = [e for p in data["left_off"] for e in p["sessions"] if e["session_id"] == "o1"]
        self.assertEqual([(n["text"], n["status"]) for n in left["notes"]], [("when you are back", "queued")])

    def test_left_off_failures(self):
        self.other("s1", age_s=600, cwd=os.path.realpath(self.work), owner=True)
        with mock.patch.object(status, "scan_with_claims", side_effect=RuntimeError("boom")):
            data = snapshot.build()
        self.assertEqual((data["rows"], data["features"], data["other_sessions"]), ([], [], []))
        self.assertIn("Features could not be read: boom", data["notes"])
        self.assertEqual(self.left(data), [("work", [("s1", None)])])
        with mock.patch.object(snapshot, "_left_marks", side_effect=RuntimeError("boom")):
            self.assertEqual(self.left(snapshot.build()), [("work", [("s1", None)])])
        with mock.patch.object(snapshot.leftoff, "projects", side_effect=RuntimeError("boom")):
            data = snapshot.build()
        self.assertEqual((data["left_off"], [r["feature"] for r in data["rows"]]), ([], ["demo"]))
        with mock.patch.object(sessions, "read_records", side_effect=RuntimeError("boom")):
            data = snapshot.build()
        self.assertEqual((data["left_off"], [r["feature"] for r in data["rows"]]), ([], ["demo"]))

    def test_asks_excerpt_and_full_text_live_only_in_memory(self):
        question = "PURPLE-GIRAFFE should I start the build now? " + "Details follow. " * 30
        self.st.update(stage="build", status="drafting")
        self.save()
        self.stopped(said=question)
        snap = snapshot.build()
        row = snap["rows"][0]
        self.assertEqual(row["asks"], [{"kind": "answer", "text": "Answer the codex session", "since": row["wait_since"],
                                        "session": "codex:s1"}])
        self.assertTrue(row["waiting_on_owner"])
        self.assertEqual(row["excerpt"]["source"], "agent")
        self.assertTrue(row["excerpt"]["text"].startswith("PURPLE-GIRAFFE should I") and row["excerpt"]["text"].endswith("…"))
        detail = snapshot.feature(self.work, "demo")
        self.assertEqual(detail["agent_text"], {"source": "agent", "text": question})
        with mock.patch("sys.stdout", io.StringIO()):
            commands.main(["status", "--json"])
        for dirpath, _, files in os.walk(os.environ["RELAY_HOME"]):
            for name in files:
                with open(os.path.join(dirpath, name), "rb") as f:
                    self.assertNotIn(b"PURPLE-GIRAFFE", f.read(), name)

    def test_a_dead_session_asks_nothing_in_the_feature_detail(self):
        self.st.update(stage="build", status="drafting")
        self.save()
        self.stopped(said="Approve this?")
        record_path = sessions.record_path("codex", "s1")
        data = json.load(open(record_path))
        data.update(state="permission", process={"pid": 7, "started": "Thu Oct  8 15:13:49 2026"},
                    pending=[{"tool_use_id": None, "tool_name": "AskUserQuestion", "input_sha1": "x"}])
        json.dump(data, open(record_path, "w"))
        with mock.patch.object(sessions, "alive", return_value={"s1"}):
            self.assertEqual(snapshot.feature(self.work, "demo")["pending_tools"], ["AskUserQuestion"])
        with mock.patch.object(sessions, "alive", return_value=set()):
            detail = snapshot.feature(self.work, "demo")
            row = snapshot.build()["rows"][0]
        self.assertEqual(detail["pending_tools"], [])
        self.assertTrue(detail["health"]["text"].startswith("session stopped"))
        self.assertEqual([a["kind"] for a in row["asks"]], ["check"])

    def test_a_review_error_card_can_open_the_review_dialog(self):
        self.st.update(stage="build", status="review-error", pr=7)
        self.save()
        cfg = os.path.join(os.environ["RELAY_HOME"], "config.toml")
        helpers.write(cfg, '[review]\ncodex = ["claude:claude-opus-5-5"]\n')
        row = snapshot.build()["rows"][0]
        detail = snapshot.feature(self.work, "demo")
        self.assertEqual([a["kind"] for a in row["asks"]][:1], ["review-failed"])
        self.assertTrue(row["review_choices"])                       # actions() disables the button without them
        self.assertEqual((row["review_choices"], row["review_default"]),
                         (detail["review_choices"], detail["review_default"]))

    def test_rows_offer_re_reviews_with_a_default_per_stage(self):
        self.st.update(stage="build", status="drafting", verdicts={"spec": "GO", "plan": "GO"},
                       authors={"spec": "claude", "plan": "codex", "build": "codex"})
        self.save()
        helpers.write(os.path.join(os.environ["RELAY_HOME"], "config.toml"),
                      '[review.prefer]\nclaude = ["codex:gpt-6-astra"]\ncodex = ["claude:claude-opus-5-5"]\n')
        row = snapshot.build()["rows"][0]
        self.assertIn("review-spec", row["actions"])
        self.assertIn("review-plan", row["actions"])
        self.assertEqual(row["review_defaults"]["spec"]["id"], "codex:gpt-6-astra")
        self.assertEqual(row["review_defaults"]["plan"]["id"], "claude:claude-opus-5-5")
        self.assertEqual(snapshot.feature(self.work, "demo")["review_defaults"], row["review_defaults"])
        self.assertEqual(row["asks"], [])                                  # never suggested (D7)

    def test_a_missing_log_keeps_the_ask_without_words(self):
        self.st.update(stage="build", status="drafting")
        self.save()
        self.stopped()
        row = snapshot.build()["rows"][0]
        self.assertEqual([a["kind"] for a in row["asks"]], ["answer"])
        self.assertIsNone(row["excerpt"])
        self.assertIsNone(snapshot.feature(self.work, "demo")["agent_text"])

    def test_dashboard_and_status_agree_and_row_order_is_unchanged(self):
        other = os.path.join(self.tmp, "zeta")
        os.makedirs(other)
        _, zeta = helpers.make_repo(other)
        helpers.sh(zeta, "git", "switch", "-qc", "feat/older")
        st = state.new_state("older", "zeta", {"provider": "claude", "session": "z1"}, "feat/older")
        st.update(stage="plan", status="waiting-owner")
        with mock.patch.object(state, "now_iso", return_value="2026-10-01T09:00:00-04:00"):
            state.write_state(state.state_path(zeta, "older"), st)      # stopped days ago
        helpers.sh(zeta, "git", "add", ".")
        helpers.sh(zeta, "git", "commit", "-qm", "fixture")
        helpers.sh(zeta, "git", "push", "-qu", "origin", "HEAD")
        self.st.update(stage="build", status="drafting")
        self.save()
        self.stopped(age_s=120)                                    # newer than zeta's wait, but first by repo
        with mock.patch("relaylib.status.checkouts", return_value=[self.work, zeta]):
            snap = snapshot.build()
            scanned = status.scan(self.tmp)
        waiting_snap = sorted(r["feature"] for r in snap["rows"] if r["waiting_on_owner"])
        self.assertEqual(waiting_snap, sorted(r["feature"] for r in scanned if r["waiting_on_owner"]))
        self.assertEqual(waiting_snap, ["demo", "older"])
        self.assertEqual([r["feature"] for r in snap["rows"]], [r["feature"] for r in scanned])   # R11: same order
        self.assertEqual([r["feature"] for r in snap["rows"]], ["demo", "older"])                # repo order, not wait
        self.assertGreater(snap["rows"][0]["wait_since"], snap["rows"][1]["wait_since"])         # demo waited less

    def test_missing_hook_records_keep_state_and_activity_asks(self):
        os.makedirs(sessions.folder(), exist_ok=True)                 # setUp's feature: spec, waiting-owner
        helpers.write(sessions.record_path("codex", "s1"), "{not json")
        old = time.time() - 3 * 86400
        with mock.patch("relaylib.health.activity", return_value={"last_activity": old, "unpushed": None, "origin": None}):
            row = snapshot.build()["rows"][0]
        self.assertEqual([a["kind"] for a in row["asks"]], ["decide", "check"])

    def test_an_unreadable_repo_row_asks_to_be_fixed(self):
        os.makedirs(os.path.join(self.tmp, "bad"))
        _, bad = helpers.make_repo(os.path.join(self.tmp, "bad"))
        real = state.list_features
        def fail(repo):
            if os.path.realpath(repo) == os.path.realpath(bad):
                raise PermissionError("cannot read repo state")
            return real(repo)
        with mock.patch("relaylib.status.checkouts", return_value=[self.work, bad]), \
                mock.patch("relaylib.state.list_features", side_effect=fail):
            rows = snapshot.build()["rows"]
        row = next(r for r in rows if r["feature"] == "?")
        self.assertEqual([a["kind"] for a in row["asks"]], ["error"])
        self.assertIn("cannot read repo state", row["asks"][0]["text"])
        self.assertIsNone(row["wait_since"])
        self.assertIsNone(row["excerpt"])
        self.assertTrue(row["waiting_on_owner"])

    def test_one_session_waits_once_after_the_rows_get_their_asks(self):
        self.st["status"] = "drafting"
        self.save()
        self.hook("Stop")
        record_path = sessions.record_path("codex", "s1")
        with open(record_path) as f:
            data = json.load(f)
        data["since"] = data["at"] = time.time() - 600            # stopped ten minutes ago
        with open(record_path, "w") as f:
            json.dump(data, f)
        seen = []
        real = snapshot.waiting.one_ask_per_session
        def spy(rows):
            seen.append([list(r.get("asks") or []) for r in rows])  # every row is enriched before the merge
            return real(rows)
        with mock.patch.object(snapshot.waiting, "one_ask_per_session", side_effect=spy):
            snap = snapshot.build()
        self.assertEqual(len(seen), 1)  # once, after enrichment (the scan's rows stay whole for failed details)
        self.assertEqual(seen[0], [r["asks"] for r in snap["rows"]])
        self.assertEqual([a["kind"] for a in snap["rows"][0]["asks"]], ["answer"])

    def two_features_one_session(self):
        """A second feature, "other", held by the same session s1, which waits on the owner."""
        self.st["status"] = "drafting"
        other = state.new_state("other", "project", {"provider": "codex", "session": "s1"}, "feat/demo")
        other.update(stage="spec", status="drafting")
        state.write_state(state.state_path(self.work, "other"), other)
        self.save()
        self.stopped()

    def test_one_session_holding_two_features_waits_once_in_the_dashboard(self):
        self.two_features_one_session()
        rows = {r["feature"]: r for r in snapshot.build()["rows"]}
        answers = {f: [a["text"] for a in r["asks"] if a["kind"] == "answer"] for f, r in rows.items()}
        self.assertEqual(sorted(len(v) for v in answers.values()), [0, 1])
        kept = next(f for f, v in answers.items() if v)
        self.assertEqual(answers[kept], ["Answer the codex session · also for " + ({"demo", "other"} - {kept}).pop()])

    def test_a_feature_that_fails_to_load_keeps_the_wait_when_the_other_is_done(self):
        self.two_features_one_session()
        self.st.update(stage="done", status="done")
        self.save()
        real = snapshot.feature
        def fail_other(repo, slug, *a, **k):
            if slug == "other":
                raise RelayError("cannot read other")
            return real(repo, slug, *a, **k)
        with mock.patch.object(snapshot, "feature", side_effect=fail_other):
            rows = {r["feature"]: r for r in snapshot.build()["rows"]}
        self.assertEqual([a["kind"] for a in rows["other"]["asks"]], ["answer"])  # the scan's ask, not merged away
        self.assertEqual(rows["demo"]["asks"], [])

    def test_a_waiting_session_puts_a_drafting_feature_in_the_inbox(self):
        self.st["status"] = "drafting"
        self.save()
        self.hook("Stop")
        record_path = sessions.record_path("codex", "s1")
        data = json.load(open(record_path))
        data["since"] = data["at"] = time.time() - 600            # stopped ten minutes ago
        json.dump(data, open(record_path, "w"))
        snap = snapshot.build()
        row = snap["rows"][0]
        self.assertEqual(row["health"]["text"], "waiting on you 10m")
        self.assertTrue(row["waiting_on_owner"])
        self.assertTrue(row["health_inbox"])
        detail = snapshot.feature(self.work, "demo")
        self.assertEqual(detail["health"]["kind"], "attention")

    def test_an_already_waiting_feature_keeps_its_reason_and_buttons(self):
        self.hook("Stop")                                            # spec / waiting-owner, from setUp
        record_path = sessions.record_path("codex", "s1")
        data = json.load(open(record_path))
        data["since"] = data["at"] = time.time() - 600
        json.dump(data, open(record_path, "w"))
        row = snapshot.build()["rows"][0]
        self.assertTrue(row["waiting_on_owner"])
        self.assertFalse(row["health_inbox"])
        self.assertIn("go", row["actions"])

    def test_activity_only_health_hints_and_invalid_settings(self):
        self.st["status"] = "drafting"
        self.save()
        cfg_dir = state.relay_dir(self.work)
        helpers.write(os.path.join(cfg_dir, "config.toml"), "[ui]\nquiet_minutes = 0\n")
        snap = snapshot.build()
        row = snap["rows"][0]
        self.assertEqual(row["health"]["kind"], "ok")                # just committed: active
        self.assertFalse(row["health_inbox"])
        self.assertIn("ignored invalid [ui] quiet_minutes", snap["notes"])
        self.assertIn("run relay hooks install", snap["session_hints"]["codex"])
        os.makedirs(os.path.join(os.environ["HOME"], ".codex"), exist_ok=True)
        from relaylib import hookinstall
        hookinstall.install_file("codex", hookinstall.path_for("codex"), "s")
        self.assertIn("trust them with /hooks", snapshot.build()["session_hints"]["codex"])
        self.hook("UserPromptSubmit")
        self.assertIsNone(snapshot.build()["session_hints"]["codex"])

    def test_hook_health_survives_activity_failures_and_bad_records(self):
        from relaylib import health
        self.st["status"] = "drafting"
        self.save()
        os.makedirs(sessions.folder(), exist_ok=True)
        with open(os.path.join(sessions.folder(), "bad.json"), "w") as f:
            json.dump({"session_id": "s1", "state": "waiting", "pending": [], "since": 1, "at": 1}, f)   # no provider
        with open(os.path.join(sessions.folder(), "list.json"), "w") as f:
            json.dump({"provider": [], "session_id": "s1", "state": "waiting", "event": "Stop", "cwd": None,
                       "pending": [], "since": 1, "at": 1}, f)
        self.hook("PermissionRequest", tool_name="Bash", tool_input={"command": "x"})
        record_path = sessions.record_path("codex", "s1")
        data = json.load(open(record_path))
        data["since"] = data["at"] = time.time() - 300
        json.dump(data, open(record_path, "w"))
        snap = snapshot.build()                                      # the bad record neither crashes nor counts
        self.assertEqual(snap["rows"][0]["health"]["text"], "needs permission 5m")
        with mock.patch.object(health, "branch_checkouts", side_effect=RuntimeError("worktrees vanished")):
            found, act, _ = health.session_health(self.work, self.st, sessions.read_records(), time.time())
        self.assertEqual(found["text"], "needs permission 5m")
        self.assertIsNone(act["last_activity"])

    def test_a_failed_local_scan_keeps_hook_health(self):
        self.st["status"] = "drafting"
        self.save()
        self.hook("PermissionRequest", tool_name="Bash", tool_input={"command": "x"})
        record_path = sessions.record_path("codex", "s1")
        data = json.load(open(record_path))
        data["since"] = data["at"] = time.time() - 300
        json.dump(data, open(record_path, "w"))
        with mock.patch.object(snapshot, "_local", side_effect=RelayError("git status failed")):
            row = snapshot.build()["rows"][0]
        self.assertEqual(row["health"]["text"], "needs permission 5m")
        self.assertTrue(row["waiting_on_owner"])
        self.assertTrue(any("could not read the local checkout" in f for f in row["flags"]))

    def test_one_broken_config_does_not_stop_the_snapshot(self):
        self.st["status"] = "drafting"
        self.save()
        helpers.write(os.path.join(state.relay_dir(self.work), "config.toml"), "[ui\nnot toml")
        snap = snapshot.build()
        self.assertEqual(len(snap["rows"]), 1)
        self.assertEqual(snap["notes"], [])

    def test_a_merged_feature_has_no_health_and_stays_out_of_the_inbox(self):
        self.st.update(stage="build", status="ready-to-merge", pr=7)   # the branch state still says ready
        self.save()
        self.hook("Stop")
        record_path = sessions.record_path("codex", "s1")
        data = json.load(open(record_path))
        data["since"] = data["at"] = time.time() - 600
        json.dump(data, open(record_path, "w"))
        reply = {"state": "MERGED", "number": 7, "baseRefName": "develop", "headRefOid": "a" * 40,
                 "statusCheckRollup": []}
        with mock.patch("relaylib.gitops.gh_json", return_value=reply):
            row = snapshot.build()["rows"][0]
            detail = snapshot.feature(self.work, "demo")
        self.assertEqual(row["stage"], "done")
        self.assertIsNone(row["health"])
        self.assertFalse(row["health_inbox"])
        self.assertFalse(row["waiting_on_owner"])
        self.assertIsNone(detail["health"])

    def test_a_closed_pr_leaves_the_inbox_and_is_listed_as_hidden(self):  # merge-and-tidy D12
        self.st.update(stage="build", status="ready-to-merge", pr=7)
        self.save()
        reply = {"state": "CLOSED", "number": 7, "baseRefName": "develop", "headRefOid": "a" * 40,
                 "statusCheckRollup": []}
        with mock.patch("relaylib.gitops.gh_json", return_value=reply):
            snap = snapshot.build()
        self.assertFalse(snap["rows"][0]["waiting_on_owner"])
        self.assertEqual(snap["hidden"], [{"repo": os.path.basename(self.work), "slug": "demo",
                                           "reason": "PR #7 was closed without merging"}])
        with open(os.path.join(os.path.dirname(snapshot.__file__), "page.html")) as f:
            page = f.read()
        self.assertIn('id="hidden-features"', page)
        self.assertIn("renderHidden()", page)

    def test_done_and_unheld_features_have_no_health(self):
        self.st.update(stage="done", status="done", owner={})
        self.save()
        row = snapshot.build()["rows"][0]
        self.assertIsNone(row.get("health"))


FACT_READS = (["rev-parse", "--abbrev-ref"], ["rev-parse", "HEAD"], ["remote", "get-url"], ["--git-common-dir"],
              ["worktree", "list"], ["for-each-ref"])


class StoreBase(SnapshotBase):
    """Builds that share one store, as relay ui's do."""

    def setUp(self):
        super().setUp()
        from relaylib import scancache
        self.store = scancache.Store()
        helpers.sh(self.work, "git", "fetch", "-q", "origin")  # a first fetch may add origin/HEAD
        self.calls = []
        real_run, real_cat = gitops.run, gitops.cat_files

        def run(args, *a, **k):
            self.calls.append(list(args))
            return real_run(args, *a, **k)

        def cat(root, specs):
            if specs:  # no specs starts no process
                self.calls.append(["git", "cat-file", "--batch", *specs])
            return real_cat(root, specs)

        for name, fn in (("run", run), ("cat_files", cat)):
            p = mock.patch.object(gitops, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    def build(self):
        self.calls.clear()
        return snapshot.build(store=self.store)

    def reads(self, *words):
        return [c for c in self.calls if c[0] == "git" and all(w in c for w in words)]


class SharedStoreTest(StoreBase):
    """dashboard-scan Task 4."""

    def test_second_build_reads_no_facts_and_no_relay_files(self):
        self.build()
        self.build()
        for words in FACT_READS:
            self.assertEqual(self.reads(*words), [], words)
        for words in (["ls-tree"], ["show"], ["cat-file"]):
            self.assertEqual(self.reads(*words), [], words)

    def test_checkouts_are_listed_once_per_build(self):
        import glob as globmod
        with mock.patch("relaylib.status.glob.glob", wraps=globmod.glob) as spy:
            snapshot.build(store=self.store)
        roots = [c for c in spy.call_args_list if c.args[0] == os.path.join(self.tmp, "*")]
        self.assertEqual(len(roots), 1)

    def test_local_changes_are_seen_by_the_next_build(self):
        self.build()
        self.st["status"] = "drafting"
        self.save()
        self.assertEqual(self.build()["rows"][0]["status"], "drafting")
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        row = self.build()["rows"][0]
        self.assertIn("on origin/feat/demo (not checked out)", row["flags"])
        tree = os.path.join(self.tmp, "tree")
        helpers.sh(self.work, "git", "worktree", "add", "-q", tree, "feat/demo")
        row = self.build()["rows"][0]
        self.assertEqual(os.path.realpath(row["checkout"]), os.path.realpath(tree))

    def test_unreadable_origin_branches_show_an_error_row(self):
        with mock.patch.object(status, "_remote_features", side_effect=RelayError("for-each-ref failed: boom")):
            rows = snapshot.build(store=self.store)["rows"]
        flagged = [r for r in rows if any("cannot read origin branches" in f for f in r["flags"])]
        self.assertEqual([r["feature"] for r in flagged], ["demo"])

    def test_repo_with_no_rows_gets_a_question_row(self):
        os.makedirs(os.path.join(self.tmp, "solo"))
        _, other = helpers.make_repo(os.path.join(self.tmp, "solo"))
        target = os.path.join(self.tmp, "solo-repo")
        os.rename(other, target)
        real = status._remote_features

        def fail(checkout):
            if os.path.realpath(checkout) == os.path.realpath(target):
                raise RelayError("for-each-ref failed: boom")
            return real(checkout)
        with mock.patch.object(status, "_remote_features", side_effect=fail):
            rows = snapshot.build(store=self.store)["rows"]
        mine = [r for r in rows if os.path.realpath(r["checkout"]) == os.path.realpath(target)]
        self.assertEqual([r["feature"] for r in mine], ["?"])
        self.assertIn("boom", mine[0]["flags"][0])


class PublishedTest(StoreBase):
    """feature() inside a build takes the published commit from the scan (dashboard-scan Task 5)."""

    def test_detail_starts_no_branch_walk_of_its_own(self):
        for name in ("old-1", "old-2", "old-3"):  # stacked branches carrying older copies of the state
            helpers.sh(self.work, "git", "push", "-q", "origin", f"HEAD:{name}")
        self.st["status"] = "drafting"
        self.save()
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        with mock.patch("relaylib.owneractions.published", wraps=snapshot.owneractions.published) as walk:
            data = self.build()
        self.assertEqual(walk.call_count, 0)
        self.assertEqual(data["rows"][0]["status"], "drafting")
        self.assertEqual(len(self.reads("for-each-ref")), 1)  # the scan's own read of the origin refs
        self.assertEqual(len(self.reads("ls-tree")), 3)  # develop, the shared older commit, feat/demo
        self.assertEqual(data["features"][0]["seen"]["commit"],
                         helpers.sh(self.work, "git", "rev-parse", "origin/feat/demo").strip())

    def test_detail_endpoint_outside_a_build_walks_the_branches(self):
        with mock.patch("relaylib.owneractions.fingerprint", wraps=snapshot.owneractions.fingerprint) as spy:
            snapshot.feature(self.work, "demo")
        self.assertEqual(spy.call_count, 1)


class DoneReuseTest(StoreBase):
    """Done features cost nothing while their commit is unchanged (dashboard-scan Task 6)."""

    def setUp(self):
        super().setUp()
        from relaylib import merged
        self.merged = merged
        self.st.update(stage="build", status="ready-to-merge", pr=7)
        self.save()
        helpers.sh(self.work, "git", "switch", "-q", "develop")  # merged: nobody has the branch checked out
        merged.mark_merged(self.work, 7)
        self.gh_log = os.path.join(self.tmp, "gh.log")
        info = os.path.join(self.tmp, "pr.json")
        helpers.write(info, json.dumps({"number": 7, "state": "MERGED", "baseRefName": "develop",
                                        "headRefOid": "0" * 40, "statusCheckRollup": [], "isDraft": False,
                                        "mergedAt": "2026-11-01T00:00:00Z"}))
        patch = mock.patch.dict(os.environ, {"RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH),
                                             "FAKE_GH_JSON": info, "FAKE_GH_LOG": self.gh_log})
        patch.start()
        self.addCleanup(patch.stop)

    def gh_calls(self):
        try:
            with open(self.gh_log) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def row(self, data):
        return next(r for r in data["rows"] if r["feature"] == "demo")

    def test_second_build_reuses_a_merged_feature(self):
        first = self.build()
        self.assertEqual(self.row(first)["stage"], "done")
        self.assertIsNone(self.row(first)["seen"]["pr_head"])
        self.assertEqual([c for c in self.gh_calls() if "pr view" in c], ["pr view 7 --json state,mergedAt"])
        os.remove(self.gh_log)
        with mock.patch.object(snapshot, "feature", wraps=snapshot.feature) as spy:
            second = self.build()
        self.assertEqual(spy.call_count, 0)
        self.assertEqual([c for c in self.gh_calls() if "pr view" in c], [])
        for words in (["status", "--porcelain"], ["diff"], ["log"]):
            self.assertEqual(self.reads(*words), [], words)
        self.assertEqual(self.row(second), self.row(first))

    def test_new_commit_on_the_branch_rebuilds_it(self):
        self.build()
        helpers.sh(self.work, "git", "switch", "-q", "feat/demo")
        helpers.write(os.path.join(self.folder, "notes.md"), "later")
        self.save()
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        with mock.patch.object(snapshot, "feature", wraps=snapshot.feature) as spy:
            data = self.build()
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(self.row(data)["seen"]["commit"],
                         helpers.sh(self.work, "git", "rev-parse", "origin/feat/demo").strip())

    def test_checked_out_done_branch_is_built_every_time(self):
        helpers.sh(self.work, "git", "switch", "-q", "feat/demo")
        self.build()
        helpers.write(os.path.join(self.work, "edit.txt"), "x")
        with mock.patch.object(snapshot, "feature", wraps=snapshot.feature) as spy:
            data = self.build()
        self.assertEqual(spy.call_count, 1)
        self.assertIn("local changes not published", self.row(data)["flags"])

    def test_fetch_error_shows_on_a_reused_row(self):
        self.build()
        with mock.patch.object(snapshot, "_fetch", return_value="cannot fetch origin: offline"):
            data = self.build()
            again = self.build()
        self.assertIn("cannot fetch origin: offline", self.row(data)["flags"])
        self.assertEqual(self.row(again)["flags"], self.row(data)["flags"])

    def test_merge_log_is_read_once_across_builds(self):
        from relaylib import scancache
        head = helpers.sh(self.work, "git", "rev-parse", "origin/feat/demo").strip()
        st = {"feature": "demo", "branch": "feat/demo"}
        for _ in range(2):
            with scancache.use(self.store):
                self.merged.merged_at(self.work, st, branch_head=head)
                self.store.prune([self.work])
        self.assertEqual(len(self.reads("log", "--merges")), 1)  # develop; the fixture has no main
        self.calls.clear()
        with scancache.use(self.store):
            self.merged.merged_at(self.work, st, branch_head=head)
        self.assertEqual(self.reads("log", "--merges"), [])

    def test_fingerprint_outside_a_build_asks_github(self):
        self.build()
        with mock.patch("relaylib.gitops.pr_info", return_value={"headRefOid": "1" * 40}) as spy:
            seen = snapshot.owneractions.fingerprint(self.work, "demo", fetch=False)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(seen["pr_head"], "1" * 40)


class SharedReadsTest(StoreBase):
    """The other steps share the build's reads (dashboard-scan Task 7)."""

    def test_writing_totals_start_no_git_on_a_second_build(self):
        snapshot.build(store=self.store)
        from relaylib import scancache
        with scancache.use(self.store), mock.patch.object(gitops, "network_env"):
            self.calls.clear()
            writerusage.summary(status.checkouts(self.tmp))
        self.assertEqual([c for c in self.calls if c[0] == "git"], [])

    def test_ps_runs_once_per_build(self):
        with mock.patch("relaylib.sessions._ps", wraps=sessions._ps) as spy:
            snapshot.build(store=self.store)
        self.assertEqual(len([c for c in spy.call_args_list if c.args[0] == "pid=,lstart="]), 1)

    def test_usage_samples_parse_once_until_the_file_changes(self):
        from relaylib import scancache
        os.makedirs(os.path.join(self.tmp, "relayhome"), exist_ok=True)
        path = os.path.join(self.tmp, "relayhome", "usage.jsonl")
        helpers.write(path, json.dumps({"provider": "codex", "sampled_at": "2026-11-12T00:00:00+00:00",
                                        "resets_at": "2026-11-19T00:00:00+00:00", "used_pct": 10}) + "\n")
        with mock.patch.object(usage, "_read", wraps=usage._read) as spy:
            for _ in range(2):
                with scancache.use(self.store):
                    self.assertEqual(len(usage.read()), 1)
                    usage.read()
            self.assertEqual(spy.call_count, 1)
            with open(path, "a") as f:
                f.write(json.dumps({"provider": "claude", "sampled_at": "2026-11-12T00:00:00+00:00",
                                    "resets_at": "2026-11-19T00:00:00+00:00", "used_pct": 5}) + "\n")
            with scancache.use(self.store):
                self.assertEqual(len(usage.read()), 2)
            self.assertEqual(spy.call_count, 2)

    def test_merged_record_is_parsed_once_per_build(self):
        from relaylib import merged
        merged.mark_merged(self.work, 99)
        self.build()
        with mock.patch.object(merged, "_read_raw", wraps=merged._read_raw) as spy:
            self.build()
        self.assertEqual(spy.call_count, 0)  # unchanged since the last build


class CacheTest(unittest.TestCase):
    def test_loading_success_failure_and_refresh(self):
        gate, called = threading.Event(), threading.Event()
        def build():
            called.set()
            gate.wait(2)
            return {"rows": []}
        cache = snapshot.Cache(builder=build, interval=60)
        self.addCleanup(cache.close)
        self.assertTrue(cache.get()["loading"])
        cache.start()
        self.assertTrue(called.wait(1))
        self.assertTrue(cache.get()["loading"])
        gate.set()
        self.wait_for(lambda: cache.get()["data"] is not None)
        self.assertGreaterEqual(cache.get()["age_seconds"], 0)
        def failed():
            raise RuntimeError("refresh failed")
        cache.builder = failed
        cache.refresh()
        self.wait_for(lambda: cache.get()["error"])
        self.assertEqual(cache.get()["data"], {"rows": []})
        self.assertIn("refresh failed", cache.get()["error"])
        first = snapshot.Cache(builder=failed)
        self.addCleanup(first.close)
        first.start()
        self.wait_for(lambda: first.get()["error"])
        self.assertIsNone(first.get()["data"])
        self.assertFalse(first.get()["loading"])

    def wait_for(self, predicate):
        until = time.monotonic() + 2
        while time.monotonic() < until:
            if predicate():
                return
            time.sleep(.005)
        self.fail("cache did not finish")


class ReviewersTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = temp.name
        self.cfg = os.path.join(self.tmp, "config.toml")
        helpers.write(self.cfg, '[review.prefer]\nclaude = ["codex:gpt-6-astra@high"]\n')
        root = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(root, "proj", ".git"))
        helpers.write(os.path.join(root, "proj", "docs", "relay", "config.toml"), '[review.prefer]\ncodex = ["claude:x"]\n')
        os.makedirs(os.path.join(root, "plain", ".git"))
        patch = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "home"), "RELAY_CONFIG": self.cfg,
                                            "RELAY_ROOT": root, "CODEX_HOME": os.path.join(self.tmp, "codex")})
        patch.start()
        self.addCleanup(patch.stop)
        ledger.append({"at": "2026-10-06T10:00:00-04:00", "provider": "claude", "model": "claude-haiku-4-5",
                       "repo": "x"})

    def test_reviewers_object(self):
        from relaylib import reviewtables
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-2"], "23:59", "owner")
        r = snapshot.reviewers()
        claude = r["authors"]["claude"]
        self.assertEqual([e["id"] for e in claude["entries"]], ["claude:claude-fable-5-2"])
        self.assertEqual([e["id"] for e in claude["permanent"]], ["codex:gpt-6-astra@high"])
        self.assertTrue(claude["until"] and claude["until_label"])
        self.assertRegex(claude["until_iso"], r"T23:59[-+]\d\d:\d\d$")
        self.assertEqual(claude["changed"]["by"], "owner")
        self.assertIsNone(r["authors"]["codex"]["until"])
        self.assertIsNone(r["authors"]["codex"]["changed"])
        known = [k["id"] for k in r["known"]]
        self.assertIn("claude:claude-haiku-4-5", known)                 # from the ledger
        self.assertIn("codex:gpt-6-astra", known)                       # from the config, without effort
        self.assertEqual([(o["repo"], o["authors"]) for o in r["overriding"]], [("proj", ["codex"])])
        self.assertEqual(set(r["writers"]), {"spec", "plan", "build", "audit"})
        self.assertEqual(r["effort"]["effort"], "medium")
        self.assertEqual(r["revision"], reviewtables.revision())
        self.assertEqual(r["problems"], [])

    def test_reviewers_with_no_files_still_has_a_revision(self):
        os.unlink(self.cfg)
        self.assertTrue(snapshot.reviewers()["revision"])

    def test_a_broken_table_does_not_break_the_snapshot(self):
        helpers.write(self.cfg, '[review.prefer]\nclaude = ["gemini:x"]\n')
        self.assertIn("error", snapshot._reviewers_or_error())
