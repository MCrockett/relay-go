import json
import os
import stat
import tempfile
import time
import unittest
from unittest import mock

from relaylib import dispatch, gitops, hub, notes, sessions, state
from relaylib.dispatch import DispatchError
from tests import helpers

NOW = 1_800_000_000.0


class StoreTest(unittest.TestCase):  # dispatch R5, R6, R22, R23
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.tmp = temp.name
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "h")})
        p.start()
        self.addCleanup(p.stop)

    def fields(self, **extra):
        return {"repo": {"name": "proj", "path": "/x/proj"}, "request": "do it", "owner_said": None, "slug": None,
                "requested_by": "owner", "asked_by": None, "relayed_by": None, **extra}

    def test_ids_are_unique_and_match_the_pattern(self):
        a = dispatch.create(self.fields(), now=NOW)
        b = dispatch.create(self.fields(), now=NOW)
        self.assertNotEqual(a["id"], b["id"])
        for r in (a, b):
            self.assertRegex(r["id"], r"^q[0-9a-z]+(-[0-9]+)?$")
        self.assertTrue(b["id"].endswith("-2"))

    def test_new_id_adds_a_suffix_on_a_clash(self):
        first = dispatch.new_id(1234567, set())
        self.assertEqual(dispatch.new_id(1234567, {first}), first + "-2")
        self.assertEqual(dispatch.new_id(1234567, {first, first + "-2"}), first + "-3")

    def test_folder_is_0700_and_files_0600(self):
        r = dispatch.create(self.fields(), now=NOW)
        self.assertEqual(stat.S_IMODE(os.stat(dispatch.folder()).st_mode), 0o700)
        path = os.path.join(dispatch.folder(), r["id"] + ".json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_record_holds_the_fields_and_status(self):
        r = dispatch.create(self.fields(), now=NOW)
        self.assertEqual((r["status"], r["created_at"], r["updated_at"]), ("waiting", NOW, NOW))
        self.assertEqual(dispatch.read(r["id"]), r)

    def test_a_failing_rename_leaves_the_old_file_and_no_temporary_file(self):
        r = dispatch.create(self.fields(), now=NOW)
        path = os.path.join(dispatch.folder(), r["id"] + ".json")
        with open(path) as f:
            before = f.read()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(DispatchError):
                dispatch.mutate(r["id"], lambda rec: rec.update(request="changed"))
        with open(path) as f:
            self.assertEqual(f.read(), before)
        self.assertEqual([n for n in os.listdir(dispatch.folder()) if n.startswith(".tmp-")], [])

    def test_updated_at_moves_on_every_write(self):
        r = dispatch.create(self.fields(), now=NOW)
        got = dispatch.mutate(r["id"], lambda rec: rec.update(status="cancelled"), now=NOW + 5)
        self.assertEqual((got["created_at"], got["updated_at"]), (NOW, NOW + 5))

    def test_unreadable_files_are_skipped_and_named(self):
        good = dispatch.create(self.fields(), now=NOW)
        bad = os.path.join(dispatch.folder(), "qbad.json")
        helpers.write(bad, "{nope")
        records, unreadable = dispatch.all_records()
        self.assertEqual([r["id"] for r in records], [good["id"]])
        self.assertEqual(unreadable, [bad])
        with self.assertRaises(DispatchError) as ctx:
            dispatch.read("qbad")
        self.assertIn(bad, str(ctx.exception))

    def test_read_of_a_missing_request_is_none(self):
        self.assertIsNone(dispatch.read("qzzz"))

    def test_prune_drops_only_old_finished_records(self):
        ids = {}
        for status in ("waiting", "started", "merged", "released", "cancelled"):
            r = dispatch.create(self.fields(), now=NOW)
            ids[status] = r["id"]
            dispatch.mutate(r["id"], lambda rec, s=status: rec.update(status=s), now=NOW)
        late = dispatch.create(self.fields(), now=NOW + 31 * 86400)  # a write prunes
        left = {r["id"] for r in dispatch.all_records()[0]}
        self.assertEqual(left, {ids["waiting"], ids["started"], late["id"]})

    def test_prune_keeps_a_finished_record_under_30_days(self):
        r = dispatch.create(self.fields(), now=NOW)
        dispatch.mutate(r["id"], lambda rec: rec.update(status="merged"), now=NOW)
        dispatch.create(self.fields(), now=NOW + 29 * 86400)
        self.assertIsNotNone(dispatch.read(r["id"]))

    def test_a_bad_id_is_refused_before_any_path_is_built(self):
        for bad in ("../x", "q/../a", "Q1", "", "q", "x1", "q1-"):
            with mock.patch.object(dispatch, "folder", side_effect=AssertionError("path built")):
                with self.assertRaises((DispatchError, AssertionError)) as ctx:
                    dispatch.read(bad)
            self.assertIsInstance(ctx.exception, DispatchError, bad)


class InputsTest(unittest.TestCase):  # dispatch R1, R1a, R3, R4
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.tmp = os.path.realpath(temp.name)
        self.root = os.path.join(self.tmp, "p")
        os.makedirs(self.root)
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "h"), "RELAY_ROOT": self.root,
                                         "HOME": self.tmp, "RELAY_CONFIG": os.path.join(self.tmp, "none.toml")})
        p.start()
        self.addCleanup(p.stop)
        self.target = self.make("target")
        self.other = self.make("other")

    def make(self, name):
        sub = os.path.join(self.tmp, "mk-" + name)
        os.makedirs(sub)
        _, work = helpers.make_repo(sub)
        dest = os.path.join(self.root, name)
        os.rename(work, dest)
        return dest

    # target_repo
    def test_target_repo_returns_the_real_path(self):
        self.assertEqual(dispatch.target_repo("target"), self.target)

    def test_target_repo_refuses_what_is_not_a_repo_under_the_root(self):
        os.makedirs(os.path.join(self.root, "plain"))
        os.makedirs(os.path.join(self.target, "sub"))
        for name in ("plain", "nothere", "../p", "target/sub", "..", ".hidden", "", "a/b"):
            with self.assertRaises(DispatchError) as ctx:
                dispatch.target_repo(name)
            self.assertEqual(str(ctx.exception), f"{name} is not a repository under {self.root}")

    # clean_text
    def test_clean_text_removes_control_characters_and_keeps_lines(self):
        self.assertEqual(dispatch.clean_text("a\x00b\x1b[31m\nc\td\r\n", "the request"), "ab[31m\nc\td\n")

    def test_clean_text_refuses_empty_and_too_long(self):
        for bad in ("", "  \n", "\x00\x01"):
            with self.assertRaises(DispatchError) as ctx:
                dispatch.clean_text(bad, "the request")
            self.assertEqual(str(ctx.exception), "the request cannot be empty")
        self.assertEqual(len(dispatch.clean_text("x" * 4000, "the request")), 4000)
        with self.assertRaises(DispatchError) as ctx:
            dispatch.clean_text("x" * 4001, "the owner's words")
        self.assertIn("at most 4,000 characters", str(ctx.exception))

    # check_slug
    def test_check_slug(self):
        self.assertEqual(dispatch.check_slug("clip-hook"), "clip-hook")
        for bad in ("Clip", "-a", "a b", "a_b", ""):
            with self.assertRaises(DispatchError):
                dispatch.check_slug(bad)

    # main_checkout
    def test_main_checkout_of_a_worktree_is_its_main_checkout(self):
        wt = os.path.join(self.tmp, "wt")
        helpers.sh(self.target, "git", "worktree", "add", "-q", "-b", "feat/x", wt)
        self.assertEqual(dispatch.main_checkout(wt), self.target)
        self.assertEqual(dispatch.main_checkout(self.target), self.target)
        self.assertEqual(dispatch.main_checkout(os.path.join(wt, ".")), self.target)

    # resolve_asker
    def session(self, sid, cwd, provider="claude"):
        return helpers.live_session(provider, sid, cwd=cwd)

    def test_an_asker_by_session_id_needs_a_valid_record(self):
        self.session("A1", self.other)
        got = dispatch.resolve_asker("claude:A1", {"provider": "claude", "session_id": "hub"}, self.target)
        self.assertEqual(got, {"provider": "claude", "session_id": "A1", "repo": "other"})
        with self.assertRaises(DispatchError) as ctx:
            dispatch.resolve_asker("claude:nope", None, self.target)
        self.assertIn("unknown session claude:nope", str(ctx.exception))
        with self.assertRaises(DispatchError):
            dispatch.resolve_asker("codex:A1", None, self.target)

    def test_a_repo_name_resolves_to_its_one_live_session(self):
        self.session("A1", self.other)
        got = dispatch.resolve_asker("other", None, self.target)
        self.assertEqual((got["provider"], got["session_id"], got["repo"]), ("claude", "A1", "other"))

    def test_a_worktree_session_counts_for_its_repo(self):
        wt = os.path.join(self.tmp, "wt")
        helpers.sh(self.other, "git", "worktree", "add", "-q", "-b", "feat/x", wt)
        self.session("W1", os.path.join(wt))
        got = dispatch.resolve_asker("other", None, self.target)
        self.assertEqual((got["session_id"], got["repo"]), ("W1", "other"))

    def test_a_repo_with_no_live_session_is_refused(self):
        helpers.live_session("claude", "E1", cwd=self.other, state="ended")
        with self.assertRaises(DispatchError) as ctx:
            dispatch.resolve_asker("other", None, self.target)
        self.assertIn("no live session in other", str(ctx.exception))

    def test_a_repo_with_two_live_sessions_is_refused_naming_them(self):
        self.session("A1", self.other)
        self.session("A2", self.other, provider="codex")
        with self.assertRaises(DispatchError) as ctx:
            dispatch.resolve_asker("other", None, self.target)
        self.assertIn("more than one live session in other", str(ctx.exception))
        self.assertIn("claude:A1", str(ctx.exception))
        self.assertIn("codex:A2", str(ctx.exception))

    def test_the_hub_and_a_session_in_the_target_cannot_be_named(self):
        self.session("HUB", self.other)
        with self.assertRaises(DispatchError) as ctx:
            dispatch.resolve_asker("claude:HUB", {"provider": "claude", "session_id": "HUB"}, self.target)
        self.assertIn("hub itself", str(ctx.exception))
        self.session("T1", self.target)
        with self.assertRaises(DispatchError) as ctx:
            dispatch.resolve_asker("claude:T1", None, self.target)
        self.assertIn("inside target", str(ctx.exception))
        with self.assertRaises(DispatchError):
            dispatch.resolve_asker("target", None, self.target)

    def test_an_unknown_repo_name_is_refused(self):
        with self.assertRaises(DispatchError):
            dispatch.resolve_asker("nothere", None, self.target)


