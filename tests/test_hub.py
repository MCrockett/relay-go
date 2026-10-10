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


if __name__ == "__main__":
    unittest.main()
