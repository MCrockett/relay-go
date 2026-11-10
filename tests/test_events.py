import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from relaylib import events, hub, notes
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


if __name__ == "__main__":
    unittest.main()
