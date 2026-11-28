import os
import tempfile
import time
import unittest
from unittest import mock

from relaylib import dispatch, gitops, hubstart, launch
from relaylib.errors import RelayError
from tests import helpers


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")  # short: macOS caps socket paths near 104 bytes
        self.addCleanup(temp.cleanup)
        self.tmp = os.path.realpath(temp.name)
        self.root = os.path.join(self.tmp, "p")
        os.makedirs(self.root)
        sub = os.path.join(self.tmp, "mk")
        os.makedirs(sub)
        self.origin, work = helpers.make_repo(sub)
        self.repo = os.path.join(self.root, "proj")
        os.rename(work, self.repo)
        self.log = os.path.join(self.tmp, "claude.log")
        self.bin = helpers.fake_claude(self.tmp)
        env = {"RELAY_HOME": os.path.join(self.tmp, "h"), "RELAY_ROOT": self.root, "HOME": self.tmp,
               "RELAY_CONFIG": os.path.join(self.tmp, "none.toml"), "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "c"),
               "CODEX_HOME": os.path.join(self.tmp, "x"), "FAKE_LOG": self.log,
               "PATH": self.bin + os.pathsep + os.environ["PATH"]}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def env(self, **values):
        p = mock.patch.dict(os.environ, values)
        p.start()
        self.addCleanup(p.stop)

    def calls(self):
        return helpers.fake_claude_calls(self.log)


