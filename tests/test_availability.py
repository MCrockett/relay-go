import json, os, shutil, tempfile, time, unittest
from unittest import mock
from relaylib import availability
from tests import helpers

LIMITS = {"weekly_stop_pct": 100}


def session_line(used, resets_at, window=10080):
    return json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "limit_id": "codex", "primary": {"used_percent": used, "window_minutes": window, "resets_at": resets_at}}}})


class AvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.codex_home = os.path.join(self.tmp, "codex")
        p = mock.patch.dict(os.environ, {"CODEX_HOME": self.codex_home, "RELAY_HOME": os.path.join(self.tmp, "relay")})
        p.start()
        self.addCleanup(p.stop)

    def log(self, *lines):
        helpers.write(os.path.join(self.codex_home, "sessions", "2026", "09", "25", "rollout-x.jsonl"),
                      "\n".join(lines) + "\n")

    def test_codex_at_its_weekly_limit_is_out_until_reset(self):
        reset = int(time.time()) + 3600
        self.log(session_line(40, reset), session_line(100, reset))
        out, why = availability.blocked("codex", LIMITS)
        self.assertTrue(out)
        self.assertIn("weekly limit", why)

    def test_codex_below_the_limit_or_after_reset_is_available(self):
        self.log(session_line(99, int(time.time()) + 3600))
        self.assertFalse(availability.blocked("codex", LIMITS)[0])
        self.log(session_line(100, int(time.time()) - 60))
        self.assertFalse(availability.blocked("codex", LIMITS)[0])

    def test_no_codex_logs_means_available(self):
        self.assertEqual(availability.blocked("codex", LIMITS), (False, ""))

    def test_source_timestamp_is_preserved(self):
        event = json.loads(session_line(12, 2000000000))
        event["timestamp"] = "2026-09-23T18:00:00Z"
        self.log(json.dumps(event))
        self.assertEqual(availability.codex_weekly(), (12, 2000000000, 1790186400))

    def test_a_usage_limit_failure_is_remembered_until_reset(self):
        self.log(session_line(99, int(time.time()) + 3600))
        self.assertTrue(availability.is_usage_limit("codex reported: {\"message\": \"You've hit your usage limit.\"}"))
        self.assertFalse(availability.is_usage_limit("exit code 1; claude output is not JSON"))
        availability.record_out("codex")
        out, why = availability.blocked("codex", LIMITS)
        self.assertTrue(out)
        self.assertIn("out of usage", why)

    def both_windows(self, five_used, five_reset, week_used, week_reset):
        return json.dumps({"payload": {"rate_limits": {"limit_id": "codex",
            "primary": {"used_percent": five_used, "window_minutes": 300, "resets_at": five_reset},
            "secondary": {"used_percent": week_used, "window_minutes": 10080, "resets_at": week_reset}}}})

    def remembered_until(self):
        with open(os.path.join(self.tmp, "relay", "unavailable.json")) as f:
            return json.load(f)["codex"]

    def test_a_failure_with_weekly_room_waits_only_for_the_5_hour_reset(self):
        now = int(time.time())
        self.log(self.both_windows(100, now + 1800, 51, now + 3 * 86400))
        availability.record_out("codex")
        self.assertEqual(self.remembered_until(), now + 1800)

    def test_a_failure_with_the_weekly_limit_used_up_waits_for_the_weekly_reset(self):
        now = int(time.time())
        self.log(self.both_windows(40, now + 1800, 100, now + 3 * 86400))
        availability.record_out("codex")
        self.assertEqual(self.remembered_until(), now + 3 * 86400)

    def test_a_failure_with_a_past_5_hour_reset_waits_an_hour(self):
        now = int(time.time())
        self.log(self.both_windows(100, now - 60, 51, now + 3 * 86400))
        availability.record_out("codex")
        self.assertAlmostEqual(self.remembered_until(), now + 3600, delta=5)

    def test_a_failure_with_only_a_weekly_window_that_has_room_waits_an_hour(self):
        now = int(time.time())
        self.log(session_line(51, now + 3 * 86400))
        availability.record_out("codex")
        self.assertAlmostEqual(self.remembered_until(), now + 3600, delta=5)

    def test_a_line_without_windows_does_not_hide_an_older_one(self):
        now = int(time.time())
        empty = json.dumps({"payload": {"rate_limits": {"limit_id": "premium", "primary": None, "secondary": None}}})
        self.log(self.both_windows(100, now + 1800, 51, now + 3 * 86400), empty)
        availability.record_out("codex")
        self.assertEqual(self.remembered_until(), now + 1800)

    def test_a_remembered_failure_expires(self):
        availability.record_out("claude", until=int(time.time()) - 1)
        self.assertFalse(availability.blocked("claude", LIMITS)[0])


    def test_a_weekly_limit_reported_as_secondary_counts(self):
        reset = int(time.time()) + 3600
        self.log(json.dumps({"payload": {"rate_limits": {
            "primary": {"used_percent": 10, "window_minutes": 300, "resets_at": reset},
            "secondary": {"used_percent": 100, "window_minutes": 10080, "resets_at": reset}}}}))
        self.assertTrue(availability.blocked("codex", LIMITS)[0])

    def test_a_large_log_is_read_from_its_end(self):
        reset = int(time.time()) + 3600
        junk = json.dumps({"type": "event_msg", "payload": {"text": "x" * 5000}})
        self.log(*([junk] * 800), session_line(100, reset))    # about 4 MB before the limit line
        self.assertTrue(availability.blocked("codex", LIMITS)[0])

if __name__ == "__main__":
    unittest.main()
