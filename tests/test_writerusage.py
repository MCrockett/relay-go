import unittest
from unittest import mock
from relaylib import holds, transcripts, writerusage


def W(slug, start, end, stages, branch="feat/", session="S1", provider="claude"):
    return {"repo": "r", "url": "u", "slug": slug, "branch": branch + slug, "provider": provider,
            "session": session, "start": start, "end": end, "stages": stages, "last_commit": start}


def turn(at, branch=None, model="claude-opus-5-5"):
    return {"at": at, "model": model, "input": 10, "cached": 4, "output": 2, "branch": branch}


class AttributeTest(unittest.TestCase):
    def test_one_turn_one_feature(self):
        ws = [W("a", 0, None, [[0, "spec"], [50, "plan"]]), W("b", 10, None, [[10, "build"]])]
        got = writerusage.attribute(ws, {("claude", "S1"): [turn(5), turn(60, "feat/b"), turn(60), turn(70, "feat/x")]})
        self.assertEqual([(g["slug"], g["stage"]) for g in got],
                         [("a", "spec"), ("b", "build"), ("a", "plan"), ("a", "plan")])
        # turn(60) without a branch: a's latest state commit (50) is newer than b's (10)

    def test_equal_commit_times_break_ties_by_repo_and_slug(self):
        ws = [W("b", 0, None, [[0, "spec"]]), W("a", 0, None, [[0, "build"]])]
        (got,) = writerusage.attribute(ws, {("claude", "S1"): [turn(5)]})
        self.assertEqual(got["slug"], "a")

    def test_turn_before_the_first_state_commit_takes_the_first_stage(self):
        (got,) = writerusage.attribute([W("a", 0, None, [[30, "plan"], [60, "build"]])], {("claude", "S1"): [turn(5)]})
        self.assertEqual(got["stage"], "plan")

    def test_outside_windows_is_unattributed_and_minutes_are_capped(self):
        ws = [W("a", 0, 100, [[0, "spec"]])]
        got = writerusage.attribute(ws, {("claude", "S1"): [turn(10), turn(70), turn(1000), turn(1010)]})
        self.assertEqual([g["slug"] for g in got], ["a", "a", None, None])
        self.assertEqual([g["minutes"] for g in got], [0, 1.0, 5.0, 10 / 60])     # 60 s, capped 300 s, 10 s

    def test_other_sessions_windows_never_claim_a_turn(self):
        (got,) = writerusage.attribute([W("a", 0, None, [[0, "spec"]], session="S2")], {("claude", "S1"): [turn(5)]})
        self.assertIsNone(got["slug"])


class TotalsTest(unittest.TestCase):
    def test_totals_by_feature_and_model_within_the_period(self):
        attributed = [dict(turn(t), provider="claude", session=s, repo="r", slug="a", stage="spec", minutes=1)
                      for t, s in ((100, "S1"), (200, "S2"), (-10 * 86400, "S1"))]
        got = writerusage.totals(attributed, 7 * 86400, now=300)
        (row,) = got["features"]
        self.assertEqual((row["name"], row["sessions"], row["turns"], row["input"], row["cached_share"], row["minutes"]),
                         ("r · a · spec (claude)", 2, 2, 20, 40.0, 2))
        self.assertEqual(got["models"][0]["name"], "claude:claude-opus-5-5")
        self.assertEqual(got["unattributed"], {})

    def test_two_models_on_one_feature_and_stage_split_in_feature_models(self):
        attributed = [dict(turn(100, model=m), provider="claude", session="S1", repo="r", slug="a", stage="build",
                           minutes=1) for m in ("claude-opus-5-5", "claude-fable-5-1")]
        got = writerusage.totals(attributed, 86400, now=300)
        self.assertEqual(len(got["features"]), 1)
        self.assertEqual([r["name"] for r in got["feature_models"]],
                         ["r · a · build (claude) · claude:claude-fable-5-1",
                          "r · a · build (claude) · claude:claude-opus-5-5"])
        self.assertEqual(got["feature_models"][0]["feature"], "r · a · build (claude)")
        self.assertEqual(got["feature_models"][0]["model"], "claude:claude-fable-5-1")

    def test_unattributed_by_provider(self):
        attributed = [dict(turn(100), provider="codex", session="C1", repo=None, slug=None, stage=None, minutes=2)]
        got = writerusage.totals(attributed, 86400, now=300)
        self.assertEqual(got["unattributed"], {"codex": {"turns": 1, "input": 10, "cached": 4, "output": 2,
                                                         "minutes": 2}})
        self.assertEqual(got["features"], [])


class SummaryTest(unittest.TestCase):
    def test_summary_reports_unreadable_and_skips_owner_sessions(self):
        windows = [W("a", 0, None, [[0, "spec"]]), W("b", 0, None, [[0, "spec"]], session="owner@host", provider="owner")]
        with mock.patch.object(holds, "windows", return_value={"windows": windows, "flags": ["merge time unknown for r b"]}), \
                mock.patch.object(transcripts, "read_sessions", return_value={
                    "turns": {}, "unreadable": {("claude", "S1"): "log not found"},
                    "notes": ["usage cache not saved"]}) as read:
            got = writerusage.summary(["/repo"], now=1000)
        self.assertEqual(got["unreadable"], [{"provider": "claude", "session": "S1", "reason": "log not found"}])
        read.assert_called_once_with({("claude", "S1")})                  # owner sessions are never looked up
        self.assertEqual(got["notes"], ["merge time unknown for r b", "usage cache not saved"])
        self.assertEqual(set(got), {"7", "30", "unreadable", "notes"})

    def test_summary_takes_periods_in_days(self):
        with mock.patch.object(holds, "windows", return_value={"windows": [W("a", 0, None, [[0, "spec"]])], "flags": []}), \
                mock.patch.object(transcripts, "read_sessions", return_value={
                    "turns": {("claude", "S1"): [turn(900), turn(50)]}, "unreadable": {}, "notes": []}):
            got = writerusage.summary(["/repo"], now=1000, periods=(0.002,))   # about 173 seconds
        self.assertEqual(got["0.002"]["features"][0]["turns"], 1)


if __name__ == "__main__":
    unittest.main()
