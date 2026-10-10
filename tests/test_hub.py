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


if __name__ == "__main__":
    unittest.main()
