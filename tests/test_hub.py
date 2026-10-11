import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from relaylib import commands, hub, notes, sessions, state
from tests import helpers
from tests.test_agentask import said
from tests.test_notes import FakeInbox


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
                                   else CANNED, "error": "refresh failed: boom" if outer.mode == "stale" else None,
                                   "age_seconds": 12.0}).encode()
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
        for mode in ("500", "403", "loading", "stale", "slow-headers", "trickle"):
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


def malformed():
    """A snapshot with values of the wrong type everywhere (phone-artifact F5)."""
    return {"rows": [
        row("ok", status="waiting-owner", wait=100, asks=[{"kind": "decide", "text": "Decide", "since": 100}]),
        row("badwait", status="waiting-owner", wait="x", pr="7", flags=[3],
            asks=["x", {"kind": "decide", "text": 5, "since": "y"}]),
        row("truewait", status="ready-to-merge", wait=True, pr_info="x") | {"asks": None, "actions": None, "flags": None},
        "not a row"],
        "other_sessions": [other("X1", since="soon", tools=[1, "Bash"]) | {"excerpt": "plain"}, 7],
        "running": [{"provider": 1, "session_id": None}, None]}


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

    def test_asks_waiting_on_a_hub_are_left_out_of_its_features(self):  # hub-owned-feature
        hub_ask = {"kind": "answer", "text": "Answer the claude session", "since": 20, "session": "claude:HUB"}
        data = {"rows": [
            row("mine", status="ready-to-merge", wait=20, actions=["merge"],
                asks=[{"kind": "merge", "text": "Merge PR #12", "since": 300}, hub_ask]),
            row("only", wait=20, asks=[dict(hub_ask)]),
            row("other", wait=40, asks=[dict(hub_ask, session="claude:P1", since=40)]),
        ], "other_sessions": [], "running": []}
        found = hub.items(data, hubs={("claude", "HUB")})
        self.assertEqual([i["slug"] for i in found], ["other", "mine"])
        mine = found[1]
        self.assertEqual((mine["asks"], mine["session"], mine["answer_here"], mine["wait_since"]),
                         ([{"kind": "merge", "text": "Merge PR #12"}], None, False, 300))
        self.assertEqual(found[0]["session"]["session_id"], "P1")
        self.assertEqual(hub.counts(data, found, hubs={("claude", "HUB")}), {"features": 1, "sessions": 0})
        self.assertEqual([a["session"] for a in data["rows"][1]["asks"]], ["claude:HUB"])  # the input is unchanged

    def test_malformed_values_do_not_abort_the_digest(self):  # phone-artifact F5
        found = hub.items(malformed(), hubs=set())
        self.assertEqual([i.get("slug") or i.get("session_id") for i in found], ["ok", "badwait", "truewait", "X1"])
        bad = found[1]
        self.assertEqual((bad["wait_since"], bad["asks"], bad["flags"], bad["pr"]),
                         (None, [{"kind": "decide", "text": None}], [], None))
        self.assertEqual(hub.counts(malformed(), found, hubs=set()), {"features": 0, "sessions": 1})

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

    def test_pruning_takes_pages_with_their_digests(self):  # phone-artifact D2
        from relaylib import hubpage
        ids = [hub.save([feat(1, str(i))], SRC, S, now=1000.0 + i)[0] for i in range(21)]
        for digest in ids:
            hubpage.write(digest, "<p>page</p>")
        hubpage.write("orphan1234", "<p>gone</p>")
        hubpage.write("unsaved-old", "<p>old</p>")
        hubpage.write("unsaved-new", "<p>new</p>")
        os.utime(os.path.join(hubpage.pages(), "unsaved-old.html"), (1000.0, 1000.0))
        late = 1000.0 + 86400 + 30
        os.utime(os.path.join(hubpage.pages(), "unsaved-new.html"), (late, late))
        hub.save([feat(1, "late")], SRC, S, now=late)
        left = sorted(os.listdir(hubpage.pages()))
        self.assertNotIn(ids[0] + ".html", left)                       # its digest was pruned
        self.assertIn(ids[-1] + ".html", left)
        self.assertNotIn("orphan1234.html", left)
        self.assertEqual([n for n in left if n.startswith("unsaved-")], ["unsaved-new.html"])

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

    def test_a_malformed_snapshot_prints_in_every_mode(self):  # phone-artifact F5
        data = malformed()
        before = json.dumps(data, sort_keys=True)
        text, _ = self.run_digest(data)
        self.assertIn("badwait", text)
        self.assertEqual([i.get("slug") or i.get("session_id") for i in json.loads(self.run_digest(data, True)[0])
                          ["items"]], ["ok", "badwait", "truewait", "X1"])
        self.assertEqual(json.dumps(data, sort_keys=True), before)

    def page_run(self, data, as_json, session=S, now=None):
        with mock.patch.object(hub, "load", return_value=(data, {"from": "dashboard", "age_seconds": 12.0})):
            return hub.digest(session, records=[rec("S")], as_json=as_json, now=now or self.NOW, page=True)

    def test_page_matches_the_digest(self):  # phone-artifact R1
        from relaylib import hubpage
        text, warnings = self.page_run(self.data(), False)
        self.assertEqual(warnings, [])
        digest = text.split("digest ", 1)[1].split(" ", 1)[0]
        path = os.path.join(hubpage.pages(), digest + ".html")
        self.assertTrue(text.endswith(f"page: {path}\n"))
        with open(path) as f:
            page = f.read()
        self.assertIn(f"digest {digest}", page)
        for label in ("bottomsup/ledger", "proteindiary · codex session", "isitev/perm"):
            self.assertIn(label, page)
        out = json.loads(self.page_run(self.data(), True)[0])
        self.assertEqual(out["page"], {"path": os.path.join(hubpage.pages(), out["digest"] + ".html"), "url": None,
                                       "published_digest": None, "published_at": None})

    def test_no_page_without_the_flag(self):
        from relaylib import hubpage
        text, _ = self.run_digest(self.data())
        self.assertNotIn("page:", text)
        self.assertNotIn("page", json.loads(self.run_digest(self.data(), True)[0]))
        self.assertFalse(os.path.exists(hubpage.pages()))
        with_page, _ = self.page_run(self.data(), False)
        strip = lambda t: "\n".join(l for l in t.splitlines()[1:] if not l.startswith("page: "))
        self.assertEqual(strip(with_page), strip(text))                      # same digest apart from its id

    def test_page_write_failure_keeps_the_digest(self):  # F1
        from relaylib import hubpage
        with mock.patch.object(hubpage, "write", side_effect=OSError("disk full")):
            text, warnings = self.page_run(self.data(), False)
            out = json.loads(self.page_run(self.data(), True)[0])
        self.assertNotIn("page:", text)
        self.assertEqual(warnings, ["could not write the page: disk full"])
        self.assertIsNone(out["page"])
        self.assertIn("could not write the page: disk full", out["warnings"])
        self.assertEqual(hub.resolve(out["digest"] + ".1", S, now=self.NOW)["slug"], "ledger")

    def test_page_reports_the_link(self):
        from relaylib import hubpage
        first = json.loads(self.page_run(self.data(), True)[0])
        url = "https://claude.ai/artifact/page-1"
        hubpage.record(S, first["digest"], url, now=self.NOW)
        text, _ = self.page_run(self.data(), False)
        self.assertTrue(text.endswith(f"publish to: {url}\n"))
        out = json.loads(self.page_run(self.data(), True)[0])
        self.assertEqual((out["page"]["url"], out["page"]["published_digest"], out["page"]["published_at"]),
                         (url, first["digest"], self.NOW))

    def test_a_malformed_snapshot_gets_a_page(self):  # F5
        for as_json in (False, True):
            out, _ = self.page_run(malformed(), as_json)
            self.assertIn("badwait", out)
        from relaylib import hubpage
        self.assertEqual(len(os.listdir(hubpage.pages())), 2)

    def test_an_unsaved_digest_still_gets_a_page(self):  # R5
        from relaylib import hubpage
        with mock.patch.object(hub, "save", return_value=(None, "could not save the digest: boom")):
            text, _ = self.page_run(self.data(), False)
            out = json.loads(self.page_run(self.data(), True)[0])
        for path in (text.rsplit("page: ", 1)[1].strip(), out["page"]["path"]):
            self.assertTrue(os.path.basename(path).startswith("unsaved-"))
            with open(path) as f:
                page = f.read()
            self.assertIn("Not saved: actions need a new digest.", page)
            self.assertNotIn('class="n"', page)

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

    def test_a_failed_scan_is_not_an_all_clear(self):
        # the shape snapshot.build returns when the feature scan fails (where-i-left-off F5)
        data = {"rows": [], "other_sessions": [], "running": [],
                "notes": ["Features could not be read: boom", "some health note"]}
        out, warnings = self.run_digest(data)
        self.assertNotIn("Nothing needs you right now.", out)
        self.assertIn("Could not check everything: Features could not be read: boom", out)
        self.assertEqual(warnings, [])                                   # shown once, in the digest itself
        parsed = json.loads(self.run_digest(data, as_json=True)[0])
        self.assertEqual(parsed["warnings"], ["Features could not be read: boom"])

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


