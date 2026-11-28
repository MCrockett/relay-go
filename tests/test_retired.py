import os
import stat
import tempfile
import unittest
from unittest import mock

from relaylib import retired
from tests import helpers

NOW = 1_800_000_000.0
DAY = 86400


def entry(sid="a", state="retired", replaced_by="b", at=NOW, **extra):
    e = {"provider": "claude", "session_id": sid, "label": "proj", "state": state, "replaced_by": replaced_by,
         "replaced_by_name": "proj proj (fresh)", "at": at, "relayed_by": None}
    if state == "retired":
        e["retired_at"] = at
    e.update(extra)
    return e


class RetiredStoreTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temp.cleanup)
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(temp.name, "h")})
        p.start()
        self.addCleanup(p.stop)

    def put(self, *entries):
        with retired.locked():
            retired.save({f"{e['provider']}:{e['session_id']}": e for e in entries})

    def test_missing_file_is_empty(self):
        self.assertEqual(retired.load(), {})
        self.assertFalse(retired.is_retired("claude", "a"))
        self.assertIsNone(retired.entry("claude", "a"))

    def test_unreadable_file_is_empty(self):
        os.makedirs(os.path.dirname(retired.path()), exist_ok=True)
        with open(retired.path(), "w") as f:
            f.write("{not json")
        self.assertEqual(retired.load(), {})
        self.assertFalse(retired.is_retired("claude", "a"))

    def test_save_and_entry_round_trip(self):
        self.put(entry())
        self.assertEqual(retired.entry("claude", "a")["replaced_by"], "b")
        self.assertTrue(retired.is_retired("claude", "a"))
        self.assertEqual(stat.S_IMODE(os.stat(retired.path()).st_mode), 0o600)

    def test_stale_needs_both_quiet_and_ten_minutes(self):
        self.assertFalse(retired.is_stale(entry(at=NOW - 599), NOW))
        self.assertTrue(retired.is_stale(entry(at=NOW - 600), NOW))
        helpers.live_session("claude", "b")
        self.assertFalse(retired.is_stale(entry(at=NOW - 600), NOW))

    def test_clearing_is_not_retired(self):
        self.put(entry(state="clearing"))
        self.assertFalse(retired.is_retired("claude", "a"))

    def test_prune_keeps_live_old_session(self):
        helpers.live_session("claude", "a")
        self.put(entry("a", at=NOW - 8 * DAY), entry("c", at=NOW - 8 * DAY), entry("d", at=NOW - 6 * DAY))
        self.assertEqual(retired.prune(NOW), 1)
        self.assertEqual(set(retired.load()), {"claude:a", "claude:d"})

    def test_prune_clearing(self):
        helpers.live_session("claude", "live-replacement")
        self.put(entry("a", state="clearing", at=NOW - 8 * DAY),
                 entry("c", state="clearing", replaced_by="live-replacement", at=NOW - 8 * DAY),
                 entry("d", state="clearing", at=NOW - 6 * DAY))
        self.assertEqual(retired.prune(NOW), 1)
        self.assertEqual(set(retired.load()), {"claude:c", "claude:d"})


if __name__ == "__main__":
    unittest.main()
