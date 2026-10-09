import datetime
import os
import tempfile
import unittest

from relaylib import gitops, state, waiting
from tests import helpers

UPDATED = "2026-10-07T10:00:00-04:00"
AT = datetime.datetime.fromisoformat(UPDATED).timestamp()


def row(status="drafting", flags=(), stage="build", feature="f", pr=None):
    return {"repo": "r", "feature": feature, "stage": stage, "status": status, "flags": list(flags), "pr": pr}


def st(status="drafting", stage="build", **extra):
    return dict({"stage": stage, "status": status, "updated": UPDATED, "owner": {"provider": "claude"}}, **extra)


def rec(state_="waiting", since=500.0, pending=(), provider="claude"):
    return {"provider": provider, "session_id": "S", "state": state_, "since": since, "at": since,
            "pending": [{"tool_name": t} for t in pending]}


def texts(asks):
    return [a["text"] for a in asks]


class AsksTest(unittest.TestCase):
    def test_error_row(self):
        asks = waiting.asks(row(status="?", feature="?", stage="?", flags=["not a git repository"]), None, None, None, None)
        self.assertEqual(asks, [{"kind": "error", "text": "Fix: not a git repository", "since": None}])

    def test_decide_with_and_without_the_stuck_reason(self):
        r, s = row("waiting-owner"), st("waiting-owner")
        self.assertEqual(texts(waiting.asks(r, s, None, None, "no progress on R1-1")),
                         ["Decide: build review stopped. no progress on R1-1"])
        self.assertEqual(waiting.asks(r, s, None, None, None),
                         [{"kind": "decide", "text": "Decide: build review stopped", "since": AT}])

    def test_review_failed_with_and_without_its_reason(self):
        r = row("review-error")
        self.assertEqual(texts(waiting.asks(r, st("review-error", review_error="codex timed out twice"), None, None, None)),
                         ["Review failed: codex timed out twice"])
        self.assertEqual(texts(waiting.asks(r, st("review-error"), None, None, None)), ["Review failed"])

    def test_merge_fallback_and_stale(self):
        s = st("ready-to-merge")
        self.assertEqual(texts(waiting.asks(row("ready-to-merge", pr=6), s, None, None, None)), ["Merge PR #6"])
        call = "fallback GO: confirm with codex before merging (codex out of usage; your call)"
        self.assertEqual(texts(waiting.asks(row("ready-to-merge", [call], pr=6), s, None, None, None)),
                         ["Merge PR #6 · " + call])
        for stale in ("stale build GO: code changed since the GO",
                      "fallback GO: confirm with codex before merging (agent: relay review)"):
            self.assertEqual(waiting.asks(row("ready-to-merge", [stale], pr=6), s, None, None, None), [])

    def test_take_a_handoff(self):
        asks = waiting.asks(row(flags=["handoff (over 24h old): open a session and run `relay take`"]), st(), None, None, None)
        self.assertEqual(asks, [{"kind": "take", "text": "Take the handoff: open a session and run relay take", "since": AT}])
        self.assertEqual(waiting.asks(row(flags=["handoff drafted, not pushed yet"]), st(), None, None, None), [])

    def test_session_asks(self):
        waiting_on = {"kind": "attention", "text": "waiting on you 16h", "since": 500.0}
        self.assertEqual(waiting.asks(row(), st(), waiting_on, rec(), None),
                         [{"kind": "answer", "text": "Answer the claude session", "since": 500.0, "session": "claude:S"}])
        perm = {"kind": "attention", "text": "needs permission 3m", "since": 700.0}
        self.assertEqual(texts(waiting.asks(row(), st(), perm, rec("permission", 700.0, ("Bash", "Edit", "Bash"),
                                                                    provider="codex"), None)),
                         ["Approve Bash, Edit in the codex session"])
        quiet = {"kind": "attention", "text": "no activity 23h", "since": 300.0}  # F2: activity alone, no record
        self.assertEqual(waiting.asks(row(), st(), quiet, None, None),
                         [{"kind": "check", "text": "Check the claude session: no activity 23h", "since": 300.0}])
        self.assertEqual(waiting.asks(row(), st(), {"kind": "ok", "text": "active 1m ago", "since": 1.0}, rec(), None), [])
        self.assertEqual(waiting.asks(row(), st(), {"kind": "quiet", "text": "session ended 1h ago", "since": 1.0},
                                      rec("ended"), None), [])

    def test_order_when_several_apply(self):
        waiting_on = {"kind": "attention", "text": "waiting on you 2h", "since": 500.0}
        asks = waiting.asks(row("ready-to-merge", pr=6), st("ready-to-merge"), waiting_on, rec(), None)
        self.assertEqual([a["kind"] for a in asks], ["merge", "answer"])
        perm = {"kind": "attention", "text": "needs permission 3m", "since": 700.0}
        asks = waiting.asks(row("waiting-owner"), st("waiting-owner"), perm, rec("permission", 700.0, ("Bash",)), None)
        self.assertEqual([a["kind"] for a in asks], ["decide", "approve"])

    def test_done_has_no_asks(self):
        self.assertEqual(waiting.asks(row("done", stage="done", pr=6), st("ready-to-merge"), None, None, None), [])

    def test_one_session_waits_once_across_its_features(self):
        def waiting_row(feature, updated, *asks):
            return dict(row(feature=feature), updated=updated, asks=list(asks), wait_since=waiting.since(asks),
                        waiting_on_owner=bool(asks), health_inbox=True)
        answer = lambda: {"kind": "answer", "text": "Answer the claude session", "since": 500.0, "session": "claude:S"}
        older = waiting_row("older", "2026-10-07T09:00:00-04:00", answer())
        newer = waiting_row("newer", "2026-10-07T11:00:00-04:00", answer())
        merge = waiting_row("merge", "2026-10-07T08:00:00-04:00",
                            {"kind": "merge", "text": "Merge PR #6", "since": 100.0}, answer())
        other = waiting_row("other", "2026-10-07T07:00:00-04:00",
                            dict(answer(), session="codex:T", text="Answer the codex session"))
        waiting.one_ask_per_session([older, newer, merge, other])
        self.assertEqual(texts(newer["asks"]), ["Answer the claude session · also for merge, older"])
        self.assertEqual((older["asks"], older["waiting_on_owner"], older["wait_since"], older["health_inbox"]),
                         ([], False, None, False))
        self.assertEqual((texts(merge["asks"]), merge["waiting_on_owner"], merge["wait_since"], merge["health_inbox"]),
                         (["Merge PR #6"], True, 100.0, False))
        self.assertEqual(texts(other["asks"]), ["Answer the codex session"])  # a different session is untouched

    def test_asks_without_a_record_are_never_merged(self):
        check = lambda: {"kind": "check", "text": "Check the claude session: no activity 1d", "since": 1.0}
        rows = [dict(row(feature=f), updated=UPDATED, asks=[check()], wait_since=1.0, waiting_on_owner=True)
                for f in ("a", "b")]
        waiting.one_ask_per_session(rows)
        self.assertEqual([texts(r["asks"]) for r in rows], [["Check the claude session: no activity 1d"]] * 2)

    def test_since_and_sort(self):
        self.assertEqual(waiting.since([{"since": 5.0}, {"since": None}, {"since": 3.0}]), 3.0)
        self.assertIsNone(waiting.since([{"since": None}]))
        rows = [dict(row(feature="quiet"), waiting_on_owner=False, wait_since=None),
                dict(row(feature="error"), waiting_on_owner=True, wait_since=None),
                dict(row(feature="new"), waiting_on_owner=True, wait_since=900.0),
                dict(row(feature="old"), waiting_on_owner=True, wait_since=100.0)]
        self.assertEqual([r["feature"] for r in sorted(rows, key=waiting.sort_key)], ["new", "old", "error", "quiet"])
        self.assertEqual([r["feature"] for r in sorted(rows, key=waiting.oldest_first_key)],
                         ["old", "new", "error", "quiet"])


