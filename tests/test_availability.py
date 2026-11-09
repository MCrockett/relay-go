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
        self.assertIn("relay tries again", why)  # no 5-hour or weekly cause in the log: limit unknown

    def both_windows(self, five_used, five_reset, week_used, week_reset):
        return json.dumps({"payload": {"rate_limits": {"limit_id": "codex",
            "primary": {"used_percent": five_used, "window_minutes": 300, "resets_at": five_reset},
            "secondary": {"used_percent": week_used, "window_minutes": 10080, "resets_at": week_reset}}}})

    def remembered_until(self):
        with open(os.path.join(self.tmp, "relay", "unavailable.json")) as f:
            return json.load(f)["codex"]["until"]

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


def five_line(used, resets_at, timestamp="now"):
    event = {"payload": {"rate_limits": {"primary": {"used_percent": used, "window_minutes": 300,
                                                         "resets_at": resets_at}}}}
    if timestamp == "now":
        event["timestamp"] = time.time()
    elif timestamp is not None:
        event["timestamp"] = timestamp
    return json.dumps(event)


class FiveHourHome(unittest.TestCase):  # five-hour-usage
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.codex_home = os.path.join(self.tmp, "codex")
        self.relay_home = os.path.join(self.tmp, "relay")
        p = mock.patch.dict(os.environ, {"CODEX_HOME": self.codex_home, "RELAY_HOME": self.relay_home})
        p.start()
        self.addCleanup(p.stop)
        self.now = time.time()

    def log(self, *lines):
        helpers.write(os.path.join(self.codex_home, "sessions", "2026", "11", "08", "rollout-x.jsonl"),
                      "\n".join(lines) + "\n")

    def claude(self, five=None, week=None, sampled=None, raw=None):
        path = os.path.join(self.relay_home, "claude-usage.json")
        if raw is not None:
            helpers.write(path, raw)
            return
        data = {"sampled_at": self.now if sampled is None else sampled}
        if five is not None:
            data["five_hour"] = {"used_pct": five[0], "resets_at": five[1]}
        if week is not None:
            data["seven_day"] = {"used_pct": week[0], "resets_at": week[1]}
        helpers.write(path, json.dumps(data))

    def stored(self, provider):
        with open(os.path.join(self.relay_home, "unavailable.json")) as f:
            return json.load(f)[provider]


