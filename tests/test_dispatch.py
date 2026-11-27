import json
import os
import stat
import tempfile
import time
import unittest
from unittest import mock

from relaylib import dispatch, sessions
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


if __name__ == "__main__":
    unittest.main()
