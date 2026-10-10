import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from relaylib import hub


class HubHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="relay-hub-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "relayhome")})
        p.start()
        self.addCleanup(p.stop)


def rec(sid, state="waiting", provider="claude"):
    return {"provider": provider, "session_id": sid, "state": state}


class RegistryTest(HubHome):  # mobile-hub D9, R14
    def test_register_adds_once_and_drops_ended_and_missing_sessions(self):
        self.assertIsNone(hub.register("claude", "A", [rec("A")]))
        self.assertIsNone(hub.register("claude", "A", [rec("A")]))
        self.assertEqual(hub.registered(), {("claude", "A")})
        self.assertIsNone(hub.register("codex", "B", [rec("A", "ended"), rec("B", provider="codex")]))
        self.assertEqual(hub.registered(), {("codex", "B")})
        self.assertIsNone(hub.register("claude", "C", [rec("C")]))  # B's record is gone
        self.assertEqual(hub.registered(), {("claude", "C")})

    def test_two_hubs_registering_at_once_are_both_kept(self):
        records = [rec(f"S{i}") for i in range(8)]
        threads = [threading.Thread(target=hub.register, args=("claude", f"S{i}", records)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(hub.registered(), {("claude", f"S{i}") for i in range(8)})

    def test_an_unreadable_file_leaves_nothing_out_and_is_rewritten(self):
        os.makedirs(hub.folder())
        with open(os.path.join(hub.folder(), "sessions.json"), "w") as f:
            f.write("{not json")
        self.assertEqual(hub.registered(), set())
        self.assertIsNone(hub.register("claude", "A", [rec("A")]))
        self.assertEqual(hub.registered(), {("claude", "A")})

    def test_an_unwritable_folder_is_a_warning(self):
        os.makedirs(hub.folder())
        open(os.path.join(hub.folder(), "lock"), "a").close()
        os.chmod(hub.folder(), 0o500)
        self.addCleanup(os.chmod, hub.folder(), 0o700)
        warning = hub.register("claude", "A", [rec("A")])
        self.assertIn("could not record this hub session", warning)
        self.assertEqual(hub.registered(), set())


CANNED = {"rows": [], "features": [], "other_sessions": [], "running": [], "left_off": []}
TOKEN = "tok-secret-123"


class FakeDashboard:
    """A local stand-in for relay ui's /api/snapshot: mode picks how it answers."""

    def __init__(self, test, mode="ok"):
        import http.server
        self.mode = mode
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                import time as t
                if self.headers.get("X-Relay-Token") != TOKEN or outer.mode == "403":
                    self.send_response(403)
                    self.end_headers()
                    return
                if outer.mode == "500":
                    self.send_response(500)
                    self.end_headers()
                    return
                if outer.mode == "slow-headers":
                    t.sleep(1.5)
                body = json.dumps({"loading": outer.mode == "loading", "data": None if outer.mode == "loading"
                                   else CANNED, "error": None, "age_seconds": 12.0}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if outer.mode == "trickle":
                    for b in body:
                        self.wfile.write(bytes([b]))
                        self.wfile.flush()
                        t.sleep(0.05)
                    return
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        os.makedirs(os.environ["RELAY_HOME"], exist_ok=True)
        with open(os.path.join(os.environ["RELAY_HOME"], "ui.json"), "w") as f:
            json.dump({"pid": os.getpid(), "port": self.server.server_address[1], "token": TOKEN}, f)


def no_processes(*a, **k):
    raise AssertionError("no git or gh process may run with a dashboard's data")


class LoadTest(HubHome):  # mobile-hub D3, R6
    def test_a_running_dashboard_is_used_with_no_git_or_gh_call(self):
        FakeDashboard(self)
        with mock.patch("subprocess.run", no_processes), mock.patch("relaylib.gitops.run", no_processes), \
                mock.patch("relaylib.ui.snapshot.build", no_processes):
            found = hub.load()
        self.assertEqual(found, (CANNED, {"from": "dashboard", "age_seconds": 12.0}))
        self.assertNotIn(TOKEN, repr(found))

    def test_falls_back_to_a_build_when_the_dashboard_cannot_answer(self):
        import time
        built = {"rows": [], "built": True}
        for mode in ("500", "403", "loading", "slow-headers", "trickle"):
            with self.subTest(mode=mode):
                dash = FakeDashboard(self, mode)
                with mock.patch("relaylib.ui.snapshot.build", return_value=built):
                    started = time.monotonic()
                    data, source = hub.load(timeout=0.3)
                    self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual((data, source["from"]), (built, "build"))
                self.assertNotIn(TOKEN, repr((data, source)))
                dash.server.shutdown()

    def test_a_dead_dashboard_falls_back_to_a_build(self):
        os.makedirs(os.environ["RELAY_HOME"], exist_ok=True)
        with open(os.path.join(os.environ["RELAY_HOME"], "ui.json"), "w") as f:
            json.dump({"pid": 999999, "port": 1, "token": TOKEN}, f)
        with mock.patch("relaylib.ui.snapshot.build", return_value={"rows": []}):
            self.assertEqual(hub.load(timeout=0.3)[1]["from"], "build")


def row(slug, repo="bottomsup", status="drafting", wait=None, asks=(), flags=(), actions=(), **extra):
    r = {"repo": repo, "feature": slug, "stage": "build", "status": status, "flags": list(flags),
         "asks": list(asks), "wait_since": wait, "waiting_on_owner": bool(asks), "actions": list(actions),
         "pr": 12, "pr_info": {"state": "OPEN"}, "ci": "green", "seen": {"commit": "c" + slug},
         "repo_path": "/p/" + repo}
    r.update(extra)
    return r


def other(sid, state="waiting", since=None, tools=(), label="proteindiary", provider="codex"):
    return {"provider": provider, "session_id": sid, "label": label, "state": state, "since": since,
            "pending_tools": list(tools), "excerpt": {"source": "agent", "text": f"{sid} says hi"}}


class ItemsTest(HubHome):  # mobile-hub D2, D8, R1 to R3
    def data(self):
        return {"rows": [
            row("merge", status="ready-to-merge", wait=300, asks=[{"kind": "merge", "text": "Merge PR #12"}],
                actions=["merge", "review"]),
            row("stale", status="ready-to-merge", flags=["stale spec GO: re-review before going on"],
                actions=["review"]),
            row("decide", repo="idlekeeper", status="waiting-owner", wait=100,
                asks=[{"kind": "decide", "text": "Decide: build review stopped"}], actions=["go", "extra-round"]),
            row("perm", repo="isitev", wait=200,
                asks=[{"kind": "approve", "text": "Approve Bash in the claude session", "session": "claude:P1"}]),
            row("busy"),
            row("gone", stage="done", status="done"),
            {"repo": "broken", "feature": "?", "error": "could not read state", "checkout": "/p/broken"},
            row("unready", status="ready-to-merge", wait=50, asks=[{"kind": "merge", "text": "Merge PR #12"}],
                actions=["review"]),
        ], "other_sessions": [other("Q1", since=150), other("PERM", "permission", since=400, tools=["Bash"]),
                              other("ASK", "permission", since=None, tools=["AskUserQuestion"]),
                              other("HUB", since=10, provider="claude")],
            "running": [{"provider": "claude", "session_id": "R1"}, {"provider": "claude", "session_id": "P1"},
                        {"provider": "claude", "session_id": "HUB"}]}

    def test_inclusion_order_sessions_and_actions(self):
        found = hub.items(self.data(), hubs={("claude", "HUB")})
        self.assertEqual([(i["n"], i.get("slug") or i.get("session_id")) for i in found],
                         [(1, "unready"), (2, "decide"), (3, "Q1"), (4, "perm"), (5, "merge"), (6, "PERM"),
                          (7, "stale"), (8, "?"), (9, "ASK")])
        by = {i.get("slug") or i.get("session_id"): i for i in found}
        self.assertEqual(by["unready"]["actions"], ["review"])            # the dashboard's filtered list, unchanged
        self.assertEqual(by["merge"]["actions"], ["merge", "review"])
        self.assertEqual(by["perm"]["session"], {"provider": "claude", "session_id": "P1", "label": "isitev",
                                                 "state": "permission"})
        self.assertTrue(by["perm"]["answer_here"])
        self.assertIsNone(by["decide"]["session"])
        self.assertFalse(by["decide"]["answer_here"])
        self.assertTrue(by["PERM"]["answer_here"])
        self.assertTrue(by["ASK"]["answer_here"])
        self.assertFalse(by["Q1"]["answer_here"])
        self.assertEqual(by["?"]["kind"], "error")
        self.assertEqual((by["merge"]["pr"], by["merge"]["pr_state"], by["merge"]["ci"]), (12, "OPEN", "green"))
        self.assertEqual(by["merge"]["seen"], {"commit": "cmerge"})
        self.assertEqual(hub.counts(self.data(), found, hubs={("claude", "HUB")}), {"features": 1, "sessions": 1})

    def test_a_registered_hub_is_left_out_of_a_cached_snapshot(self):
        hub.register("claude", "HUB", [rec("HUB")])
        found = hub.items(self.data())
        self.assertNotIn("HUB", [i.get("session_id") for i in found])
        self.assertEqual(hub.counts(self.data(), found)["sessions"], 1)


S = {"provider": "claude", "session_id": "S"}
SRC = {"from": "build", "seconds": 1.0}


def feat(n, slug):
    return {"n": n, "kind": "feature", "slug": slug}


class DigestStoreTest(HubHome):  # mobile-hub D4, R7, R7a
    def test_save_and_resolve(self):
        digest, warning = hub.save([feat(1, "x"), feat(2, "y")], SRC, S, now=1000.0)
        self.assertIsNone(warning)
        self.assertEqual(hub.resolve(f"{digest}.2", S, now=1001.0)["slug"], "y")
        mode = os.stat(os.path.join(hub.folder(), "digests", digest + ".json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual([n for n in os.listdir(os.path.join(hub.folder(), "digests"))], [digest + ".json"])

    def test_the_same_millisecond_and_random_characters_never_overwrite(self):
        draws = iter("aaaa" "aaaa" "bbbb")
        with mock.patch("secrets.choice", lambda alphabet: next(draws)):
            first, _ = hub.save([feat(1, "x")], SRC, S, now=1000.0)
            second, _ = hub.save([feat(1, "y")], SRC, S, now=1000.0)
        self.assertNotEqual(first, second)
        self.assertEqual(hub.resolve(f"{first}.1", S, now=1000.0)["slug"], "x")
        self.assertEqual(hub.resolve(f"{second}.1", S, now=1000.0)["slug"], "y")

    def test_concurrent_saves_get_different_ids(self):
        ids = []
        threads = [threading.Thread(target=lambda: ids.append(hub.save([feat(1, "x")], SRC, S, now=1000.0)[0]))
                   for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(ids)), 10)
        self.assertNotIn(None, ids)

    def test_an_old_reference_is_refused_after_the_folder_is_wiped(self):
        a, _ = hub.save([feat(1, "x")], SRC, S, now=1000.0)
        shutil.rmtree(hub.folder())
        b, _ = hub.save([feat(1, "y")], SRC, S, now=1000.5)
        with self.assertRaisesRegex(hub.RelayError, "unknown reference"):
            hub.resolve(f"{a}.1", S, now=1001.0)
        self.assertEqual(hub.resolve(f"{b}.1", S, now=1001.0)["slug"], "y")

    def test_unknown_references(self):
        digest, _ = hub.save([feat(1, "x")], SRC, S, now=1000.0)
        bad = os.path.join(hub.folder(), "digests", "zzzzzzzz.json")
        with open(bad, "w") as f:
            f.write("{not json")
        refs = ["abc", "abc.", ".1", "abc.x", f"{digest}.0", f"{digest}.2", "zzzzzzzz.1", None, f"{digest}.1 "]
        for ref in refs:
            with self.subTest(ref=ref), self.assertRaisesRegex(hub.RelayError, "unknown reference"):
                hub.resolve(ref, S, now=1001.0)
        with self.assertRaisesRegex(hub.RelayError, "unknown reference"):
            hub.resolve(f"{digest}.1", {"provider": "claude", "session_id": "other"}, now=1001.0)
        with self.assertRaisesRegex(hub.RelayError, "unknown reference"):
            hub.resolve(f"{digest}.1", None, now=1001.0)
        with self.assertRaisesRegex(hub.RelayError, "unknown reference"):
            hub.resolve(f"{digest}.1", S, now=1000.0 + 86401)
        self.assertEqual(hub.resolve(f"{digest}.1", S, now=1000.0 + 86400)["slug"], "x")

    def test_pruning_keeps_the_newest_twenty(self):
        ids = [hub.save([feat(1, str(i))], SRC, S, now=1000.0 + i)[0] for i in range(25)]
        left = sorted(os.listdir(os.path.join(hub.folder(), "digests")))
        self.assertEqual(len(left), 25)                               # none older than 24 hours yet
        hub.save([feat(1, "late")], SRC, S, now=1000.0 + 86400 + 30)
        left = os.listdir(os.path.join(hub.folder(), "digests"))
        self.assertEqual(len(left), 20)
        self.assertNotIn(ids[0] + ".json", left)
        self.assertIn(ids[-1] + ".json", left)

    def test_an_unwritable_folder_gives_a_warning(self):
        os.makedirs(os.path.join(hub.folder(), "digests"))
        os.chmod(os.path.join(hub.folder(), "digests"), 0o500)
        self.addCleanup(os.chmod, os.path.join(hub.folder(), "digests"), 0o700)
        digest, warning = hub.save([feat(1, "x")], SRC, S)
        self.assertIsNone(digest)
        self.assertIn("could not save the digest", warning)


class OutputTest(HubHome):  # mobile-hub D2, R1 to R6
    NOW = 10_000.0

    def data(self):
        return {"rows": [
            row("ledger", status="ready-to-merge", wait=self.NOW - 7200,
                asks=[{"kind": "merge", "text": "Merge PR #12"}], actions=["merge", "review"]),
            row("perm", repo="isitev", wait=self.NOW - 600, pr=None, pr_info={},
                asks=[{"kind": "approve", "text": "Approve Bash in the claude session", "session": "claude:P1"}]),
            row("busy")],
            "other_sessions": [other("Q1", since=self.NOW - 2400)],
            "running": [{"provider": "claude", "session_id": "R1"}]}

    def run_digest(self, data, as_json=False, session=S):
        with mock.patch.object(hub, "load", return_value=(data, {"from": "dashboard", "age_seconds": 12.0})):
            return hub.digest(session, records=[rec("S")], as_json=as_json, now=self.NOW)

    def test_text(self):
        out, warnings = self.run_digest(self.data())
        self.assertEqual(warnings, [])
        digest = out.split("digest ", 1)[1].split(" ", 1)[0]
        self.assertEqual(out, f"""relay hub · digest {digest} · dashboard data 12s old
1. bottomsup/ledger · build ready-to-merge · waiting 2h
    Merge PR #12
    PR #12 · CI green
    actions: merge, review
2. proteindiary · codex session · waiting 40m
    "Q1 says hi"
3. isitev/perm · build drafting · waiting 10m
    Approve Bash in the claude session
    session: isitev (claude) · permission
    open this session to answer
Nothing else needs you: 1 feature in progress, 1 session working.
""")
        self.assertEqual(hub.resolve(f"{digest}.3", S, now=self.NOW)["session"]["session_id"], "P1")
        self.assertEqual(hub.registered(), {("claude", "S")})

    def test_nothing_waiting(self):
        out, _ = self.run_digest({"rows": [row("busy")], "other_sessions": [], "running": []})
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("relay hub · digest "))
        self.assertEqual(lines[1:], ["Nothing needs you right now.",
                                     "Nothing else needs you: 1 feature in progress, 0 sessions working."])

    def test_an_unsaved_digest_has_no_numbers(self):
        os.makedirs(os.path.join(hub.folder(), "digests"))
        os.chmod(os.path.join(hub.folder(), "digests"), 0o500)
        self.addCleanup(os.chmod, os.path.join(hub.folder(), "digests"), 0o700)
        out, warnings = self.run_digest(self.data())
        self.assertTrue(out.startswith("relay hub · not saved: actions need a new digest · dashboard data 12s old\n"))
        self.assertIn("\n- bottomsup/ledger", out)
        self.assertNotIn("\n1. ", out)
        self.assertIn("could not save the digest", warnings[0])

    def test_json_parses_with_counts_and_warnings(self):
        os.makedirs(hub.folder())
        os.chmod(hub.folder(), 0o500)
        self.addCleanup(os.chmod, hub.folder(), 0o700)
        out, warnings = self.run_digest(self.data(), as_json=True)
        self.assertEqual(warnings, [])
        parsed = json.loads(out)
        self.assertIsNone(parsed["digest"])
        self.assertEqual(parsed["counts"], {"features": 1, "sessions": 1})
        self.assertEqual(len(parsed["warnings"]), 2)                    # registration and save
        self.assertEqual([i["n"] for i in parsed["items"]], [1, 2, 3])
        self.assertNotIn("seen", parsed["items"][0])
        self.assertNotIn("repo_path", parsed["items"][0])
        self.assertEqual(parsed["source"], {"from": "dashboard", "age_seconds": 12.0})

    def test_owner_terminal_registers_nothing_and_the_token_is_never_printed(self):
        FakeDashboard(self)
        with mock.patch("relaylib.ui.snapshot.build", no_processes):
            for as_json in (False, True):
                out, _ = hub.digest(None, records=[], as_json=as_json)
                self.assertNotIn(TOKEN, out)
        self.assertEqual(hub.registered(), set())


if __name__ == "__main__":
    unittest.main()
