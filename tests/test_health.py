import os
import tempfile
import time
import unittest

from relaylib import config, health
from tests import helpers


def rec(state="working", since=1000.0, at=1000.0, sid="s1", provider="claude", cwd="/w"):
    return {"provider": provider, "session_id": sid, "cwd": cwd, "state": state, "since": since, "at": at,
            "event": "x", "pending": []}


class HealthRulesTest(unittest.TestCase):
    def test_rules_in_order(self):
        act = {"last_activity": None, "unpushed": None}
        now = 1000.0 + 600
        self.assertEqual(health.health(rec("permission"), act, 1, 30, now)["text"], "needs permission 10m")
        self.assertEqual(health.health(rec("waiting"), act, 1, 30, now),
                         {"kind": "attention", "text": "waiting on you 10m", "since": 1000.0})
        self.assertEqual(health.health(rec("ended"), act, 1, 30, now)["kind"], "quiet")
        self.assertEqual(health.health(rec("working"), act, 1, 30, now)["text"], "active 10m ago")
        self.assertEqual(health.health(rec("working"), act, 1, 30, 1000.0 + 3600)["text"], "no activity 1h")
        self.assertIsNone(health.health(None, act, 1, 30, now))

    def test_grace_falls_through_to_activity(self):
        act = {"last_activity": 1020.0, "unpushed": 0}
        self.assertEqual(health.health(rec("waiting"), act, 1, 30, 1030.0)["text"], "active 10s ago")
        self.assertEqual(health.health(rec("waiting"), act, 1, 30, 1061.0)["kind"], "attention")

    def test_activity_alone_and_newest_source_wins(self):
        self.assertEqual(health.health(None, {"last_activity": 900.0, "unpushed": 0}, 1, 30, 1000.0),
                         {"kind": "ok", "text": "active 1m ago", "since": 900.0})
        r = health.health(rec("working", at=990.0), {"last_activity": 100.0, "unpushed": 0}, 1, 30, 1000.0)
        self.assertEqual(r["since"], 990.0)

    def test_ages_and_future_times(self):
        self.assertEqual([health.age(s) for s in (5, 59, 61, 3599, 7200, 3 * 86400, -40)],
                         ["5s", "59s", "1m", "59m", "2h", "3d", "0s"])

    def test_ui_settings_validate_and_default(self):
        cfg = config.load()
        self.assertEqual(health.ui_settings(cfg)[:2], (1, 30))
        cfg["ui"] = {"health_grace_minutes": 90, "quiet_minutes": "soon"}
        grace, quiet, notes = health.ui_settings(cfg)
        self.assertEqual((grace, quiet), (1, 30))
        self.assertEqual(notes, ["ignored invalid [ui] health_grace_minutes", "ignored invalid [ui] quiet_minutes"])
        cfg["ui"] = {"health_grace_minutes": 0, "quiet_minutes": 1440}
        self.assertEqual(health.ui_settings(cfg), (0, 1440, []))
        cfg["ui"] = {"health_grace_minutes": True}
        self.assertEqual(health.ui_settings(cfg)[2], ["ignored invalid [ui] health_grace_minutes"])


class MatchTest(unittest.TestCase):
    def test_id_first_then_newest_record_in_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            inside = os.path.join(tmp, "sub")
            os.makedirs(inside)
            records = [rec(sid="codex-hook-a", provider="codex", cwd=os.path.realpath(inside), at=2000.0),
                       rec(sid="codex-hook-b", provider="codex", cwd=os.path.realpath(tmp), at=2000.0),
                       rec(sid="old", provider="codex", cwd=os.path.realpath(tmp), at=500.0),
                       rec(sid="S", provider="claude", cwd=None, at=3000.0),
                       rec(sid="S", provider="codex", cwd="/elsewhere", at=10.0)]
            self.assertEqual(health.match(records, "codex", "S", 1000.0, [tmp])["cwd"], "/elsewhere")  # id wins
            self.assertEqual(health.match(records, "codex", "T", 1000.0, [tmp])["session_id"], "codex-hook-a")  # tie: smaller id
            self.assertIsNone(health.match(records, "claude", "T", 1000.0, [tmp]))   # null cwd never matches
            self.assertIsNone(health.match(records, "codex", "T", 2500.0, [tmp]))    # written before the owner
            link = os.path.join(os.path.dirname(tmp), os.path.basename(tmp) + "-link")   # outside tmp, not via tmp/..
            os.symlink(tmp, link)
            self.addCleanup(os.unlink, link)
            linked = [rec(sid="via-link", provider="claude", cwd=link, at=2000.0)]
            self.assertEqual(health.match(linked, "claude", "T", 1000.0, [tmp])["session_id"], "via-link")


class ActivityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.origin, self.work = helpers.make_repo(self.tmp)
        helpers.sh(self.work, "git", "switch", "-qc", "feat/x")
        helpers.write(os.path.join(self.work, "a.txt"), "a")
        helpers.sh(self.work, "git", "add", "a.txt")
        helpers.sh(self.work, "git", "commit", "-qm", "a")
        helpers.sh(self.work, "git", "push", "-qu", "origin", "feat/x")

    def test_files_commits_origin_and_unpushed(self):
        helpers.sh(self.work, "git", "commit", "-q", "--allow-empty", "-m", "local only")
        nested = os.path.join(self.work, "new", "deep", "b.txt")
        helpers.write(nested, "b")
        os.utime(nested, (5_000_000_000, 5_000_000_000))       # newest, inside an untracked folder
        act = health.activity([self.work], "feat/x")
        self.assertEqual((act["last_activity"], act["unpushed"]), (5_000_000_000, 1))
        origin_ct = int(helpers.sh(self.work, "git", "log", "-1", "--format=%ct", "origin/feat/x").strip())
        self.assertEqual(act["origin"], origin_ct)

    def test_activity_degrades_input_by_input(self):
        os.remove(os.path.join(self.work, "a.txt"))             # a deleted path has no mtime: skipped
        helpers.sh(self.work, "git", "push", "-q", "origin", "--delete", "feat/x")
        helpers.sh(self.work, "git", "fetch", "-q", "--prune")
        act = health.activity([self.work, os.path.join(self.tmp, "gone")], "feat/x")
        self.assertIsNone(act["unpushed"])                        # no origin branch
        self.assertAlmostEqual(act["last_activity"], time.time(), delta=120)   # HEAD's commit still counts
        self.assertEqual(health.activity([os.path.join(self.tmp, "gone")], "feat/x"),
                         {"last_activity": None, "unpushed": None, "origin": None})

    def test_branch_checkouts_are_real_paths_on_the_branch(self):
        other = os.path.join(self.tmp, "other")
        helpers.sh(self.work, "git", "worktree", "add", "-q", "-b", "side", other)
        self.assertEqual(health.branch_checkouts(self.work, "feat/x"), [os.path.realpath(self.work)])
        from unittest import mock
        from relaylib.errors import RelayError
        with mock.patch.object(health.gitops, "worktrees", side_effect=RelayError("git is broken")):
            self.assertEqual(health.branch_checkouts(self.work, "feat/x"), [])
