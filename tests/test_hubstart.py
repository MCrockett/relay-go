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


class ReleaseTest(Base):  # D8, R12
    def feature(self, slug, sid, push=True):
        """A feature owned by claude session `sid` on feat/<slug>, published to origin unless push is False."""
        from relaylib import state
        work = os.path.join(self.tmp, "w-" + slug)
        helpers.sh(self.tmp, "git", "clone", "-q", self.origin, work)
        helpers._identify(work)
        helpers.sh(work, "git", "switch", "-qc", f"feat/{slug}")
        st = state.new_state(slug, "proj", {"provider": "claude", "session": sid}, f"feat/{slug}")
        st.update(stage="spec", status="drafting")
        state.write_state(state.state_path(work, slug), st)
        helpers.sh(work, "git", "add", ".")
        helpers.sh(work, "git", "commit", "-qm", slug)
        if push:
            helpers.sh(work, "git", "push", "-qu", "origin", "HEAD")
        else:  # only in a worktree of the repo: a local claim
            helpers.sh(self.repo, "git", "fetch", "-q", work, f"feat/{slug}:feat/{slug}")
            helpers.sh(self.repo, "git", "worktree", "add", "-q", os.path.join(self.tmp, "lw-" + slug), f"feat/{slug}")
        return work

    def handoff_on_origin(self, slug):
        helpers.sh(self.repo, "git", "fetch", "-q", "origin")
        return gitops.show(self.repo, f"origin/feat/{slug}", f"docs/relay/{slug}/handoff.md")

    def test_release_all_releases_every_held_feature(self):
        from relaylib import ownership, owneractions
        self.feature("a", "old")
        self.feature("b", "old")
        self.assertEqual(hubstart.held_features(self.repo, "old"), ["a", "b"])
        self.assertEqual(hubstart.release_all(self.repo, "old", "claude session hub1"), ["a", "b"])
        for slug in ("a", "b"):
            self.assertIn("Released by the owner", self.handoff_on_origin(slug))
            _, st = owneractions.published(self.repo, slug)
            self.assertEqual(st["owner_actions"][-1]["relayed_by"], "claude session hub1")
            self.assertIn("release", st["owner_actions"][-1]["action"])
        self.assertEqual(hubstart.held_features(self.repo, "old"), [])
        ownership.check_can_write(self.repo, "new")  # another session may take them now

    def test_release_all_nothing_held(self):
        self.assertEqual(hubstart.release_all(self.repo, "old", None), [])

    def test_release_all_refuses_unpublished_first(self):
        self.feature("a", "old")
        self.feature("b", "old", push=False)
        with self.assertRaisesRegex(RelayError, "b is not published on origin; push its branch first"):
            hubstart.release_all(self.repo, "old", None)
        self.assertIsNone(self.handoff_on_origin("a"))

    def test_release_all_partial_failure(self):
        self.feature("a", "old")
        self.feature("b", "old")
        helpers.hook(self.origin, "pre-receive",
                     'while read o n r; do [ "$r" = refs/heads/feat/b ] && echo "no b" >&2 && exit 1; done\nexit 0\n')
        with self.assertRaises(RelayError) as e:
            hubstart.release_all(self.repo, "old", None)
        self.assertIn("could not release b:", str(e.exception))
        self.assertIn("released so far: a", str(e.exception))
        self.assertIsNotNone(self.handoff_on_origin("a"))