class FiveHourTest(FiveHourHome):  # R1, R2
    def test_codex_current(self):
        reset = int(self.now) + 3600
        self.log(five_line(40, reset))
        w = availability.five_hour("codex", self.now)
        self.assertEqual((w["used_pct"], w["resets_at"], w["state"]), (40, reset, "current"))
        self.assertIsNotNone(w["sampled_at"])

    def test_codex_stale_edge(self):
        reset = int(self.now) + 7200
        self.log(five_line(40, reset, timestamp=self.now - 3600))
        self.assertEqual(availability.five_hour("codex", self.now)["state"], "current")
        self.log(five_line(40, reset, timestamp=self.now - 3660))
        self.assertEqual(availability.five_hour("codex", self.now)["state"], "stale")

    def test_codex_past_reset(self):
        self.log(five_line(100, int(self.now) - 60))
        w = availability.five_hour("codex", self.now)
        self.assertEqual((w["state"], w["used_pct"]), ("reset", 0))

    def test_codex_without_a_time_is_stale(self):
        self.log(five_line(40, int(self.now) + 3600, timestamp=None))
        w = availability.five_hour("codex", self.now)
        self.assertEqual((w["state"], w["sampled_at"]), ("stale", None))

    def test_codex_reset_zero_is_unknown_reset_never_reset(self):
        self.log(five_line(40, 0))
        w = availability.five_hour("codex", self.now)
        self.assertEqual((w["state"], w["resets_at"], w["used_pct"]), ("current", None, 40))

    def test_no_codex_log_is_unknown(self):
        self.assertEqual(availability.five_hour("codex", self.now),
                         {"used_pct": None, "resets_at": None, "sampled_at": None, "state": "unknown"})

    def test_claude_states(self):
        self.claude(five=(12, self.now + 3600))
        self.assertEqual(availability.five_hour("claude", self.now)["state"], "current")
        self.assertEqual(availability.five_hour("claude", self.now)["used_pct"], 12)
        self.claude(five=(12, self.now + 3600), sampled=self.now - 3660)
        self.assertEqual(availability.five_hour("claude", self.now)["state"], "stale")
        self.claude(five=(100, self.now - 10), sampled=self.now - 3660)
        w = availability.five_hour("claude", self.now)
        self.assertEqual((w["state"], w["used_pct"]), ("reset", 0))

    def test_claude_bad_files_are_unknown(self):
        for raw in ("not json", json.dumps({"sampled_at": self.now}), json.dumps([1]),
                    json.dumps({"sampled_at": self.now, "five_hour": {"used_pct": 101, "resets_at": self.now + 9}}),
                    json.dumps({"sampled_at": self.now, "five_hour": {"used_pct": 5, "resets_at": 0}}),
                    json.dumps({"sampled_at": self.now, "five_hour": {"used_pct": True, "resets_at": self.now + 9}})):
            self.claude(raw=raw)
            self.assertEqual(availability.five_hour("claude", self.now)["state"], "unknown", raw)
        os.remove(os.path.join(self.relay_home, "claude-usage.json"))
        self.assertEqual(availability.five_hour("claude", self.now)["state"], "unknown")
        os.makedirs(os.path.join(self.relay_home, "claude-usage.json"))  # unreadable
        self.assertEqual(availability.five_hour("claude", self.now)["state"], "unknown")

    def test_reading_writes_no_history(self):
        self.log(five_line(40, int(self.now) + 3600))
        self.claude(five=(12, self.now + 3600))
        availability.five_hour("codex", self.now)
        availability.five_hour("claude", self.now)
        self.assertFalse(os.path.exists(os.path.join(self.relay_home, "usage.jsonl")))


class RecordTest(FiveHourHome):  # R3
    def both(self, five, week):
        self.log(json.dumps({"timestamp": self.now, "payload": {"rate_limits": {
            "primary": {"used_percent": five[0], "window_minutes": 300, "resets_at": five[1]},
            "secondary": {"used_percent": week[0], "window_minutes": 10080, "resets_at": week[1]}}}}))

    def test_codex_five_hour_cause(self):
        now = int(self.now)
        self.both((100, now + 1800), (51, now + 86400))
        availability.record_out("codex")
        self.assertEqual(self.stored("codex"), {"until": now + 1800, "limit": "5-hour", "resets_at": now + 1800})

    def test_codex_weekly_cause(self):
        now = int(self.now)
        self.both((40, now + 1800), (100, now + 86400))
        availability.record_out("codex")
        self.assertEqual(self.stored("codex"), {"until": now + 86400, "limit": "weekly", "resets_at": now + 86400})

    def test_codex_unknown_cause(self):
        availability.record_out("codex")
        entry = self.stored("codex")
        self.assertEqual((entry["limit"], entry["resets_at"]), ("unknown", None))
        self.assertAlmostEqual(entry["until"], self.now + 3600, delta=5)

    def test_claude_names_the_limit_but_waits_an_hour(self):
        self.claude(five=(100, self.now + 4 * 3600), week=(30, self.now + 86400))
        availability.record_out("claude")
        entry = self.stored("claude")
        self.assertEqual((entry["limit"], entry["resets_at"]), ("5-hour", self.now + 4 * 3600))
        self.assertAlmostEqual(entry["until"], self.now + 3600, delta=5)
        self.claude(five=(20, self.now + 4 * 3600), week=(100, self.now + 86400))
        availability.record_out("claude")
        self.assertEqual(self.stored("claude")["limit"], "weekly")

    def test_claude_without_a_snapshot_is_unknown(self):
        availability.record_out("claude")
        self.assertEqual(self.stored("claude")["limit"], "unknown")

    def test_old_and_new_entries_block_and_bad_ones_are_ignored(self):
        later = int(self.now) + 600
        helpers.write(os.path.join(self.relay_home, "unavailable.json"), json.dumps({"codex": later}))
        self.assertTrue(availability.blocked("codex", LIMITS)[0])
        helpers.write(os.path.join(self.relay_home, "unavailable.json"),
                      json.dumps({"codex": {"until": later, "limit": "5-hour", "resets_at": later}}))
        self.assertTrue(availability.blocked("codex", LIMITS)[0])
        for bad in ("soon", None, {"limit": "5-hour"}, {"until": "x"}, [later]):
            helpers.write(os.path.join(self.relay_home, "unavailable.json"), json.dumps({"codex": bad}))
            self.assertEqual(availability.blocked("codex", LIMITS), (False, ""), bad)

    def test_recording_one_provider_keeps_the_other(self):
        availability.record_out("claude")
        availability.record_out("codex")
        self.assertEqual(self.stored("claude")["limit"], "unknown")