class StartTest(Base):
    def request(self, slug="thing", **extra):
        return dispatch.create({"repo": {"name": "proj", "path": self.repo}, "request": "do it", "owner_said": None,
                                "slug": slug, "requested_by": "owner", "asked_by": None, "relayed_by": None,
                                **extra})["id"]

    def wt(self, slug="thing"):
        return os.path.join(self.repo, ".claude", "worktrees", slug)

    def set_started(self, rid, **fields):
        def change(record):
            record["started"] = {"provider": "claude", "session_id": "old-sid", "slug": "thing",
                                 "worktree": self.wt(), "name": "proj thing", "at": time.time() - 3600,
                                 "relayed_by": None, "launch": "ok", **fields}
        dispatch.mutate(rid, change)

    def assert_unchanged(self, rid):
        self.assertEqual(self.calls(), [])
        self.assertIsNone(dispatch.read(rid).get("started"))
        self.assertFalse(os.path.exists(self.wt()))

    def test_start_ok(self):
        rid = self.request()
        out = hubstart.start(rid, None, "claude session hub1")
        self.assertEqual((out["launch"], out["reused"], out["name"]), ("ok", False, "proj thing"))
        self.assertEqual(out["worktree"], self.wt())
        self.assertEqual(gitops.head_sha(self.wt()), gitops.head_sha(self.repo, "origin/develop"))
        [call] = self.calls()
        self.assertEqual(call["cwd"], self.wt())
        self.assertEqual(call["argv"][:5], ["--bg", "--remote-control", "proj thing", "--session-id",
                                            out["session_id"]])
        record = dispatch.read(rid)
        self.assertEqual(record["status"], "waiting")
        self.assertEqual(record["started"]["launch"], "ok")
        self.assertEqual(record["started"]["relayed_by"], "claude session hub1")
        self.assertEqual(record["started"]["slug"], "thing")

    def test_slug_option_wins(self):
        out = hubstart.start(self.request(), "other", None)
        self.assertEqual(out["worktree"], self.wt("other"))

    def test_refuses_without_slug(self):
        rid = self.request(slug=None)
        with self.assertRaises(RelayError) as e:
            hubstart.start(rid, None, None)
        self.assertEqual(str(e.exception), f"request {rid} has no slug; run relay dispatch --start {rid} --slug "
                                           "<slug> --relayed")

    def test_refuses_not_waiting(self):
        rid = self.request()
        dispatch.mutate(rid, lambda r: r.update(status="cancelled"))
        with self.assertRaisesRegex(RelayError, "cancelled"):
            hubstart.start(rid, None, None)
        self.assertEqual(self.calls(), [])

    def test_refuses_unknown(self):
        with self.assertRaisesRegex(RelayError, "no request qzzz on this machine"):
            hubstart.start("qzzz", None, None)

    def test_refuses_without_claude(self):  # R3
        rid = self.request()
        self.env(PATH="/usr/bin:/bin")
        with self.assertRaisesRegex(RelayError, "claude is not on PATH"):
            hubstart.start(rid, None, None)
        self.assertIsNone(dispatch.read(rid).get("started"))
        self.assertFalse(os.path.exists(self.wt()))

    def test_refuses_existing_path(self):  # R4
        rid = self.request()
        os.makedirs(self.wt())
        with self.assertRaisesRegex(RelayError, "already exists"):
            hubstart.start(rid, None, None)
        self.assertEqual(self.calls(), [])
        self.assertIsNone(dispatch.read(rid).get("started"))

    def test_refuses_existing_local_branch(self):  # R4
        rid = self.request()
        helpers.sh(self.repo, "git", "branch", "feat/thing")
        with self.assertRaisesRegex(RelayError, "the branch feat/thing already exists"):
            hubstart.start(rid, None, None)
        self.assert_unchanged(rid)

    def test_refuses_existing_origin_branch(self):  # R4
        rid = self.request()
        helpers.sh(self.repo, "git", "push", "-q", "origin", "develop:feat/thing")
        with self.assertRaisesRegex(RelayError, "the branch feat/thing already exists"):
            hubstart.start(rid, None, None)
        self.assert_unchanged(rid)

    def test_refuses_fetch_failure(self):  # R4
        rid = self.request()
        helpers.sh(self.repo, "git", "remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        with self.assertRaisesRegex(RelayError, "cannot fetch origin"):
            hubstart.start(rid, None, None)
        self.assert_unchanged(rid)

    def test_refuses_unresolved_base(self):  # R4
        rid = self.request()
        helpers.write(os.path.join(self.repo, "docs", "relay", "config.toml"), '[relay]\nbase_branch = "nope"\n')
        with self.assertRaisesRegex(RelayError, "no origin/nope"):
            hubstart.start(rid, None, None)
        self.assert_unchanged(rid)

    def test_refuses_live_claim(self):  # R2
        rid = self.request()
        helpers.live_session("claude", "taker")
        dispatch.claim(rid, {"provider": "claude", "session_id": "taker"}, "thing", "feat/thing")
        with self.assertRaisesRegex(RelayError, "is being started by claude session taker"):
            hubstart.start(rid, None, None)
        self.assertEqual(self.calls(), [])

    def test_refuses_fresh_started(self):  # R2, D9
        rid = self.request()
        self.set_started(rid, at=time.time() - 300, launch="pending", worktree=os.path.join(self.tmp, "none"))
        with self.assertRaisesRegex(RelayError, "already has a session starting"):
            hubstart.start(rid, None, None)
        self.assertEqual(self.calls(), [])
        self.set_started(rid, at=time.time() - 1800, worktree=os.path.join(self.tmp, "none"))
        helpers.live_session("claude", "old-sid")
        with self.assertRaisesRegex(RelayError, "already has a session starting"):
            hubstart.start(rid, None, None)
        os.unlink(helpers_record("old-sid"))
        self.set_started(rid, at=time.time() - 600, worktree=os.path.join(self.tmp, "none"))
        out = hubstart.start(rid, None, None)
        self.assertEqual(out["launch"], "ok")
        self.assertNotEqual(dispatch.read(rid)["started"]["session_id"], "old-sid")

    def test_failed_launch_cleans_up(self):  # R9
        rid = self.request()
        self.env(FAKE_EXIT="1", FAKE_ERR="boom")
        with self.assertRaises(RelayError) as e:
            hubstart.start(rid, None, None)
        self.assertEqual(str(e.exception), "boom")
        self.assertFalse(os.path.exists(self.wt()))
        record = dispatch.read(rid)
        self.assertIsNone(record.get("started"))
        self.assertEqual(record["status"], "waiting")

    def test_failed_launch_keeps_dirty_worktree(self):  # R9
        rid = self.request()
        self.env(FAKE_EXIT="1", FAKE_TOUCH="notes.txt")
        with self.assertRaises(RelayError):
            hubstart.start(rid, None, None)
        self.assertTrue(os.path.exists(os.path.join(self.wt(), "notes.txt")))
        self.assertIsNone(dispatch.read(rid).get("started"))

    def test_unconfirmed(self):  # R9a
        rid = self.request()
        self.env(FAKE_SLEEP="5")
        with mock.patch.object(launch, "LAUNCH_WAIT_S", 1):
            out = hubstart.start(rid, None, None)
        self.addCleanup(_kill_calls, self.log)
        self.assertEqual(out["launch"], "unconfirmed")
        self.assertTrue(os.path.isdir(self.wt()))
        self.assertEqual(dispatch.read(rid)["started"]["launch"], "unconfirmed")

    def test_restart_reuses_worktree(self):  # R4a
        rid = self.request()
        helpers.sh(self.repo, "git", "branch", "feat/thing")  # the branch check is skipped on a re-start
        launch.make_worktree(self.repo, self.wt(), "develop")
        self.set_started(rid)
        self.env(FAKE_EXIT="1")
        with self.assertRaises(RelayError):
            hubstart.start(rid, None, None)
        self.assertTrue(os.path.isdir(self.wt()))  # a reused worktree is never removed
        self.env(FAKE_EXIT="0")
        self.set_started(rid)
        out = hubstart.start(rid, None, None)
        self.assertTrue(out["reused"])
        self.assertEqual(self.calls()[-1]["cwd"], self.wt())

    def test_restart_refuses_when_feature_started(self):  # R4a
        rid = self.request()
        launch.make_worktree(self.repo, self.wt(), "develop")
        helpers.write(os.path.join(self.wt(), "docs", "relay", "thing", "state.md"), "---\n{}\n---\n")
        self.set_started(rid)
        with self.assertRaises(RelayError) as e:
            hubstart.start(rid, None, None)
        self.assertEqual(str(e.exception), f"thing was already started in {self.wt()} by session old-sid; its work "
                                           "is kept there. Finish it at the Mac, or push its branch and run relay "
                                           "override release")
        self.assertEqual(self.calls(), [])
        self.assertEqual(dispatch.read(rid)["started"]["session_id"], "old-sid")

    def test_restart_with_other_slug_refused(self):
        rid = self.request()
        launch.make_worktree(self.repo, self.wt(), "develop")
        self.set_started(rid)
        with self.assertRaisesRegex(RelayError, "was started as thing in"):
            hubstart.start(rid, "other", None)
        self.assertEqual(self.calls(), [])

    def test_restart_after_worktree_gone(self):
        rid = self.request()
        self.set_started(rid, worktree=os.path.join(self.tmp, "gone"))
        out = hubstart.start(rid, None, None)
        self.assertFalse(out["reused"])
        self.assertEqual(out["worktree"], self.wt())

    def test_take_claim_works_with_launch_record(self):  # Review Focus 1
        rid = self.request()
        hubstart.start(rid, None, None)
        dispatch.claim(rid, {"provider": "claude", "session_id": "new"}, "thing", "feat/thing")
        dispatch.mark_started(rid, {"provider": "claude", "session_id": "new", "slug": "thing",
                                    "branch": "feat/thing"})
        record = dispatch.read(rid)
        self.assertEqual(record["status"], "started")
        self.assertNotIn("launch", record["started"])


def helpers_record(sid):
    from relaylib import sessions
    return sessions.record_path("claude", sid)


def _kill_calls(log):
    for call in helpers.fake_claude_calls(log):
        try:
            os.kill(call["pid"], 9)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
