import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from relaylib import events, hub, notes, sessions
from tests import helpers
from tests.test_notes import FakeInbox


class EventsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")  # short: macOS caps socket paths near 104 bytes
        self.addCleanup(temp.cleanup)
        self.tmp = temp.name
        patch = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "h")})
        patch.start()
        self.addCleanup(patch.stop)

    def hubs(self, *pairs):
        os.makedirs(hub.folder(), exist_ok=True)
        hub._write_json(hub._sessions_path(), [{"provider": p, "session_id": s, "at": 1} for p, s in pairs])

    def log_lines(self):
        try:
            with open(os.path.join(hub.folder(), "log.jsonl")) as f:
                return [json.loads(line) for line in f]
        except FileNotFoundError:
            return []

    def notes_of(self, provider, sid):
        return notes._load(notes.log_path(provider, sid))

    def test_nothing_is_delivered_before_flush(self):
        self.hubs(("claude", "h1"))
        with events.collect() as batch:
            events.queue("go", "relay-go", "x", "build", 3, "relay: go")
            self.assertEqual(self.log_lines(), [])
            self.assertEqual(self.notes_of("claude", "h1"), [])
            self.assertEqual(batch.flush(), [])
        self.assertEqual(len(self.log_lines()), 1)
        self.assertEqual(len(self.notes_of("claude", "h1")), 1)

    def test_log_line_fields(self):
        events.queue("merged", "relay-go", "x", "build", 7, "relay: merged")
        (line,) = self.log_lines()
        self.assertEqual({k: line[k] for k in ("command", "event", "repo", "slug", "pr", "text")},
                         {"command": "event", "event": "merged", "repo": "relay-go", "slug": "x", "pr": 7,
                          "text": "relay: merged"})
        self.assertIn("at", line)

    def test_note_to_each_hub_but_the_cause_with_event_prefix(self):
        self.hubs(("claude", "h1"), ("codex", "h2"))
        events.queue("go", "r", "x", "build", 1, "relay: go", cause=("claude", "h1"))
        self.assertEqual(self.notes_of("claude", "h1"), [])
        (note,) = self.notes_of("codex", "h2")
        self.assertEqual((note["status"], note["prefix"], note["text"]), ("queued", "relay event:", "relay: go"))

    def test_posted_to_a_running_session_inbox(self):
        fake = FakeInbox(self.tmp)
        self.addCleanup(fake.close)
        self.hubs(("claude", "h1"))
        with mock.patch.object(events, "_inbox", return_value=fake.path):
            events.queue("go", "r", "x", "build", 1, "relay: go")
        (got,) = fake.wait()
        self.assertIn("relay event:\\nrelay: go", got)
        self.assertEqual(self.notes_of("claude", "h1")[0]["status"], "posted")

    def test_inbox_none_without_a_record(self):
        self.assertIsNone(events._inbox("claude", "nobody"))

    def test_failures_are_warnings(self):
        self.hubs(("claude", "h1"))
        with mock.patch.object(hub, "log", return_value="could not write the hub log: x"), \
                mock.patch.object(notes, "send", side_effect=OSError("disk")):
            warnings = events.queue("go", "r", "x", "build", 1, "relay: go")
        self.assertEqual(len(warnings), 2)
        self.assertIn("could not write the hub log", warnings[0])
        self.assertIn("disk", warnings[1])

    def test_one_operation_flush_leaves_anothers_events(self):
        ready, done = threading.Event(), threading.Event()
        held = {}

        def other():
            with events.collect() as batch:
                events.queue("go", "r", "other", "build", 2, "relay: other")
                held["batch"] = batch
                ready.set()
                done.wait(5)

        t = threading.Thread(target=other)
        t.start()
        ready.wait(5)
        with events.collect() as mine:
            events.queue("go", "r", "mine", "build", 1, "relay: mine")
            mine.flush()
        self.assertEqual([l["slug"] for l in self.log_lines()], ["mine"])
        self.assertEqual(len(held["batch"].events), 1)
        done.set()
        t.join()

    def test_wording(self):
        self.assertEqual(events.waiting_text("relay-go", "m", "spec", None, "3 rounds"),
                         "relay: relay-go m spec review stopped: 3 rounds; waiting on the owner.")
        self.assertIn("PR #4 (m)", events.go_text("relay-go", "m", 4, "codex"))
        self.assertIn("fallback", events.go_text("relay-go", "m", 4, "claude, fallback"))
        self.assertIn("released to main", events.merged_text("r", "m", 4, "main"))
        self.assertIn("review failed", events.waiting_text("r", "m", "build", 4, "boom", error=True))

    # delegated-merge R12, R14: the owner session, fields, log-only lines
    OWNER = ("claude", "f1")

    def test_owner_session_gets_the_merged_event_note(self):
        events.queue("merged", "r", "x", "build", 1, "relay: merged", owner=self.OWNER)
        (note,) = self.notes_of(*self.OWNER)
        self.assertEqual((note["prefix"], note["text"]), ("relay event:", "relay: merged"))

    def test_owner_is_not_told_when_it_is_the_cause(self):
        events.queue("merged", "r", "x", "build", 1, "relay: merged", cause=self.OWNER, owner=self.OWNER)
        self.assertEqual(self.notes_of(*self.OWNER), [])

    def test_owner_that_is_a_hub_session_gets_one_note(self):
        self.hubs(self.OWNER)
        events.queue("merged", "r", "x", "build", 1, "relay: merged", owner=self.OWNER)
        self.assertEqual(len(self.notes_of(*self.OWNER)), 1)

    def test_owner_note_only_for_merged_and_released(self):
        events.queue("go", "r", "x", "build", 1, "relay: go", owner=self.OWNER)
        self.assertEqual(self.notes_of(*self.OWNER), [])
        events.queue("released", "r", "x", "build", 1, "relay: released", owner=self.OWNER)
        self.assertEqual(len(self.notes_of(*self.OWNER)), 1)

    def test_note_extra_goes_to_the_owner_only(self):
        self.hubs(("codex", "h2"))
        events.queue("merged", "r", "x", "build", 1, "relay: merged", owner=self.OWNER,
                     note_extra="Remove the worktree: x")
        self.assertEqual(self.notes_of(*self.OWNER)[0]["text"], "relay: merged Remove the worktree: x")
        self.assertEqual(self.notes_of("codex", "h2")[0]["text"], "relay: merged")

    def test_fields_land_in_the_hub_log_line(self):
        events.queue("merged", "r", "x", "build", 1, "relay: merged", fields={"delegated": True, "outcome": "merged"})
        (line,) = self.log_lines()
        self.assertEqual((line["delegated"], line["outcome"]), (True, "merged"))

    def test_log_only_writes_the_log_line_and_no_notes(self):
        self.hubs(("codex", "h2"))
        events.queue("merged", "r", "x", "build", 1, "relay: merged", owner=self.OWNER, log_only=True)
        self.assertEqual(len(self.log_lines()), 1)
        self.assertEqual((self.notes_of(*self.OWNER), self.notes_of("codex", "h2")), ([], []))

    def test_owner_only_skips_the_hub_sessions_but_tells_the_owner(self):
        self.hubs(("codex", "h2"))
        events.queue("merged", "r", "x", "build", 1, "relay: merged", owner=self.OWNER, owner_only=True)
        self.assertEqual(len(self.log_lines()), 1)
        self.assertEqual(len(self.notes_of(*self.OWNER)), 1)
        self.assertEqual(self.notes_of("codex", "h2"), [])

    def test_owner_of_needs_a_provider_and_a_session(self):
        self.assertIsNone(events.owner_of({}))
        self.assertIsNone(events.owner_of({"owner": {}}))
        self.assertIsNone(events.owner_of({"owner": {"provider": "gemini", "session": "s"}}))
        self.assertIsNone(events.owner_of({"owner": {"provider": "claude", "session": ""}}))
        self.assertEqual(events.owner_of({"owner": {"provider": "codex", "session": "s9"}}), ("codex", "s9"))

    def test_session_live_is_false_for_ended_and_stopped_and_true_for_working(self):
        self.assertIsNone(events.session_live("claude", "none"))
        helpers.live_session("claude", "w1", state="working")
        helpers.live_session("claude", "e1", state="ended")
        self.assertEqual(events.session_live("claude", "w1")["session_id"], "w1")
        self.assertIsNone(events.session_live("claude", "e1"))
        helpers.live_session("claude", "d1", state="working")  # a process that no longer runs shows as stopped
        path = sessions.record_path("claude", "d1")
        with open(path) as f:
            record = json.load(f)
        record["process"] = {"pid": 2 ** 22 + 12345, "started": "Mon Jan  1 00:00:00 2001"}
        with open(path, "w") as f:
            json.dump(record, f)
        self.assertIsNone(events.session_live("claude", "d1"))

    def test_delegated_texts(self):
        self.assertEqual(events.merged_text("r", "m", 4, "develop", delegated=True),
                         "relay: r PR #4 (m) is merged into develop, delegated under D1.")
        self.assertEqual(events.merged_text("r", "m", 4, "main", delegated=True),
                         "relay: r PR #4 (m) is released to main, delegated under D1.")
        self.assertEqual(events.delegated_text("r", "m", 4, "merged into develop; not marked yet"),
                         "relay: r PR #4 (m): merged into develop; not marked yet; delegated under D1.")
        self.assertEqual(events.delegated_text("r", "m", 4, "merged into develop", left=["a", "b"]),
                         "relay: r PR #4 (m): merged into develop; delegated under D1. Not tidied: a; b.")


if __name__ == "__main__":
    unittest.main()