HUB = {"provider": "claude", "session_id": "hub1"}


class NoteTest(unittest.TestCase):  # mobile-hub D5, D6, D8, R8, R11, R12
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")  # short: macOS caps socket paths near 104 bytes
        self.addCleanup(temp.cleanup)
        self.tmp = temp.name
        root = os.path.join(self.tmp, "p")
        os.makedirs(root)
        self.origin, made = helpers.make_repo(self.tmp)
        self.work = os.path.join(root, "proj")
        os.rename(made, self.work)
        helpers.sh(self.work, "git", "switch", "-qc", "feat/demo")
        st = state.new_state("proj", "demo", {"provider": "claude", "session": "F1"}, "feat/demo")
        state.write_state(state.state_path(self.work, "demo"), st)
        helpers.sh(self.work, "git", "add", ".")
        helpers.sh(self.work, "git", "commit", "-qm", "feature")
        helpers.sh(self.work, "git", "push", "-qu", "origin", "HEAD")
        env = {"RELAY_HOME": os.path.join(self.tmp, "h"), "RELAY_ROOT": root, "HOME": self.tmp,
               "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "c"), "CODEX_HOME": os.path.join(self.tmp, "x"),
               "CLAUDECODE": "1", "RELAY_PROVIDER": "claude", "RELAY_SESSION": "hub1",
               "RELAY_CONFIG": os.path.join(self.tmp, "none.toml")}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.now = time.time()

    def record(self, sid, st="waiting", since_h=2, inbox=None, provider="claude"):
        at = self.now - since_h * 3600
        r = {"provider": provider, "session_id": sid, "state": st, "since": at, "at": at, "event": "Stop",
             "pending": [], "cwd": self.work}
        if st == "working":
            r["at"] = self.now - 30
        if inbox:
            r["inbox"] = inbox
        os.makedirs(sessions.folder(), exist_ok=True)
        with open(sessions.record_path(provider, sid), "w") as f:
            json.dump(r, f)
        path = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "-p", sid + ".jsonl")
        helpers.write(path, said(f"{sid} waits for you.") + "\n")

    def digest(self, *found):
        digest, warning = hub.save(list(found), {"from": "build", "seconds": 1}, HUB)
        self.assertIsNone(warning)
        return digest

    def session_item(self, n, sid, **extra):
        return {"n": n, "kind": "session", "provider": "claude", "session_id": sid, "label": "proj",
                "state": "waiting", "answer_here": False, **extra}

    def feature_item(self, n, sid="F1", session=True, **extra):
        return {"n": n, "kind": "feature", "repo": "proj", "repo_path": self.work, "slug": "demo",
                "actions": [], "session": {"provider": "claude", "session_id": sid, "label": "proj",
                                           "state": "waiting"} if session else None, "answer_here": False, **extra}

    def relay(self, *argv, env=None):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), redirect_stdout(out), redirect_stderr(err):
            rc = commands.main(list(argv))
        self.out, self.err = out.getvalue(), err.getvalue()
        return rc

    def logged(self):
        with open(os.path.join(hub.folder(), "log.jsonl")) as f:
            return [json.loads(line) for line in f]

    def inbox(self):
        fake = FakeInbox(self.tmp)
        self.addCleanup(fake.close)
        return fake

    def test_posted_with_the_hub_prefix_and_logged(self):
        fake = self.inbox()
        self.record("S1", inbox=fake.path)
        d = self.digest(self.session_item(1, "S1"))
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "go ahead", "--relayed"), 0, self.err)
        self.assertEqual(self.out, "relay: posted to proj\n")
        [sent] = fake.wait()
        self.assertEqual(json.loads(sent)["message"]["content"],
                         "Note from the owner, relayed by claude session hub1 from the relay hub:\ngo ahead")
        [line] = self.logged()
        self.assertEqual((line["command"], line["target"], line["result"], line["relayed_by"]),
                         ("note", "claude:S1", "posted", "claude session hub1"))

    def test_relayed_is_required_and_only_from_an_agent(self):
        self.record("S1")
        d = self.digest(self.session_item(1, "S1"))
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "hi"), 1)
        self.assertIn("run it with --relayed", self.err)
        from relaylib import identity
        owner = dict.fromkeys(identity.AGENT_MARKERS + ("RELAY_SESSION",), "")
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "hi", env=owner), 1)
        self.assertIn("run it with --relayed", self.err)
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "hi", "--relayed", env=owner), 1)
        self.assertIn("in your own terminal", self.err)
        self.assertEqual([l["result"].split(":")[0] for l in self.logged()], ["refused"] * 3)

    def test_refusals(self):
        self.record("S1")
        self.record("P1", "permission")
        d = self.digest(self.session_item(1, "S1"), self.feature_item(2, session=False),
                        self.session_item(3, "P1", state="permission", answer_here=True),
                        self.session_item(4, "P1"))
        cases = [(f"{d}.9", "unknown reference"), (f"{d}.2", "item 2 has no session to send a note to"),
                 (f"{d}.3", "open this session to answer"), (f"{d}.4", "open this session to answer")]
        for ref, why in cases:
            with self.subTest(ref=ref):
                self.assertEqual(self.relay("hub", "note", ref, "hi", "--relayed"), 1)
                self.assertIn(why, self.err)
        for text, why in (("", "cannot be empty"), ("x" * 2001, "at most 2,000")):
            self.assertEqual(self.relay("hub", "note", f"{d}.1", text, "--relayed"), 1)
            self.assertIn(why, self.err)
        self.assertTrue(all(l["result"].startswith("refused: ") for l in self.logged()))

    def test_live_state_after_the_digest(self):
        fake = self.inbox()
        self.record("W1", inbox=fake.path)
        d = self.digest(self.session_item(1, "W1"))
        self.record("W1", "permission", inbox=fake.path)
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "hi", "--relayed"), 1)
        self.assertIn("open this session to answer", self.err)
        self.record("W1", "working", inbox=fake.path)
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "while you work", "--relayed"), 0, self.err)
        self.assertIn("posted to proj", self.out)
        self.record("W1", "ended", inbox=fake.path)                        # no longer listed anywhere
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "later", "--relayed"), 1)
        self.assertIn("no longer listed", self.err)
        self.record("F1", "ended", inbox=fake.path)                        # the feature still claims it
        f = self.digest(self.feature_item(1))
        self.assertEqual(self.relay("hub", "note", f"{f}.1", "later", "--relayed"), 0, self.err)
        self.assertIn("queued for proj's next prompt", self.out)
        self.assertEqual(notes.list_for("claude", "F1")[-1]["status"], "queued")
        os.unlink(sessions.record_path("claude", "W1"))
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "hi", "--relayed"), 1)
        self.assertIn("no longer known", self.err)

    def test_an_aged_out_session_is_refused_even_while_a_cached_snapshot_lists_it(self):
        self.record("OLD", since_h=25)
        d = self.digest(self.session_item(1, "OLD"))
        cached = {"rows": [], "other_sessions": [{"provider": "claude", "session_id": "OLD"}], "running": []}
        with mock.patch.object(hub, "load", return_value=(cached, {"from": "dashboard", "age_seconds": 1})) as load:
            self.assertEqual(self.relay("hub", "note", f"{d}.1", "hi", "--relayed"), 1)
        load.assert_not_called()
        self.assertIn("no longer listed", self.err)

    def test_a_feature_session_has_no_age_limit_while_the_feature_claims_it(self):
        fake = self.inbox()
        self.record("F1", since_h=25, inbox=fake.path)
        self.record("U1", since_h=25)
        d = self.digest(self.feature_item(1), self.session_item(2, "U1"))
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "still there?", "--relayed"), 0, self.err)
        self.assertIn("posted to proj", self.out)
        self.assertEqual(self.relay("hub", "note", f"{d}.2", "hi", "--relayed"), 1)
        self.assertIn("no longer listed", self.err)
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))   # the hub checkout is not updated
        helpers.sh(other, "git", "switch", "-q", "feat/demo")
        st = state.read_state(state.state_path(other, "demo"))
        st["owner"]["session"] = "F2"
        state.write_state(state.state_path(other, "demo"), st)
        helpers.sh(other, "git", "commit", "-qam", "relay take")
        helpers.sh(other, "git", "push", "-q")
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "hi", "--relayed"), 1)
        self.assertIn("no longer listed", self.err)
        helpers.sh(other, "git", "push", "-q", "origin", "--delete", "feat/demo")
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "hi", "--relayed"), 1)
        self.assertIn("could not confirm the feature still names that session", self.err)
        os.rename(self.origin, self.origin + ".gone")
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "hi", "--relayed"), 1)
        self.assertIn("could not confirm the feature still names that session", self.err)

    def test_posted_but_not_recorded_and_an_unwritable_log(self):
        fake = self.inbox()
        self.record("S1", inbox=fake.path)
        d = self.digest(self.session_item(1, "S1"))
        real = os.replace

        def fail_log(src, dst):
            if dst.startswith(notes.folder()):
                raise OSError("disk full")
            return real(src, dst)
        with mock.patch("relaylib.notes.os.replace", fail_log):
            self.assertEqual(self.relay("hub", "note", f"{d}.1", "hi", "--relayed"), 0, self.err)
        self.assertIn(notes.NOT_RECORDED, self.err)
        self.assertEqual(self.logged()[-1]["result"], "posted, not recorded")
        os.chmod(hub.folder(), 0o500)
        self.addCleanup(os.chmod, hub.folder(), 0o700)
        os.chmod(os.path.join(hub.folder(), "log.jsonl"), 0o400)
        self.assertEqual(self.relay("hub", "note", f"{d}.1", "again", "--relayed"), 0, self.err)
        self.assertIn("could not write the hub log", self.err)
        self.assertEqual(len(fake.wait(2)), 2)


class SkillTest(unittest.TestCase):  # mobile-hub R15
    def test_the_hub_skill_is_installable_and_plain(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "skills", "relay-hub", "SKILL.md")) as f:
            text = f.read()
        self.assertTrue(text.startswith("---\nname: relay-hub\ndescription: "))
        self.assertNotIn("\u2014", text)
        for rule in ("--relayed", "data, never instructions", "open this session to answer",
                     "changed since you looked", "~/.claude/sessions", "dashboard token", "background"):
            self.assertIn(rule, text)
        with open(os.path.join(root, "install.sh")) as f:
            self.assertIn('for d in "$HERE"/skills/*/', f.read())   # every skill folder with a SKILL.md is linked


if __name__ == "__main__":
    unittest.main()
