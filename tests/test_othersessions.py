import os
import unittest
from unittest import mock

from relaylib import config, health, othersessions
from tests import helpers
from tests.test_agentask import said
from tests.test_transcripts import Homes

NOW = 1_800_000_000.0


def record(sid, state="waiting", since=NOW - 7200, provider="claude", cwd=None, pending=()):
    return {"provider": provider, "session_id": sid, "cwd": cwd, "state": state, "since": since, "at": since,
            "event": "Stop", "pending": [{"tool_use_id": None, "tool_name": t, "input_sha1": "x"} for t in pending]}


class ListedTest(Homes):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "projects")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)
        self.repo = os.path.join(self.root, "proteindiary")
        os.makedirs(self.repo)
        helpers.sh(self.repo, "git", "init", "-q", "-b", "develop")
        helpers.sh(self.repo, "git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                   "commit", "-q", "--allow-empty", "-m", "init")
        self.cfg = config.load()

    def claude_said(self, sid, text):
        path = os.path.join(self.tmp, "claude", "projects", "-work", sid + ".jsonl")
        helpers.write(path, said(text) + "\n")

    def listed(self, records, claimed=(), cfg=None):
        return othersessions.listed(records, set(claimed), self.root, cfg or self.cfg, NOW)

    def test_a_waiting_session_with_a_message_is_listed(self):
        self.claude_said("S1", "I still need the publisher JSON key path.")
        [entry] = self.listed([record("S1", cwd=self.repo)])
        self.assertEqual(entry, {"provider": "claude", "session_id": "S1", "label": "proteindiary",
                                 "folder": self.repo, "state": "waiting", "since": NOW - 7200, "pending_tools": [],
                                 "excerpt": {"source": "agent", "text": "I still need the publisher JSON key path."}})

    def test_a_permission_session_is_listed_with_its_tools_and_no_message(self):
        [entry] = self.listed([record("C1", "permission", provider="codex", pending=("Bash", "Bash", "Edit"))])
        self.assertEqual((entry["pending_tools"], entry["excerpt"], entry["label"]), (["Bash", "Edit"], None,
                                                                                       "Unknown folder"))

    def test_a_registered_hub_session_is_not_listed(self):  # mobile-hub D9, R14
        from relaylib import hub
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "relayhome")})
        p.start()
        self.addCleanup(p.stop)
        self.claude_said("HUB", "Here is what needs you.")
        self.claude_said("S1", "I still need the key path.")
        records = [record("HUB"), record("S1")]
        self.assertIsNone(hub.register("claude", "HUB", records))
        self.assertEqual([e["session_id"] for e in self.listed(records)], ["S1"])

    def test_sessions_that_are_not_listed(self):
        for sid in ("grace", "old", "ended", "working", "claimed"):
            self.claude_said(sid, "hello")
        records = [record("grace", since=NOW - 30), record("old", since=NOW - 25 * 3600),
                   record("ended", "ended"), record("working", "working"), record("claimed"),
                   record("silent")]  # waiting, but never said anything
        self.assertEqual(self.listed(records, claimed={"claimed"}), [])

    def test_newest_wait_first_ties_by_id(self):
        for sid in ("b", "a", "c"):
            self.claude_said(sid, "x")
        got = self.listed([record("b", since=NOW - 600), record("c", since=NOW - 900), record("a", since=NOW - 600)])
        self.assertEqual([e["session_id"] for e in got], ["a", "b", "c"])

    def test_labels(self):
        sub = os.path.join(self.repo, "app", "src")
        os.makedirs(sub)
        tree = os.path.join(self.root, "proteindiary-wt")
        helpers.sh(self.repo, "git", "worktree", "add", "-q", "--detach", tree)
        downloads = os.path.join(self.home, "Downloads", "x")
        os.makedirs(downloads)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        gone = os.path.join(self.tmp, "gone")
        checkouts = [self.repo, tree]
        self.assertEqual(othersessions.label(self.repo, checkouts), "proteindiary")
        self.assertEqual(othersessions.label(sub, checkouts), "proteindiary")
        self.assertEqual(othersessions.label(tree, checkouts), "proteindiary (proteindiary-wt)")
        self.assertEqual(othersessions.label(downloads, checkouts), "~/Downloads/x")
        self.assertEqual(othersessions.label(elsewhere, checkouts), os.path.realpath(elsewhere))
        self.assertEqual(othersessions.label(gone, checkouts), os.path.realpath(gone))
        self.assertEqual(othersessions.label(None, checkouts), "Unknown folder")
        self.claude_said("W", "x")
        self.assertEqual(self.listed([record("W", cwd=tree)])[0]["label"], "proteindiary (proteindiary-wt)")

    def test_an_unexpected_read_error_skips_only_that_session(self):
        self.claude_said("ok", "fine")
        real = othersessions.agentask.last_words

        def flaky(provider, sid):
            if sid == "bad":
                raise RuntimeError("boom")
            return real(provider, sid)
        with mock.patch.object(othersessions.agentask, "last_words", flaky):
            got = self.listed([record("bad"), record("ok")])
        self.assertEqual([e["session_id"] for e in got], ["ok"])

    def test_the_window_setting(self):
        self.claude_said("S", "x")
        five_hours = [record("S", since=NOW - 5 * 3600)]
        self.assertEqual(len(self.listed(five_hours)), 1)
        self.cfg["ui"] = {"other_sessions_hours": 2}
        self.assertEqual(self.listed(five_hours), [])
        for bad in (0, 200, "soon"):
            self.cfg["ui"] = {"other_sessions_hours": bad}
            self.assertEqual(health.ui_value(self.cfg, "other_sessions_hours"), 24)
            self.assertEqual(health.ui_settings(self.cfg)[2], ["ignored invalid [ui] other_sessions_hours"])
            self.assertEqual(len(self.listed(five_hours)), 1)


if __name__ == "__main__":
    unittest.main()