class ReasonTest(FiveHourHome):  # R4
    def entry(self, provider, until, limit, resets_at):
        helpers.write(os.path.join(self.relay_home, "unavailable.json"),
                      json.dumps({provider: {"until": until, "limit": limit, "resets_at": resets_at}}))

    def test_reason_forms(self):
        later = int(self.now) + 1800
        self.entry("codex", later, "5-hour", later)
        why = availability.blocked("codex", LIMITS)[1]
        self.assertTrue(why.startswith("5-hour limit used up until "), why)
        self.entry("codex", later, "weekly", later)
        self.assertTrue(availability.blocked("codex", LIMITS)[1].startswith("weekly limit used up until "))
        self.entry("claude", later, "5-hour", later + 3 * 3600)
        why = availability.blocked("claude", LIMITS)[1]
        self.assertIn("5-hour limit used up, resets ", why)
        self.assertIn("; relay tries again ", why)
        self.entry("claude", later, "unknown", None)
        self.assertTrue(availability.blocked("claude", LIMITS)[1].startswith(
            "a review failed on a usage limit; relay tries again "))

    def test_order_recorded_then_weekly_then_five_hour(self):
        now = int(self.now)
        self.log(json.dumps({"timestamp": self.now, "payload": {"rate_limits": {
            "primary": {"used_percent": 100, "window_minutes": 300, "resets_at": now + 1800},
            "secondary": {"used_percent": 100, "window_minutes": 10080, "resets_at": now + 86400}}}}))
        self.assertTrue(availability.blocked("codex", LIMITS)[1].startswith("weekly limit 100% used"))
        self.entry("codex", now + 600, "unknown", None)
        self.assertTrue(availability.blocked("codex", LIMITS)[1].startswith("a review failed"))

    def test_live_five_hour_blocks_both_providers(self):
        reset = int(self.now) + 1800
        self.log(five_line(100, reset))
        self.claude(five=(100, reset))
        for provider in ("codex", "claude"):
            out, why = availability.blocked(provider, LIMITS)
            self.assertTrue(out, provider)
            self.assertTrue(why.startswith("5-hour limit used up until "), why)

    def test_live_five_hour_does_not_block(self):
        self.log(five_line(99, int(self.now) + 1800))
        self.assertFalse(availability.blocked("codex", LIMITS)[0])
        self.log(five_line(100, 0))
        self.assertFalse(availability.blocked("codex", LIMITS)[0])
        self.log(five_line(100, int(self.now) - 5))
        self.assertFalse(availability.blocked("codex", LIMITS)[0])

    def test_a_stale_full_window_still_blocks(self):  # F5
        self.log(five_line(100, int(self.now) + 1800, timestamp=self.now - 4 * 3600))
        self.assertTrue(availability.blocked("codex", LIMITS)[0])


if __name__ == "__main__":
    unittest.main()
