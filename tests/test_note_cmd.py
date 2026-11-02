import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from relaylib import commands, hub, notes, sessions
from tests.test_notes import FakeInbox

NOTE_PREFIX = "Note from the owner, sent with relay note:"


class NoteCommandTest(unittest.TestCase):  # hub-note R1 to R13
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")  # short: macOS caps socket paths near 104 bytes
        self.addCleanup(temp.cleanup)
        self.tmp = temp.name
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "h")})
        p.start()
        self.addCleanup(p.stop)
        self.now = time.time()

    def relay(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = commands.main(["note", *argv])
        self.out, self.err = out.getvalue(), err.getvalue()
        return rc

    def register(self, *pairs):
        os.makedirs(hub.folder(), exist_ok=True)
        with open(os.path.join(hub.folder(), "sessions.json"), "w") as f:
            json.dump([{"provider": p, "session_id": s, "at": self.now} for p, s in pairs], f)

    def record(self, sid, provider="claude", state="waiting", inbox=None):
        r = {"provider": provider, "session_id": sid, "state": state, "since": self.now - 5, "at": self.now - 5,
             "event": "Stop", "pending": [], "cwd": None}
        if inbox:
            r["inbox"] = inbox
        os.makedirs(sessions.folder(), exist_ok=True)
        with open(sessions.record_path(provider, sid), "w") as f:
            json.dump(r, f)

    def open_session(self, sid):
        folder = os.path.join(self.tmp, "sock-" + sid)
        os.makedirs(folder)
        fake = FakeInbox(folder)
        self.addCleanup(fake.close)
        self.record(sid, inbox=fake.path)
        return fake

    def log(self, provider, sid):
        return notes.list_for(provider, sid)

    def no_logs(self):
        names = os.listdir(notes.folder()) if os.path.isdir(notes.folder()) else []
        self.assertEqual([n for n in names if n.endswith(".json")], [])

    # list
    def test_list_is_empty_without_a_hub_file(self):
        self.assertEqual(self.relay("--hub", "--list", "--json"), 0, self.err)
        self.assertEqual(json.loads(self.out), [])

    def test_list_shows_open_and_closed_sessions_in_file_order(self):
        self.open_session("B")
        self.record("A", state="ended")
        self.register(("claude", "A"), ("claude", "B"), ("codex", "C"), ("claude", "gone"))
        self.assertEqual(self.relay("--hub", "--list", "--json"), 0, self.err)
        got = json.loads(self.out)
        self.assertEqual([(x["session_id"], x["open"]) for x in got],
                         [("A", False), ("B", True), ("C", False), ("gone", False)])
        self.assertEqual(sorted(got[0]), ["last_active", "open", "provider", "session_id"])
        self.assertAlmostEqual(got[1]["last_active"], self.now - 5, places=3)
        self.assertEqual((got[2]["last_active"], got[3]["last_active"]), (None, None))
        self.assertEqual(self.out.count("\n"), 1)

    # send
    def test_send_with_session_posts_and_logs(self):
        fake = self.open_session("A")
        self.open_session("B")
        self.register(("claude", "A"), ("claude", "B"))
        self.assertEqual(self.relay("--hub", "--session", "A", "please look at this"), 0, self.err)
        self.assertIn("posted to claude session A", self.out)
        [sent] = fake.wait()
        self.assertEqual(json.loads(sent)["message"]["content"], NOTE_PREFIX + "\nplease look at this")
        self.assertEqual([(n["text"], n["status"]) for n in self.log("claude", "A")], [("please look at this", "posted")])
        self.assertEqual(self.log("claude", "B"), [])

    def test_send_without_session_uses_the_only_open_one(self):
        fake = self.open_session("A")
        self.record("B", state="ended")
        self.register(("claude", "A"), ("claude", "B"))
        self.assertEqual(self.relay("--hub", "hi"), 0, self.err)
        self.assertEqual(len(fake.wait()), 1)

    def test_send_without_session_refuses_zero_or_several_open(self):
        self.register(("claude", "A"))
        self.assertEqual(self.relay("--hub", "hi"), 1)
        self.assertIn("no hub session is open", self.err)
        a, b = self.open_session("A"), self.open_session("B")
        self.register(("claude", "A"), ("claude", "B"))
        self.assertEqual(self.relay("--hub", "hi"), 1)
        self.assertIn("claude session A", self.err)
        self.assertIn("claude session B", self.err)
        self.assertEqual(a.wait(0) + b.wait(0), [])
        self.no_logs()

    def test_an_unregistered_session_is_never_posted_to(self):
        fake = self.open_session("A")
        self.open_session("HUB1")
        self.register(("claude", "HUB1"))
        self.assertEqual(self.relay("--hub", "--session", "A", "hi"), 1)
        self.assertIn("not a registered hub session", self.err)
        self.assertEqual(fake.got, [])
        self.no_logs()

    def test_a_registered_session_that_is_not_open_is_refused(self):
        self.record("A", state="ended")
        self.record("N")
        self.register(("claude", "A"), ("claude", "N"), ("claude", "missing"))
        for sid in ("A", "N", "missing"):
            self.assertEqual(self.relay("--hub", "--session", sid, "hi"), 1, sid)
            self.assertIn("not open", self.err)
        self.no_logs()

    def test_the_same_id_for_two_providers_is_ambiguous(self):
        fake = self.open_session("A")
        self.register(("claude", "A"), ("codex", "A"))
        self.assertEqual(self.relay("--hub", "--session", "A", "hi"), 1)
        self.assertIn("more than one provider", self.err)
        self.assertEqual(fake.got, [])

    def test_a_failed_post_is_a_failure_and_leaves_nothing_queued(self):
        fake = self.open_session("A")
        self.register(("claude", "A"))
        with mock.patch.object(notes, "post", return_value=False):
            self.assertEqual(self.relay("--hub", "--session", "A", "hi"), 1)
        self.assertIn("not delivered", self.err)
        self.assertIn("nothing is left waiting", self.err)
        self.no_logs()
        self.assertEqual(notes.take("claude", "A"), [])
        self.assertEqual(fake.got, [])

    def test_a_concurrent_take_finds_nothing_while_a_send_fails(self):
        self.open_session("A")
        self.register(("claude", "A"))
        taken = []
        stop = threading.Event()

        def taker():
            while not stop.is_set():
                taken.extend(notes.take("claude", "A"))

        t = threading.Thread(target=taker)
        t.start()
        try:
            with mock.patch.object(notes, "post", return_value=False):
                for _ in range(20):
                    self.assertEqual(self.relay("--hub", "--session", "A", "hi"), 1)
        finally:
            stop.set()
            t.join()
        self.assertEqual(taken, [])

    def test_text_that_cannot_be_sent_is_refused(self):
        fake = self.open_session("A")
        self.register(("claude", "A"))
        for bad in ("", "   ", "x" * 2001):
            self.assertEqual(self.relay("--hub", "--session", "A", bad), 1)
        self.assertEqual(fake.got, [])
        self.no_logs()

    def test_a_note_posted_but_not_recorded_exits_zero_with_a_warning(self):
        self.open_session("A")
        self.register(("claude", "A"))
        with mock.patch.object(notes, "send", return_value=({"status": "posted"}, notes.NOT_RECORDED)):
            self.assertEqual(self.relay("--hub", "--session", "A", "hi"), 0)
        self.assertIn(notes.NOT_RECORDED, self.err)

    def test_storage_failures_give_a_plain_message(self):
        self.open_session("A")
        self.register(("claude", "A"))
        with mock.patch.object(notes, "send", side_effect=OSError("disk full")):
            self.assertEqual(self.relay("--hub", "--session", "A", "hi"), 1)
        self.assertIn("could not send the note: disk full", self.err)
        self.assertNotIn("Traceback", self.err)
        with mock.patch.object(sessions, "read_records", side_effect=OSError("denied")):
            self.assertEqual(self.relay("--hub", "--list", "--json"), 1)
        self.assertIn("could not read the hub sessions", self.err)

    # arguments
    def test_argument_rules(self):
        fake = self.open_session("A")
        self.register(("claude", "A"))
        bad = [[], ["--list", "--json"], ["--hub"], ["--hub", "--list"], ["--hub", "--list", "--json", "hi"],
               ["--hub", "--list", "--json", "--session", "A"], ["--hub", "--json", "hi"],
               ["--hub", "--session", "A"], ["--session", "A", "hi"], ["hi"]]
        for argv in bad:
            try:
                rc = self.relay(*argv)
            except SystemExit as e:
                rc = e.code
            self.assertNotEqual(rc, 0, argv)
        self.assertEqual(fake.got, [])
        self.no_logs()


if __name__ == "__main__":
    unittest.main()
