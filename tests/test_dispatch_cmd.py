import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from relaylib import commands, dispatch, hub, identity, notes
from tests import helpers
from tests.test_dispatch import add_feature
from tests.test_notes import FakeInbox

OWNER_ENV = dict.fromkeys(identity.AGENT_MARKERS + ("RELAY_SESSION",), "")


class DispatchCommandTest(unittest.TestCase):  # dispatch R1, R2, R3, R7, R8, R13
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")  # short: macOS caps socket paths near 104 bytes
        self.addCleanup(temp.cleanup)
        self.tmp = os.path.realpath(temp.name)
        self.root = os.path.join(self.tmp, "p")
        os.makedirs(self.root)
        self.target = self.make("target")
        self.other = self.make("other")
        env = {"RELAY_HOME": os.path.join(self.tmp, "h"), "RELAY_ROOT": self.root, "HOME": self.tmp,
               "RELAY_CONFIG": os.path.join(self.tmp, "none.toml"), "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "c"),
               "CODEX_HOME": os.path.join(self.tmp, "x"), "CLAUDECODE": "1", "RELAY_PROVIDER": "claude",
               "RELAY_SESSION": "hub1"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.now = time.time()
        self.register("hub1")

    def make(self, name):
        sub = os.path.join(self.tmp, "mk-" + name)
        os.makedirs(sub)
        _, work = helpers.make_repo(sub)
        dest = os.path.join(self.root, name)
        os.rename(work, dest)
        return dest

    def register(self, *ids):
        os.makedirs(hub.folder(), exist_ok=True)
        with open(os.path.join(hub.folder(), "sessions.json"), "w") as f:
            json.dump([{"provider": "claude", "session_id": s, "at": self.now} for s in ids], f)

    def relay(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), redirect_stdout(out), redirect_stderr(err):
            rc = commands.main(["dispatch", *argv])
        self.out, self.err = out.getvalue(), err.getvalue()
        return rc

    def dispatch(self, *extra, repo="target", request="Add the clip hook.", said="please add the clip hook"):
        return self.relay(repo, request, "--owner-said", said, "--relayed", *extra)

    def records(self):
        return dispatch.all_records()[0]

    def logged(self):
        with open(os.path.join(hub.folder(), "log.jsonl")) as f:
            return [json.loads(line) for line in f]

    def asker(self, sid="A1", open_inbox=False):
        helpers.live_session("claude", sid, cwd=self.other)
        if not open_inbox:
            return None
        folder = os.path.join(self.tmp, "sock-" + sid)
        os.makedirs(folder)
        fake = FakeInbox(folder)
        self.addCleanup(fake.close)
        path = os.path.join(self.tmp, "c")  # keep the record valid and add the inbox
        from relaylib import sessions
        with open(sessions.record_path("claude", sid)) as f:
            record = json.load(f)
        record["inbox"] = fake.path
        with open(sessions.record_path("claude", sid), "w") as f:
            json.dump(record, f)
        return fake

    # create
    def test_a_registered_hub_dispatches_and_the_file_holds_every_field(self):
        self.assertEqual(self.dispatch("--slug", "clip-hook"), 0, self.err)
        (r,) = self.records()
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["repo"], {"name": "target", "path": self.target})
        self.assertEqual((r["request"], r["owner_said"], r["slug"]), ("Add the clip hook.",
                                                                      "please add the clip hook", "clip-hook"))
        self.assertEqual(r["requested_by"], {"provider": "claude", "session_id": "hub1"})
        self.assertEqual(r["relayed_by"], "claude session hub1")
        self.assertIsNone(r["asked_by"])
        for key in ("id", "created_at", "updated_at"):
            self.assertTrue(r[key])
        self.assertIn(r["id"], self.out)
        self.assertIn(f"relay take --request {r['id']}", self.out)
        self.assertIn(f"relay new clip-hook --request {r['id']}", self.out)

    def test_without_a_slug_the_output_offers_only_relay_new(self):
        self.assertEqual(self.dispatch(), 0, self.err)
        self.assertNotIn("relay take", self.out)
        self.assertIn("relay new <slug> --request", self.out)

    def test_an_unregistered_agent_is_refused_and_nothing_is_recorded(self):
        self.register("someone-else")
        self.assertEqual(self.dispatch(), 1)
        self.assertIn("only a registered hub dispatches; run relay hub first", self.err)
        self.assertEqual(self.records(), [])

    def test_relayed_from_the_owners_terminal_and_a_missing_relayed_are_refused(self):
        self.assertEqual(self.relay("target", "x", "--owner-said", "y", "--relayed", env=OWNER_ENV), 1)
        self.assertIn("in your own terminal", self.err)
        self.assertEqual(self.relay("target", "x", "--owner-said", "y"), 1)
        self.assertIn("owner-only", self.err)
        self.assertEqual(self.records(), [])

    def test_the_owner_dispatches_from_their_terminal_without_owner_said(self):
        self.assertEqual(self.relay("target", "Add the clip hook.", env=OWNER_ENV), 0, self.err)
        (r,) = self.records()
        self.assertEqual((r["requested_by"], r["relayed_by"], r["owner_said"]), ("owner", None, None))

    def test_owner_said_is_required_when_relayed(self):
        self.assertEqual(self.relay("target", "x", "--relayed"), 1)
        self.assertIn("--owner-said", self.err)
        self.assertEqual(self.records(), [])

    def test_text_is_cleaned_and_limited(self):
        self.assertEqual(self.dispatch(request="a\x00b"), 0, self.err)
        self.assertEqual(self.records()[0]["request"], "ab")
        self.assertEqual(self.dispatch(request="x" * 4001), 1)
        self.assertIn("at most 4,000", self.err)
        self.assertEqual(self.dispatch(said="  "), 1)
        self.assertEqual(self.dispatch(request=""), 1)
        self.assertEqual(len(self.records()), 1)

    def test_a_bad_repo_or_slug_is_refused(self):
        self.assertEqual(self.dispatch(repo="nothere"), 1)
        self.assertIn(f"nothere is not a repository under {self.root}", self.err)
        self.assertEqual(self.dispatch("--slug", "Bad Slug"), 1)
        self.assertEqual(self.records(), [])

    def test_the_asker_is_recorded_and_gets_one_queued_note(self):
        self.asker("A1")
        self.assertEqual(self.dispatch("--for", "claude:A1"), 0, self.err)
        (r,) = self.records()
        self.assertEqual(r["asked_by"], {"provider": "claude", "session_id": "A1", "repo": "other"})
        got = notes.list_for("claude", "A1")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["status"], "queued")
        self.assertEqual(got[0]["text"], f"relay: your request was dispatched to target as {r['id']} by the hub; "
                                         "you will be told when it is started and when it merges.")
        self.assertEqual(got[0]["prefix"], "relay event:")

    def test_an_open_asker_gets_the_note_posted(self):
        fake = self.asker("A1", open_inbox=True)
        self.assertEqual(self.dispatch("--for", "other"), 0, self.err)
        self.assertEqual(len(fake.wait(1)), 1)

    def test_a_bad_asker_refuses_the_dispatch(self):
        self.assertEqual(self.dispatch("--for", "claude:ghost"), 1)
        self.assertIn("unknown session", self.err)
        self.assertEqual(self.records(), [])

    def test_the_hub_log_line_is_written(self):
        self.assertEqual(self.dispatch(), 0, self.err)
        (r,) = self.records()
        line = [x for x in self.logged() if x.get("command") == "dispatch"][0]
        self.assertEqual((line["id"], line["repo"], line["result"]), (r["id"], "target", "recorded"))

    def test_a_refusal_is_logged(self):
        self.assertEqual(self.dispatch(repo="nothere"), 1)
        line = self.logged()[-1]
        self.assertEqual(line["command"], "dispatch")
        self.assertTrue(line["result"].startswith("refused:"))

    def test_an_unwritable_folder_exits_1_and_leaves_no_record(self):
        os.makedirs(os.environ["RELAY_HOME"], exist_ok=True)
        with open(os.path.join(os.environ["RELAY_HOME"], "dispatch"), "w") as f:
            f.write("in the way")
        self.assertEqual(self.dispatch(), 1)
        self.assertIn("could not record the request", self.err)
        with open(os.path.join(os.environ["RELAY_HOME"], "dispatch")) as f:
            self.assertEqual(f.read(), "in the way")

    def test_a_failed_hub_log_is_a_warning_and_the_request_stands(self):
        with mock.patch.object(hub, "log", return_value="could not write the hub log: boom"):
            self.assertEqual(self.dispatch(), 0, self.err)
        self.assertIn("warning: could not write the hub log: boom", self.err)
        self.assertEqual(len(self.records()), 1)

    def test_a_failed_note_to_the_asker_is_a_warning_and_the_request_stands(self):
        self.asker("A1")
        with mock.patch.object(notes, "send", side_effect=OSError("boom")):
            self.assertEqual(self.dispatch("--for", "claude:A1"), 0, self.err)
        self.assertIn("warning: could not send the event to claude session A1", self.err)
        self.assertEqual(len(self.records()), 1)

    # cancel
    def test_cancel_a_waiting_request(self):
        self.dispatch()
        rid = self.records()[0]["id"]
        self.assertEqual(self.relay("--cancel", rid, "--relayed"), 0, self.err)
        self.assertEqual(dispatch.read(rid)["status"], "cancelled")
        self.assertIn(rid, self.out)
        line = self.logged()[-1]
        self.assertEqual((line["command"], line["id"], line["result"]), ("dispatch", rid, "cancelled"))

    def test_cancel_by_the_owner_without_relayed(self):
        self.dispatch()
        rid = self.records()[0]["id"]
        self.assertEqual(self.relay("--cancel", rid, env=OWNER_ENV), 0, self.err)

    def test_cancel_refuses_what_is_not_waiting_naming_its_status(self):
        self.dispatch()
        rid = self.records()[0]["id"]
        dispatch.mutate(rid, lambda r: r.update(status="started"))
        self.assertEqual(self.relay("--cancel", rid, "--relayed"), 1)
        self.assertIn(f"request {rid} is started, not waiting", self.err)
        self.assertEqual(dispatch.read(rid)["status"], "started")

    def test_cancel_rules_who_may_and_unknown_ids(self):
        self.dispatch()
        rid = self.records()[0]["id"]
        self.register("someone-else")
        self.assertEqual(self.relay("--cancel", rid, "--relayed"), 1)
        self.assertIn("only a registered hub dispatches", self.err)
        self.register("hub1")
        self.assertEqual(self.relay("--cancel", rid), 1)
        self.assertEqual(self.relay("--cancel", "qnone", "--relayed"), 1)
        self.assertIn("no request qnone on this machine", self.err)
        self.assertEqual(self.relay("--cancel", "../x", "--relayed"), 1)
        self.assertEqual(dispatch.read(rid)["status"], "waiting")

    # cancel and claims
    def claimed(self, session="A", live=True, publishing=False, slug="s"):
        self.dispatch("--slug", slug)
        rid = self.records()[0]["id"]
        if live:
            helpers.live_session("claude", session, cwd=self.target)
        me = {"provider": "claude", "session_id": session}
        dispatch.claim(rid, me, slug, f"feat/{slug}", time.time())
        if publishing:
            dispatch.enter_publishing(rid, me)
        return rid

    def test_cancel_refuses_a_live_claim(self):
        rid = self.claimed()
        self.assertEqual(self.relay("--cancel", rid, "--relayed"), 1)
        self.assertIn(f"request {rid} is being started by claude session A", self.err)
        self.assertEqual(dispatch.read(rid)["status"], "waiting")

    def test_cancel_removes_a_claim_that_may_be_taken_over(self):
        rid = self.claimed(live=False)
        self.assertEqual(self.relay("--cancel", rid, "--relayed"), 0, self.err)
        r = dispatch.read(rid)
        self.assertEqual(r["status"], "cancelled")
        self.assertNotIn("claim", r)

    def test_cancel_removes_a_stale_live_claim(self):
        self.dispatch()
        rid = self.records()[0]["id"]
        helpers.live_session("claude", "A", cwd=self.target)
        dispatch.claim(rid, {"provider": "claude", "session_id": "A"}, "s", "feat/s", time.time() - 31 * 60)
        self.assertEqual(self.relay("--cancel", rid, "--relayed"), 0, self.err)

    def test_cancel_refuses_a_live_publisher_at_any_age(self):
        rid = self.claimed(publishing=True)
        dispatch.mutate(rid, lambda r: r["claim"].update(at=time.time() - 9 * 86400))
        self.assertEqual(self.relay("--cancel", rid, "--relayed"), 1)
        self.assertIn("is being started by claude session A", self.err)
        self.assertEqual(dispatch.read(rid)["status"], "waiting")

    def test_cancel_of_a_dead_publisher_whose_feature_is_on_origin_is_refused_and_marks_started(self):
        rid = self.claimed(live=False, publishing=True)
        add_feature(self.target, "feat/s", "s", rid, push=True)
        self.assertEqual(self.relay("--cancel", rid, "--relayed"), 1)
        self.assertIn(f"request {rid} was started as s in target", self.err)
        self.assertEqual(dispatch.read(rid)["status"], "started")
        self.assertEqual(self.relay("--cancel", rid, "--relayed"), 1)  # and a second cancel does not renotify
        self.assertIn("is started, not waiting", self.err)

    def test_cancel_of_a_dead_publisher_with_nothing_on_origin_works(self):
        rid = self.claimed(live=False, publishing=True)
        self.assertEqual(self.relay("--cancel", rid, "--relayed"), 0, self.err)
        r = dispatch.read(rid)
        self.assertEqual(r["status"], "cancelled")
        self.assertNotIn("claim", r)

    # list
    def test_list_shows_waiting_and_started_oldest_first(self):
        ids = []
        for n, text in enumerate(("one", "two", "three", "four")):
            ids.append(dispatch.create({"repo": {"name": "target", "path": self.target}, "request": text,
                                        "owner_said": "q", "slug": None, "requested_by": "owner",
                                        "asked_by": None, "relayed_by": None}, now=self.now + n)["id"])
        dispatch.mutate(ids[1], lambda r: r.update(status="started"))
        dispatch.mutate(ids[2], lambda r: r.update(status="cancelled"))
        self.assertEqual(self.relay("--list", "--json"), 0, self.err)
        got = json.loads(self.out)
        self.assertEqual([r["id"] for r in got], [ids[0], ids[1], ids[3]])
        self.assertEqual(self.relay("--list"), 0)
        self.assertEqual([ids[0] in self.out, ids[2] in self.out], [True, False])
        self.assertLess(self.out.index(ids[0]), self.out.index(ids[3]))

    def test_list_names_an_unreadable_file_on_stderr(self):
        self.dispatch()
        bad = os.path.join(dispatch.folder(), "qbad.json")
        helpers.write(bad, "{")
        self.assertEqual(self.relay("--list", "--json"), 0, self.err)
        self.assertEqual(len(json.loads(self.out)), 1)
        self.assertIn(bad, self.err)

    def test_list_is_open_to_any_session(self):
        self.register("someone-else")
        self.assertEqual(self.relay("--list", "--json"), 0, self.err)
        self.assertEqual(json.loads(self.out), [])

    # show
    def test_show_prints_everything(self):
        self.asker("A1")
        self.dispatch("--slug", "clip-hook", "--for", "claude:A1")
        rid = self.records()[0]["id"]
        self.register("someone-else")
        self.assertEqual(self.relay("--show", rid), 0, self.err)
        for part in (rid, "waiting", "target", self.target, "Add the clip hook.", "please add the clip hook",
                     "clip-hook", "claude session hub1", "claude session A1", "other"):
            self.assertIn(part, self.out)

    def test_show_of_an_unknown_request_is_refused(self):
        self.assertEqual(self.relay("--show", "qnone"), 1)
        self.assertIn("no request qnone on this machine", self.err)

    # arguments
    def test_the_forms_do_not_mix(self):
        self.assertEqual(self.relay("--list", "--show", "q1"), 1)
        self.assertEqual(self.relay("--cancel", "q1", "target", "x", "--relayed"), 1)
        self.assertEqual(self.relay("--list", "--slug", "a"), 1)
        self.assertEqual(self.relay("target"), 1)
        self.assertEqual(self.relay(), 1)


if __name__ == "__main__":
    unittest.main()
