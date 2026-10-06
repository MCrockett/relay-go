import datetime as dt, json, os, tempfile, unittest
from unittest import mock
from zoneinfo import ZoneInfo
from relaylib import reviewtables
from relaylib.errors import RelayError

TZ = "America/Detroit"
at = lambda *a: dt.datetime(*a, tzinfo=ZoneInfo(TZ)).timestamp()
NOW = at(2026, 10, 6, 15, 0)


class ParseUntilTest(unittest.TestCase):
    def test_hh_mm_today_or_tomorrow(self):
        self.assertEqual(reviewtables.parse_until("23:00", TZ, NOW), at(2026, 10, 6, 23, 0))
        self.assertEqual(reviewtables.parse_until("9:30", TZ, NOW), at(2026, 10, 7, 9, 30))
        self.assertEqual(reviewtables.parse_until("15:00", TZ, NOW), at(2026, 10, 7, 15, 0))  # now is not ahead

    def test_iso_with_zone_round_trips_the_prefill(self):
        end = at(2026, 10, 9, 23, 0)
        self.assertEqual(reviewtables.iso(end, TZ), "2026-10-09T23:00-04:00")
        self.assertEqual(reviewtables.parse_until(reviewtables.iso(end, TZ), TZ, NOW), end)
        self.assertEqual(reviewtables.parse_until("2026-10-07T03:00Z", TZ, NOW), at(2026, 10, 6, 23, 0))

    def test_refusals(self):
        for text, why in (("2026-10-06T14:00-04:00", "past"), ("2026-10-14T16:00-04:00", "7 days"),
                          ("2026-10-07T10:00", "time zone"), ("25:00", "time of day"), ("soon", "HH:MM"),
                          ("", "HH:MM")):
            with self.subTest(text=text), self.assertRaisesRegex(RelayError, why):
                reviewtables.parse_until(text, TZ, NOW)

    def test_daylight_saving(self):
        with self.assertRaisesRegex(RelayError, "daylight-saving"):           # 02:30 is skipped
            reviewtables.parse_until("02:30", TZ, at(2027, 3, 14, 1, 0))
        first = reviewtables.parse_until("01:30", TZ, at(2026, 11, 1, 0, 30))  # 01:30 occurs twice
        self.assertEqual(first, at(2026, 11, 1, 0, 30) + 3600)

    def test_uses_the_zone_given(self):
        self.assertEqual(reviewtables.parse_until("23:00", "UTC", NOW),
                         dt.datetime(2026, 10, 6, 23, 0, tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(reviewtables.when(at(2026, 10, 6, 23, 0), TZ), "Tue 23:00")


class TablesTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.cfg = os.path.join(self.home, "config.toml")
        with open(self.cfg, "w") as f:
            f.write('[review.prefer]\nclaude = ["codex:gpt-6-astra"]\ncodex = ["claude:claude-sonnet-5"]\n'
                    'owner = ["codex:gpt-6-astra"]\n')
        p = mock.patch.dict(os.environ, {"RELAY_HOME": self.home, "RELAY_CONFIG": self.cfg})
        p.start()
        self.addCleanup(p.stop)
        self.clock = mock.patch("time.time", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def prefs(self, author="claude", repo=None):
        from relaylib import config
        return [f"{p.provider}:{p.model}" for p in config.review_preferences(config.load(repo), author)]

    def test_timed_table_wins_then_expires_with_nothing_running(self):
        msg = reviewtables.save("claude", "timed", ["claude:claude-fable-5-1", "codex:gpt-6-astra"], "23:00", "owner")
        self.assertIn("until Tue 23:00", msg)
        self.assertIn("then codex:gpt-6-astra", msg)
        self.assertEqual(self.prefs(), ["claude:claude-fable-5-1", "codex:gpt-6-astra"])
        self.assertEqual(oct(os.stat(reviewtables.path()).st_mode & 0o777), "0o600")
        log = os.path.join(self.home, "roles-log.jsonl")
        self.assertEqual(oct(os.stat(log).st_mode & 0o777), "0o600")
        with mock.patch("time.time", return_value=at(2026, 10, 6, 23, 0)):
            self.assertEqual(self.prefs(), ["codex:gpt-6-astra"])            # F6: expired at the boundary

    def test_timed_wins_over_a_repo_overlay(self):
        repo = os.path.join(self.home, "repo")
        os.makedirs(os.path.join(repo, "docs", "relay"))
        with open(os.path.join(repo, "docs", "relay", "config.toml"), "w") as f:
            f.write('[review.prefer]\nclaude = ["claude:claude-sonnet-5"]\n')
        self.assertEqual(self.prefs(repo=repo), ["claude:claude-sonnet-5"])
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        self.assertEqual(self.prefs(repo=repo), ["claude:claude-fable-5-1"])

    def test_permanent_set_leaves_a_timed_table_in_place(self):          # R4, D10
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        msg = reviewtables.save("claude", "permanent", ["claude:claude-sonnet-5"], None, "owner")
        self.assertIn("temporary table still applies until Tue 23:00", msg)
        self.assertEqual(self.prefs(), ["claude:claude-fable-5-1"])
        from relaylib import config
        self.assertEqual(config.load(timed=False)["review"]["prefer"]["claude"], ["claude:claude-sonnet-5"])

    def test_end_one_all_and_nothing(self):                               # R5
        self.assertEqual(reviewtables.end("codex", "owner"), "no temporary table for codex")
        self.assertEqual(reviewtables.end("all", "owner"), "no temporary tables")
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        reviewtables.save("owner", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        self.assertIn("review.claude is back to codex:gpt-6-astra", reviewtables.end("claude", "owner"))
        self.assertEqual(self.prefs(), ["codex:gpt-6-astra"])
        reviewtables.end("all", "owner")
        self.assertEqual(reviewtables.read()[0], {})
        with self.assertRaisesRegex(RelayError, "author"):
            reviewtables.end("nobody", "owner")

    def test_every_change_is_logged_with_who(self):                     # R2a, D9
        reviewtables.save("claude", "permanent", ["claude:claude-sonnet-5"], None, "owner")
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "claude session s1")
        self.assertEqual(reviewtables.latest()["claude"]["by"], "claude session s1")
        reviewtables.end("claude", "dashboard")
        latest = reviewtables.latest()["claude"]
        self.assertEqual((latest["table"], latest["by"]), ("ended", "dashboard"))
        with open(os.path.join(self.home, "roles-log.jsonl"), "a") as f:
            f.write("{not json\n")
        self.assertEqual(reviewtables.latest()["claude"]["by"], "dashboard")  # a bad line is skipped
        os.unlink(os.path.join(self.home, "roles-log.jsonl"))
        self.assertEqual(reviewtables.latest(), {})                             # missing is not fatal

    def test_log_failure_keeps_the_write_with_a_warning(self):
        with mock.patch.object(reviewtables, "_append_log", side_effect=OSError("read-only")):
            msg = reviewtables.save("claude", "permanent", ["claude:claude-sonnet-5"], None, "owner")
        self.assertIn("warning", msg)
        self.assertEqual(self.prefs(), ["claude:claude-sonnet-5"])

    def test_malformed_file_and_bad_author_are_ignored_and_reported(self):   # R3, F3
        with open(reviewtables.path(), "w") as f:
            f.write("{oops")
        self.assertEqual(self.prefs(), ["codex:gpt-6-astra"])
        self.assertIn("ignored", reviewtables.read()[1][0])
        good = {"entries": ["claude:claude-fable-5-1"], "until": NOW + 3600, "set_at": NOW, "by": "owner"}
        with open(reviewtables.path(), "w") as f:
            json.dump({"claude": {**good, "entries": ["gemini:pro"]}, "owner": good}, f)
        tables, problems = reviewtables.read()
        self.assertEqual(list(tables), ["owner"])
        self.assertIn("claude", problems[0])
        self.assertEqual(self.prefs(), ["codex:gpt-6-astra"])
        self.assertEqual(self.prefs("owner"), ["claude:claude-fable-5-1"])
        reviewtables.save("codex", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")  # next write replaces it
        with open(reviewtables.path()) as f:
            self.assertEqual(sorted(json.load(f)), ["codex", "owner"])

    def test_stale_seen_writes_nothing(self):                            # D8, F1
        seen = reviewtables.revision()
        with open(self.cfg, "a") as f:
            f.write("# edited by hand\n")
        with self.assertRaises(reviewtables.Stale):
            reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "dashboard", seen)
        with self.assertRaises(reviewtables.Stale):
            reviewtables.end("all", "dashboard", seen)
        self.assertFalse(os.path.exists(reviewtables.path()))
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "dashboard",
                          reviewtables.revision())

    def test_a_busy_lock_writes_nothing(self):                           # F4
        import fcntl
        from relaylib import config
        with mock.patch.object(config, "LOCK_TIMEOUT_S", 0.05), \
                open(os.path.join(self.home, "roles.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(RelayError, "busy, try again"):
                reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "dashboard")
        self.assertFalse(os.path.exists(reviewtables.path()))

    def test_revision_with_missing_files(self):
        os.unlink(self.cfg)
        self.assertEqual(reviewtables.revision(), reviewtables.revision())   # missing counts as empty

    def test_table_and_until_must_agree(self):                           # R11
        with self.assertRaisesRegex(RelayError, "needs an end time"):
            reviewtables.save("claude", "timed", ["codex:a"], None, "owner")
        with self.assertRaisesRegex(RelayError, "no end time"):
            reviewtables.save("claude", "permanent", ["codex:a"], "23:00", "owner")
        with self.assertRaisesRegex(RelayError, "author"):
            reviewtables.save("nobody", "permanent", ["codex:a"], None, "owner")
        with self.assertRaisesRegex(RelayError, "list"):
            reviewtables.save("claude", "permanent", "codex:a", None, "owner")

    def test_a_loaded_config_is_not_changed_by_a_later_save(self):       # F5
        from relaylib import config
        cfg = config.load()
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        self.assertEqual(cfg["review"]["prefer"]["claude"], ["codex:gpt-6-astra"])