class StuckReasonTest(unittest.TestCase):
    def test_reads_the_newest_stuck_file_at_a_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(repo, state.RELAY_DIR, "f", "reviews"))
            helpers.sh(tmp, "git", "init", "-q", repo)
            self.assertIsNone(waiting.stuck_reason(repo, "HEAD", "f", "build"))     # no commit yet
            folder = os.path.join(repo, state.RELAY_DIR, "f", "reviews")
            for name, reason in (("build-stuck.md", "first stop"), ("build-stuck-2.md", "second stop"),
                                 ("spec-stuck.md", "spec stop")):
                with open(os.path.join(folder, name), "w") as f:
                    f.write('---\n{"reason": "%s", "stage": "x"}\n---\n\nbody\n' % reason)
            with open(os.path.join(folder, "plan-stuck.md"), "w") as f:
                f.write("no frontmatter\n")
            helpers.sh(repo, "git", "add", "-A")
            helpers.sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
            self.assertEqual(waiting.stuck_reason(repo, "HEAD", "f", "build"), "second stop")
            self.assertEqual(waiting.stuck_reason(repo, "HEAD", "f", "spec"), "spec stop")
            self.assertIsNone(waiting.stuck_reason(repo, "HEAD", "f", "plan"))
            self.assertIsNone(waiting.stuck_reason(repo, "HEAD", "f", "idea"))


if __name__ == "__main__":
    unittest.main()