class ClearTest(ReleaseTest):  # R11 to R16
    def setUp(self):
        super().setUp()
        self.closed("old")

    def closed(self, sid, cwd=None):
        helpers.live_session("claude", sid, cwd=cwd or self.repo, state="ended")

    def item(self, sid="old", **over):
        return {"kind": "session", "provider": "claude", "session_id": sid, "label": "proj", "n": 3, **over}

    def entry(self, sid="old"):
        from relaylib import retired
        return retired.entry("claude", sid)

    def put(self, sid="old", **fields):
        from relaylib import retired
        with retired.locked():
            entries = retired.load()
            entries[retired.key("claude", sid)] = {"provider": "claude", "session_id": sid, "label": "proj",
                                                   "replaced_by": "r1", "replaced_by_name": "proj proj (fresh)",
                                                   "relayed_by": None, "handover": [], **fields}
            retired.save(entries)

    def owner_actions(self, slug):
        from relaylib import owneractions
        helpers.sh(self.repo, "git", "fetch", "-q", "origin")
        return [a for a in owneractions.published(self.repo, slug)[1]["owner_actions"] if "release" in a["action"]]

    def test_clear_ok(self):  # R12, R13, R14
        self.feature("a", "old")
        out = hubstart.clear(self.item(), "claude session hub1")
        self.assertEqual((out["old"], out["launch"], out["released"]), ("old", "ok", ["a"]))
        self.assertIsNotNone(self.handoff_on_origin("a"))
        [call] = self.calls()
        self.assertEqual(call["cwd"], self.repo)
        self.assertEqual(call["argv"][:5], ["--bg", "--remote-control", "proj proj (fresh)", "--session-id",
                                            out["session_id"]])
        self.assertIn("replace session old", call["argv"][-1])
        self.assertIn("held: a.", call["argv"][-1])
        e = self.entry()
        self.assertEqual((e["state"], e["launch"], e["replaced_by"], e["label"]),
                         ("retired", "ok", out["session_id"], "proj"))

    def test_clear_refuses_live(self):  # R11
        helpers.live_session("claude", "old", cwd=self.repo)
        self.feature("a", "old")
        with self.assertRaises(RelayError) as e:
            hubstart.clear(self.item(), None)
        self.assertEqual(str(e.exception), "close proj in the Remote Control list first, then clear again")
        self.assertIsNone(self.handoff_on_origin("a"))
        self.assertIsNone(self.entry())

    def test_clear_refuses_hub_codex_gone_folder_and_other_kinds(self):  # R11
        from relaylib import hub
        os.makedirs(hub.folder(), exist_ok=True)
        with open(os.path.join(hub.folder(), "sessions.json"), "w") as f:
            f.write('[{"provider": "claude", "session_id": "old", "at": %f}]' % time.time())
        with self.assertRaisesRegex(RelayError, "proj is a hub session"):
            hubstart.clear(self.item(), None)
        with self.assertRaisesRegex(RelayError, "Claude sessions only"):
            hubstart.clear(self.item(provider="codex"), None)
        self.closed("far", cwd=os.path.join(self.tmp, "gone"))
        with self.assertRaisesRegex(RelayError, "proj's folder is gone"):
            hubstart.clear(self.item("far"), None)
        with self.assertRaisesRegex(RelayError, "item 3 is not a session"):
            hubstart.clear({"kind": "feature", "n": 3}, None)
        self.assertEqual(self.calls(), [])

    def test_clear_uses_saved_digest_after_close(self):  # R11, Review Focus 5
        from relaylib import hub
        helpers.live_session("claude", "old", cwd=self.repo, state="waiting")
        digest_id, _ = hub.save([self.item(n=1)], {"from": "built", "seconds": 1}, None)
        self.closed("old")  # the owner closes it from the Remote Control list
        item = hub.resolve(f"{digest_id}.1", None)
        self.assertEqual(hubstart.clear(item, None)["launch"], "ok")

    def test_clear_refuses_unpublished_feature(self):  # D8, Review Focus 3
        self.feature("a", "old")
        self.feature("b", "old", push=False)
        with self.assertRaisesRegex(RelayError, "b is not published on origin"):
            hubstart.clear(self.item(), None)
        self.assertIsNone(self.handoff_on_origin("a"))
        self.assertEqual(self.entry()["state"], "clearing")
        self.assertEqual(self.calls(), [])

    def test_second_clear_refused(self):  # R11a
        self.put(state="clearing", at=time.time() - 120)
        with self.assertRaisesRegex(RelayError, "proj is already being cleared"):
            hubstart.clear(self.item(), None)
        self.assertEqual(self.calls(), [])

    def test_stale_clearing_replaced(self):  # R11a
        self.put(state="clearing", at=time.time() - 660, handover=["kept"])
        out = hubstart.clear(self.item(), None)
        self.assertEqual(out["launch"], "ok")
        self.assertEqual(out["released"], ["kept"])  # a stale reservation's handover is kept

    def test_clear_failed_launch(self):  # R13
        self.feature("a", "old")
        self.env(FAKE_EXIT="1", FAKE_ERR="not logged in")
        with self.assertRaisesRegex(RelayError, "not logged in"):
            hubstart.clear(self.item(), None)
        e = self.entry()
        self.assertEqual((e["state"], e["launch"], e["launch_error"]), ("retired", "failed", "not logged in"))
        self.assertIsNotNone(self.handoff_on_origin("a"))

    def test_clear_unconfirmed(self):  # R13
        self.env(FAKE_SLEEP="5")
        with mock.patch.object(launch, "LAUNCH_WAIT_S", 1):
            out = hubstart.clear(self.item(), None)
        self.addCleanup(_kill_calls, self.log)
        self.assertEqual(out["launch"], "unconfirmed")
        self.assertEqual(self.entry()["launch"], "unconfirmed")

    def test_clear_refuses_while_replacement_fresh(self):  # R16
        self.put(state="retired", at=time.time() - 180, retired_at=time.time() - 180)
        with self.assertRaisesRegex(RelayError, "proj was retired; replaced by proj proj \\(fresh\\)"):
            hubstart.clear(self.item(), None)
        helpers.live_session("claude", "r1")
        self.put(state="retired", at=time.time() - 3600, retired_at=time.time() - 3600)
        with self.assertRaisesRegex(RelayError, "proj was retired"):
            hubstart.clear(self.item(), None)
        self.assertEqual(self.calls(), [])

    def test_chain_a_b_c_with_c_live_refused(self):  # build R1-2
        old = time.time() - 3600
        self.put("old", state="retired", replaced_by="B", at=old, retired_at=old)
        self.put("B", state="retired", replaced_by="C", replaced_by_name="proj proj (fresh) (fresh)", at=old,
                 retired_at=old)
        helpers.live_session("claude", "C", cwd=os.path.join(self.tmp, "elsewhere"))
        from relaylib import retired
        self.assertFalse(retired.is_stale(self.entry(), time.time()))
        with self.assertRaisesRegex(RelayError, "proj was retired"):
            hubstart.clear(self.item(), None)
        os.unlink(helpers_record("C"))  # C is gone too: still only the newest may be cleared
        with self.assertRaisesRegex(RelayError, "was cleared too; clear the newest session instead"):
            hubstart.clear(self.item(), None)
        self.assertEqual(self.calls(), [])

    def test_clear_refuses_another_live_session_in_the_folder(self):  # D6
        helpers.live_session("claude", "other", cwd=self.repo)
        with self.assertRaisesRegex(RelayError, "another session is running in .*claude session other"):
            hubstart.clear(self.item(), None)
        self.assertIsNone(self.entry())
        self.assertEqual(self.calls(), [])

    # retry (Task 7)
    def fail_then_wait(self):
        self.env(FAKE_EXIT="1")
        with self.assertRaises(RelayError):
            hubstart.clear(self.item(), None)
        self.env(FAKE_EXIT="0")
        first = self.entry()["replaced_by"]
        self.put(**{**self.entry(), "at": time.time() - 660})  # 11 minutes later, no session showed up
        return first

    def test_clear_retry_after_failed_launch(self):  # R13, Review Focus 4
        self.feature("a", "old")
        self.fail_then_wait()
        out = hubstart.clear(self.item(), None)
        self.assertEqual(out["launch"], "ok")
        self.assertEqual(len(self.owner_actions("a")), 1)  # not released twice
        e = self.entry()
        self.assertEqual((e["state"], e["launch"], e["replaced_by"]), ("retired", "ok", out["session_id"]))
        self.assertIn("held: a.", self.calls()[-1]["argv"][-1])

    def test_clear_retry_releases_stale_replacement(self):  # R13
        first = self.fail_then_wait()
        self.feature("c", first)  # the late replacement took a feature after all
        out = hubstart.clear(self.item(), None)
        self.assertIsNotNone(self.handoff_on_origin("c"))
        stale = self.entry(first)
        self.assertEqual((stale["state"], stale["replaced_by"], stale["label"]),
                         ("retired", out["session_id"], "proj proj (fresh)"))
        self.assertIn("c", out["released"])

    def test_clear_retry_refuses_unpublished_on_replacement(self):
        first = self.fail_then_wait()
        self.feature("c", first, push=False)
        with self.assertRaisesRegex(RelayError, "c is not published on origin"):
            hubstart.clear(self.item(), None)
        e = self.entry()
        self.assertEqual(e["replaced_by"], first)
        self.assertNotIn("retry", e)
        self.assertEqual(len(self.calls()), 1)  # only the first, failed launch

    def test_failed_retry_then_retry(self):  # R1-2
        first = self.fail_then_wait()
        work = self.feature("c", first, push=False)
        with self.assertRaises(RelayError):
            hubstart.clear(self.item(), None)
        helpers.sh(work, "git", "push", "-qu", "origin", "HEAD")
        out = hubstart.clear(self.item(), None)
        self.assertEqual(out["launch"], "ok")
        self.assertIsNotNone(self.handoff_on_origin("c"))

    def test_partial_release_then_retry(self):
        self.feature("a", "old")
        self.feature("b", "old")
        hook = helpers.hook(self.origin, "pre-receive",
                            'while read o n r; do [ "$r" = refs/heads/feat/b ] && exit 1; done\nexit 0\n')
        with self.assertRaisesRegex(RelayError, "could not release b"):
            hubstart.clear(self.item(), None)
        os.unlink(hook)
        self.assertEqual(hubstart.held_features(self.repo, "old"), [])
        self.put(**{**self.entry(), "at": time.time() - 660})
        out = hubstart.clear(self.item(), None)
        self.assertIn("held: a, b.", self.calls()[-1]["argv"][-1])
        self.assertEqual(out["released"], ["a", "b"])

    def test_concurrent_retries(self):  # R1-1
        import threading
        self.fail_then_wait()
        gate, results = threading.Event(), []
        real = launch.run

        def slow(*args):
            gate.wait(10)
            return real(*args)

        def go():
            try:
                results.append(hubstart.clear(self.item(), None)["launch"])
            except RelayError as e:
                results.append(str(e))
        with mock.patch.object(launch, "run", side_effect=slow):
            threads = [threading.Thread(target=go) for _ in range(2)]
            for t in threads:
                t.start()
            for _ in range(100):
                if results:
                    break
                time.sleep(0.05)
            gate.set()
            for t in threads:
                t.join(20)
        self.assertEqual(sorted(results), ["ok", "proj is already being cleared"])


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
