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