def add_feature(repo, branch, slug, rid, base="develop", push=False, own_branch=True):
    """Commit docs/relay/<slug>/state.md (a request record for `rid`) on `branch`, started from `base`."""
    helpers.sh(repo, "git", "switch", "-q", "-c", branch, base)
    st = state.new_state(slug, "r", {"provider": "claude", "session": "S1", "worktree": "r", "since": "x"},
                         branch if own_branch else "feat/other")
    st["request"] = {"id": rid, "requested_by": "owner", "asked_by": None, "relayed_by": None, "at": "x"}
    state.write_state(state.state_path(repo, slug), st)
    helpers.sh(repo, "git", "add", "-A")
    helpers.sh(repo, "git", "commit", "-q", "-m", f"relay: new {slug}")
    if push:
        helpers.sh(repo, "git", "push", "-q", "-u", "origin", branch)
    helpers.sh(repo, "git", "switch", "-q", "develop")


class FeaturesWithTest(unittest.TestCase):  # dispatch R18 step 1
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.tmp = os.path.realpath(temp.name)
        self.origin, self.work = helpers.make_repo(self.tmp)
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "h"), "HOME": self.tmp})
        p.start()
        self.addCleanup(p.stop)

    def test_found_on_origin(self):
        add_feature(self.work, "feat/a", "a", "q1", push=True)
        helpers.sh(self.work, "git", "branch", "-q", "-D", "feat/a")
        self.assertEqual(dispatch.features_with(self.work, "q1"), [("a", "origin/feat/a", "origin")])

    def test_found_only_on_a_local_branch_that_is_not_checked_out(self):
        add_feature(self.work, "feat/a", "a", "q1")
        self.assertEqual(dispatch.features_with(self.work, "q1"), [("a", "feat/a", "local")])

    def test_found_in_a_worktree_including_uncommitted_state(self):
        wt = os.path.join(self.tmp, "wt")
        helpers.sh(self.work, "git", "worktree", "add", "-q", "-b", "feat/w", wt, "develop")
        st = state.new_state("w", "r", {}, "feat/w")
        st["request"] = {"id": "q2", "requested_by": "owner", "asked_by": None, "relayed_by": None, "at": "x"}
        state.write_state(state.state_path(wt, "w"), st)
        self.assertEqual(dispatch.features_with(self.work, "q2"), [("w", "feat/w", "local")])

    def test_found_only_on_develop_after_the_branch_was_deleted(self):
        add_feature(self.work, "feat/a", "a", "q1")
        helpers.sh(self.work, "git", "merge", "-q", "--no-ff", "-m", "merge", "feat/a")
        helpers.sh(self.work, "git", "push", "-q", "origin", "develop")
        helpers.sh(self.work, "git", "branch", "-q", "-D", "feat/a")
        self.assertEqual(dispatch.features_with(self.work, "q1"), [("a", "origin/develop", "origin")])

    def test_a_feature_on_its_own_and_a_stacked_branch_is_reported_once_with_its_own_branch(self):
        add_feature(self.work, "feat/a", "a", "q1", push=True)
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/b", "feat/a")
        helpers.write(os.path.join(self.work, "b.txt"), "b\n")
        helpers.sh(self.work, "git", "add", "-A")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "b")
        helpers.sh(self.work, "git", "push", "-q", "-u", "origin", "feat/b")
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        self.assertEqual(dispatch.features_with(self.work, "q1"), [("a", "origin/feat/a", "origin")])

    def test_origin_wins_over_a_local_copy(self):
        add_feature(self.work, "feat/a", "a", "q1", push=True)
        self.assertEqual(dispatch.features_with(self.work, "q1"), [("a", "origin/feat/a", "origin")])

    def test_nothing_found_and_other_ids_ignored(self):
        add_feature(self.work, "feat/a", "a", "q1", push=True)
        self.assertEqual(dispatch.features_with(self.work, "q9"), [])

    def test_a_fetch_failure_raises_with_gits_message(self):
        helpers.sh(self.work, "git", "remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        with self.assertRaises(DispatchError) as ctx:
            dispatch.features_with(self.work, "q1")
        self.assertIn("cannot fetch origin", str(ctx.exception))

    def test_local_branches(self):
        helpers.sh(self.work, "git", "branch", "feat/x")
        self.assertEqual(gitops.local_branches(self.work), ["develop", "feat/x"])


class ClaimTest(unittest.TestCase):  # dispatch R8, R18 steps 2 to 5, R19
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.tmp = os.path.realpath(temp.name)
        self.origin, self.work = helpers.make_repo(self.tmp)
        env = {"RELAY_HOME": os.path.join(self.tmp, "h"), "HOME": self.tmp,
               "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "c"), "CODEX_HOME": os.path.join(self.tmp, "x")}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.hub = {"provider": "claude", "session_id": "HUB"}
        self.asker = {"provider": "claude", "session_id": "ASK", "repo": "other"}
        helpers.live_session("claude", "HUB")
        helpers.live_session("claude", "ASK")
        self.a = {"provider": "claude", "session_id": "A"}
        self.b = {"provider": "codex", "session_id": "B"}
        self.rec = dispatch.create({"repo": {"name": "work", "path": self.work}, "request": "do it",
                                    "owner_said": None, "slug": "s", "requested_by": self.hub,
                                    "asked_by": self.asker, "relayed_by": "claude session HUB"}, now=NOW)
        self.id = self.rec["id"]

    def status(self):
        return dispatch.read(self.id)

    def feature(self, **extra):
        return {"slug": "s", "branch": "feat/s", "provider": "claude", "session_id": "A", "at": NOW, **extra}

    def texts(self, sid):
        return [n["text"] for n in notes.list_for("claude", sid)]

    def hub_lines(self):
        try:
            with open(os.path.join(hub.folder(), "log.jsonl")) as f:
                return [json.loads(x) for x in f]
        except OSError:
            return []

    # claim
    def test_claim_writes_the_claim(self):
        helpers.live_session("claude", "A")
        got = dispatch.claim(self.id, self.a, "s", "feat/s", NOW + 1)
        self.assertEqual(got["claim"], {"provider": "claude", "session_id": "A", "slug": "s", "branch": "feat/s",
                                        "at": NOW + 1, "publishing": False})

    def test_a_second_live_session_is_refused(self):
        helpers.live_session("claude", "A")
        helpers.live_session("codex", "B")
        dispatch.claim(self.id, self.a, "s", "feat/s", NOW)
        with self.assertRaises(DispatchError) as ctx:
            dispatch.claim(self.id, self.b, "s", "feat/s", NOW + 60)
        self.assertEqual(str(ctx.exception), f"request {self.id} is being started by claude session A")

    def test_the_same_session_may_claim_again(self):
        helpers.live_session("claude", "A")
        dispatch.claim(self.id, self.a, "s", "feat/s", NOW)
        dispatch.claim(self.id, self.a, "t", "feat/t", NOW + 5)
        self.assertEqual(self.status()["claim"]["slug"], "t")

    def test_a_gone_session_and_a_31_minute_old_claim_can_be_taken_over(self):
        dispatch.claim(self.id, self.a, "s", "feat/s", NOW)          # A has no session record: gone
        dispatch.claim(self.id, self.b, "s", "feat/s", NOW + 10)
        self.assertEqual(self.status()["claim"]["session_id"], "B")
        helpers.live_session("codex", "B")
        with self.assertRaises(DispatchError):
            dispatch.claim(self.id, self.a, "s", "feat/s", NOW + 20)
        dispatch.claim(self.id, self.a, "s", "feat/s", NOW + 10 + 31 * 60)
        self.assertEqual(self.status()["claim"]["session_id"], "A")

    def test_only_a_waiting_request_can_be_claimed(self):
        dispatch.mutate(self.id, lambda r: r.update(status="cancelled"))
        with self.assertRaises(DispatchError) as ctx:
            dispatch.claim(self.id, self.a, "s", "feat/s", NOW)
        self.assertEqual(str(ctx.exception), f"request {self.id} was cancelled")
        with self.assertRaises(DispatchError):
            dispatch.claim("qnone", self.a, "s", "feat/s", NOW)

    def test_a_publishing_claim_cannot_be_taken_over_at_any_age(self):
        helpers.live_session("claude", "A", state="ended")
        dispatch.claim(self.id, self.a, "s", "feat/s", NOW)
        dispatch.enter_publishing(self.id, self.a)
        with self.assertRaises(DispatchError):
            dispatch.claim(self.id, self.b, "s", "feat/s", NOW + 10 * 86400)
        self.assertTrue(self.status()["claim"]["publishing"])

    # enter_publishing, push_failed, drop_claim
    def test_enter_publishing_sets_the_flag(self):
        dispatch.claim(self.id, self.a, "s", "feat/s", NOW)
        self.assertTrue(dispatch.enter_publishing(self.id, self.a)["claim"]["publishing"])

    def test_enter_publishing_fails_after_a_cancel_and_after_a_takeover(self):
        dispatch.claim(self.id, self.a, "s", "feat/s", NOW)
        dispatch.mutate(self.id, lambda r: r.update(status="cancelled", claim=None))
        with self.assertRaises(DispatchError) as ctx:
            dispatch.enter_publishing(self.id, self.a)
        self.assertEqual(str(ctx.exception), f"request {self.id} was cancelled")
        dispatch.mutate(self.id, lambda r: r.update(status="waiting"))
        dispatch.claim(self.id, self.b, "s", "feat/s", NOW + 10)
        with self.assertRaises(DispatchError) as ctx:
            dispatch.enter_publishing(self.id, self.a)
        self.assertEqual(str(ctx.exception), f"request {self.id} was taken over by codex session B")

    def test_push_failed_keeps_the_claim_and_drop_claim_removes_it(self):
        dispatch.claim(self.id, self.a, "s", "feat/s", NOW)
        dispatch.enter_publishing(self.id, self.a)
        dispatch.push_failed(self.id, self.a)
        self.assertEqual(self.status()["claim"]["publishing"], False)
        self.assertEqual(self.status()["claim"]["session_id"], "A")
        dispatch.drop_claim(self.id, self.b)        # not this session's: kept
        self.assertIsNotNone(self.status().get("claim"))
        dispatch.drop_claim(self.id, self.a)
        self.assertIsNone(self.status().get("claim"))

    # mark_started and published
    def test_published_marks_started_and_tells_each_requester_once(self):
        dispatch.claim(self.id, self.a, "s", "feat/s", NOW)
        dispatch.enter_publishing(self.id, self.a)
        warnings = []
        dispatch.published(self.id, self.a, self.feature(), warnings)
        r = self.status()
        self.assertEqual(r["status"], "started")
        self.assertNotIn("claim", r)
        self.assertEqual(r["started"], {"provider": "claude", "session_id": "A", "slug": "s", "branch": "feat/s",
                                        "at": NOW})
        text = f"relay: request {self.id} was started in work as s by claude session A."
        self.assertEqual(self.texts("HUB"), [text])
        self.assertEqual(self.texts("ASK"), [text])
        line = [x for x in self.hub_lines() if x.get("event") == "started"]
        self.assertEqual(len(line), 1)
        self.assertEqual((line[0]["command"], line[0]["repo"], line[0]["slug"]), ("event", "work", "s"))
        self.assertEqual(warnings, [])

    def test_mark_started_twice_sends_one_note_and_one_log_line(self):
        self.assertTrue(dispatch.mark_started(self.id, self.feature()))
        self.assertFalse(dispatch.mark_started(self.id, self.feature(slug="again")))
        self.assertEqual(len(self.texts("HUB")), 1)
        self.assertEqual(len(self.texts("ASK")), 1)
        self.assertEqual(len([x for x in self.hub_lines() if x.get("event") == "started"]), 1)
        self.assertEqual(self.status()["started"]["slug"], "s")

    def test_an_owner_dispatched_request_with_no_asker_tells_nobody(self):
        r = dispatch.create({"repo": {"name": "work", "path": self.work}, "request": "x", "owner_said": None,
                             "slug": None, "requested_by": "owner", "asked_by": None, "relayed_by": None},
                            now=NOW + 1)
        self.assertTrue(dispatch.mark_started(r["id"], self.feature()))
        self.assertEqual(self.texts("HUB"), [])

    def test_a_failed_note_is_a_warning(self):
        warnings = []
        with mock.patch.object(notes, "send", side_effect=OSError("boom")):
            dispatch.mark_started(self.id, self.feature(), warnings)
        self.assertEqual(self.status()["status"], "started")
        self.assertEqual(len(warnings), 2)
        self.assertIn("could not send the event to claude session", warnings[0])

    # reconcile_dead
    def make_dead_publisher(self, slug="s"):
        dispatch.claim(self.id, self.a, slug, f"feat/{slug}", NOW)   # no record for A: gone
        dispatch.enter_publishing(self.id, self.a)

    def test_reconcile_dead_marks_started_when_origin_has_the_feature(self):
        add_feature(self.work, "feat/s", "s", self.id, push=True)
        self.make_dead_publisher()
        warnings = []
        got = dispatch.reconcile_dead(self.id, NOW + 5, warnings)
        self.assertEqual((got["slug"], got["where"]), ("s", "origin"))
        r = self.status()
        self.assertEqual((r["status"], r["started"]["slug"], r["started"]["session_id"]), ("started", "s", "S1"))
        self.assertNotIn("claim", r)
        self.assertEqual(len(self.texts("HUB")), 1)

    def test_reconcile_dead_clears_publishing_when_origin_has_nothing(self):
        self.make_dead_publisher()
        self.assertIsNone(dispatch.reconcile_dead(self.id, NOW + 5))
        r = self.status()
        self.assertEqual((r["status"], r["claim"]["publishing"]), ("waiting", False))
        dispatch.claim(self.id, self.b, "s", "feat/s", NOW + 6)   # now it can be taken over

    def test_reconcile_dead_leaves_a_live_publisher_alone(self):
        helpers.live_session("claude", "A")
        self.make_dead_publisher()
        self.assertIsNone(dispatch.reconcile_dead(self.id, NOW + 5))
        self.assertTrue(self.status()["claim"]["publishing"])

    def test_reconcile_dead_leaves_the_claim_when_the_fetch_fails(self):
        self.make_dead_publisher()
        helpers.sh(self.work, "git", "remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        with self.assertRaises(DispatchError) as ctx:
            dispatch.reconcile_dead(self.id, NOW + 5)
        self.assertIn("cannot fetch origin", str(ctx.exception))
        self.assertTrue(self.status()["claim"]["publishing"])

    def test_reconcile_dead_ignores_a_request_with_no_publishing_claim(self):
        self.assertIsNone(dispatch.reconcile_dead(self.id, NOW))
        dispatch.claim(self.id, self.a, "s", "feat/s", NOW)
        self.assertIsNone(dispatch.reconcile_dead(self.id, NOW))
        self.assertIsNotNone(self.status().get("claim"))

    def test_reconcile_then_a_start_sends_each_requester_one_note(self):
        add_feature(self.work, "feat/s", "s", self.id, push=True)
        self.make_dead_publisher()
        dispatch.reconcile_dead(self.id, NOW + 5)
        found = dispatch.find_features(self.work, self.id)
        self.assertFalse(dispatch.mark_started(self.id, found[0]))
        self.assertEqual(len(self.texts("HUB")), 1)
        self.assertEqual(len(self.texts("ASK")), 1)


if __name__ == "__main__":
    unittest.main()
