import json
import os
import shlex
import subprocess
import time
import unittest
from unittest import mock

from relaylib import agentask, leftoff, sessions, state, status
from tests import helpers
from tests import test_status

NOW = 1_800_000_000.0


class LeftOffTest(test_status.StatusFixture):
    def setUp(self):
        super().setUp()
        self.home = os.path.realpath(os.path.join(self.tmp, "user"))
        os.makedirs(self.home)
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)

    def rec(self, sid, state_="ended", ago=3600, cwd=None, provider="claude", said="done here", origin=None,
            pending=(), now=NOW):
        """A hook record last written `ago` seconds before now, and a transcript of the given origin."""
        os.makedirs(sessions.folder(), exist_ok=True)
        at = now - ago
        record = {"provider": provider, "session_id": sid, "cwd": cwd, "state": state_, "since": at - 60, "at": at,
                  "event": "Stop", "pending": [{"tool_use_id": None, "tool_name": t, "input_sha1": "x"}
                                               for t in pending]}
        with open(sessions.record_path(provider, sid), "w") as f:
            json.dump(record, f)
        lines = []
        if provider == "claude":
            lines.append(json.dumps({"type": "user", "entrypoint": origin or "cli", "message": {"content": "go"}}))
            if said:
                lines.append(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": said}]}}))
            path = os.path.join(self.tmp, "claude", "projects", "-w", sid + ".jsonl")
        else:
            lines.append(json.dumps({"type": "session_meta", "payload": {"originator": origin or "codex-tui",
                                                                          "source": "cli"}}))
            if said:
                lines.append(json.dumps({"type": "event_msg", "payload": {"type": "task_complete",
                                                                          "last_agent_message": said}}))
            path = os.path.join(self.tmp, "codex", "sessions", "2026", "10", "08", f"rollout-x-{sid}.jsonl")
        helpers.write(path, "\n".join(lines) + "\n")
        return record

    def found(self, marks=None, limit=leftoff.LIMIT):
        return leftoff.projects(sessions.read_records(), marks or {}, self.projects, limit)

    def test_owner_sessions_in_each_state(self):
        app = os.path.join(self.home, "app")
        os.makedirs(app)
        self.rec("W", "waiting", 100, app, said="Which key?")
        self.rec("P", "permission", 200, app, said=None, pending=("Bash", "Bash"))
        self.rec("K", "working", 300, app)
        self.rec("E", "ended", 400, app)
        [project] = self.found(limit=None)
        self.assertEqual(project["project"], "~/app")
        self.assertEqual([(e["session_id"], leftoff.state_word(e)) for e in project["sessions"]],
                         [("W", "waiting on you"), ("P", "needs approval: Bash"), ("K", "was working"), ("E", "ended")])
        w = project["sessions"][0]
        self.assertEqual((w["at"], w["excerpt"], w["checkout"], w["feature"]),
                         (NOW - 100, {"source": "agent", "text": "Which key?"}, None, None))
        self.assertIsNone(project["sessions"][1]["excerpt"])

    def test_automated_and_silent_sessions_are_left_out(self):
        self.rec("review", origin="sdk-cli")
        self.rec("exec", provider="codex", origin="codex_exec")
        self.rec("silent", said=None)
        self.rec("asks", "permission", said=None)  # no tools pending either
        self.rec("mine", provider="codex")
        self.assertEqual([[e["session_id"] for e in p["sessions"]] for p in self.found()], [["mine"]])

    def test_a_repo_and_its_worktree_are_one_project(self):
        repo = self.repo("relay-go", {}, commit=True)
        tree = os.path.join(self.projects, "relay-go-dev")
        helpers.sh(repo, "git", "worktree", "add", "-q", "--detach", tree)
        self.rec("A", ago=100, cwd=tree)
        self.rec("B", ago=200, cwd=os.path.join(repo))
        elsewhere = os.path.realpath(os.path.join(self.tmp, "elsewhere"))
        os.makedirs(elsewhere)
        self.rec("C", ago=50, cwd=elsewhere)
        self.rec("D", ago=10)
        got = self.found()
        self.assertEqual([p["project"] for p in got], ["Unknown folder", elsewhere, "relay-go"])
        self.assertEqual([(e["session_id"], e["checkout"]) for e in got[2]["sessions"]],
                         [("A", "relay-go-dev"), ("B", None)])

    def test_order_ties_limit_and_more(self):
        for sid, ago in (("b", 100), ("a", 100), ("c", 50), ("d", 300), ("e", 400)):
            self.rec(sid, ago=ago, cwd=self.home)
        self.rec("quiet", ago=500, cwd=self.home, said=None)
        self.rec("old", ago=600, cwd=self.home)
        calls = []
        real = agentask.last_words

        def counted(provider, sid):
            calls.append(sid)
            return real(provider, sid)
        with mock.patch.object(leftoff.agentask, "last_words", counted):
            [p] = self.found()
        self.assertEqual([e["session_id"] for e in p["sessions"]], ["c", "a", "b"])
        self.assertEqual((p["more"], p["last_active"], p["project"]), (4, NOW - 50, "~"))
        self.assertEqual(calls, ["c", "a", "b"])
        [p] = self.found(limit=None)
        self.assertEqual(([e["session_id"] for e in p["sessions"]], p["more"]), (["c", "a", "b", "d", "e", "old"], 0))

    def test_one_failing_session_is_skipped(self):
        self.rec("bad")
        self.rec("ok")
        real = agentask.last_words

        def flaky(provider, sid):
            if sid == "bad":
                raise RuntimeError("boom")
            return real(provider, sid)
        with mock.patch.object(leftoff.agentask, "last_words", flaky):
            self.assertEqual([e["session_id"] for e in self.found()[0]["sessions"]], ["ok"])

    def test_resume_lines(self):
        app, spaced, outside = (os.path.join(self.home, "app"), os.path.join(self.home, "My Projects", "app"),
                                os.path.realpath(os.path.join(self.tmp, "out side")))
        for d in (app, spaced, outside):
            os.makedirs(d)
        line = leftoff.resume_line
        self.assertEqual(line("claude", "S1", app), "cd ~/app && claude --resume S1")
        self.assertEqual(line("claude", "S1", spaced), "cd ~/'My Projects/app' && claude --resume S1")
        self.assertEqual(line("codex", "C1", outside), f"cd '{outside}' && codex resume C1")
        self.assertEqual(line("claude", "S1", self.home), "cd ~ && claude --resume S1")
        self.assertEqual(line("claude", "S1", None), "claude --resume S1")
        self.assertEqual(line("codex", "C1", os.path.join(self.tmp, "gone")), "codex resume C1")
        self.assertEqual(line("claude", "a b", None), "claude --resume 'a b'")

    def test_local_marks(self):
        self.held("alpha", "live", "S1")
        self.held("beta", "shipped", "S2", stage="done", status="done")
        gamma = self.repo("gamma", {})
        for slug, stage, updated, sid in (("old", "done", "2026-10-08T09:00:00", "S3"),
                                          ("new", "build", "2026-10-01T09:00:00", "S3"),
                                          ("a1", "done", "2026-10-01T09:00:00", "S4"),
                                          ("a2", "done", "2026-10-02T09:00:00", "S4")):
            self.put(gamma, slug, stage=stage, status="done" if stage == "done" else "drafting",
                     owner={"provider": "claude", "session": sid})
            path = state.state_path(gamma, slug)
            st = state.read_state(path)
            with open(path) as f:
                text = f.read()
            with open(path, "w") as f:
                f.write(text.replace(json.dumps(st["updated"]), json.dumps(updated)))
        marks = status.local_marks(self.projects)
        self.assertEqual(marks["S1"], {"slug": "live", "done": False})
        self.assertEqual(marks["S2"], {"slug": "shipped", "done": True})
        self.assertEqual(marks["S3"], {"slug": "new", "done": False})
        self.assertEqual(marks["S4"], {"slug": "a2", "done": True})

    def test_a_health_matched_record_is_not_marked_in_the_terminal(self):
        root = self.held("alpha", "live", "OWNER")
        helpers.sh(root, "git", "checkout", "-q", "-b", "feat/live")
        self.session("OTHER", cwd=os.path.realpath(root))  # health would match it for the row
        self.assertNotIn("OTHER", status.local_marks(self.projects))

    def test_relay_left_output(self):
        repo = self.held("relay-go", "where", "S1")
        tree = os.path.join(self.projects, "relay-go-dev")
        helpers.sh(repo, "git", "worktree", "add", "-q", "--detach", tree)
        app = os.path.join(self.home, "app")
        os.makedirs(app)
        now = time.time()
        self.rec("S1", "waiting", 3 * 3600, tree, said="Send me the key.", now=now)
        for i in range(4):
            self.rec(f"A{i}", "ended", 3600 * (i + 1), app, now=now)
        with mock.patch("relaylib.leftoff.time.time", return_value=now):
            text = self.run_cmd("left")
            full = self.run_cmd("left", "--all")
        self.assertEqual(text, "\n".join([
            "~/app · last active 1h ago",
            "  claude · ended · 1h ago", "    Agent: done here", "    Resume: cd ~/app && claude --resume A0",
            "  claude · ended · 2h ago", "    Agent: done here", "    Resume: cd ~/app && claude --resume A1",
            "  claude · ended · 3h ago", "    Agent: done here", "    Resume: cd ~/app && claude --resume A2",
            "  Up to 1 more: relay left --all",
            "",
            "relay-go · last active 3h ago",
            "  claude · waiting on you · 3h ago · relay-go-dev · feature where",
            "    Agent: Send me the key.",
            f"    Resume: cd {shlex.quote(os.path.realpath(tree))} && claude --resume S1",
        ]) + "\n")
        self.assertIn("claude --resume A3", full)
        self.assertNotIn("more", full)

    def test_relay_left_done_mark_and_empty(self):
        self.assertEqual(self.run_cmd("left"), leftoff.EMPTY + "\n")
        self.held("alpha", "shipped", "S1", stage="done", status="done")
        self.rec("S1", now=time.time())
        self.assertIn("· feature shipped (done)", self.run_cmd("left"))

    def test_relay_left_stays_local(self):
        self.held("alpha", "live", "S1")
        self.rec("S1", now=time.time())
        argvs = []
        real_run, real_popen = subprocess.run, subprocess.Popen

        def run(args, *a, **k):
            argvs.append(list(args))
            return real_run(args, *a, **k)

        def popen(args, *a, **k):
            argvs.append(list(args))
            return real_popen(args, *a, **k)
        with mock.patch("subprocess.run", run), mock.patch("subprocess.Popen", popen):
            self.assertIn("feature live", self.run_cmd("left"))
        self.assertTrue(argvs)
        for argv in argvs:
            self.assertNotIn("fetch", argv)
            self.assertNotIn("ls-remote", argv)
            self.assertFalse(argv[0].endswith("gh"), argv)

    def test_failures(self):
        self.rec("S1", now=time.time())
        with mock.patch.object(status, "local_marks", side_effect=RuntimeError("boom")):
            self.assertIn("claude --resume S1", self.run_cmd("left"))
        with mock.patch.object(sessions, "read_records", side_effect=RuntimeError("boom")):
            self.assertEqual(self.run_cmd("left"), leftoff.EMPTY + "\n")

    def test_relay_status_is_unchanged(self):
        self.rec("S1", "ended", now=time.time())
        self.assertNotIn("S1", self.run_cmd("status"))


if __name__ == "__main__":
    unittest.main()
