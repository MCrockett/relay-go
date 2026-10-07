import concurrent.futures
import http.client
import json
import os
import re
import shutil
import socket
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from relaylib import commands, config, identity, owneractions, reviewjobs
from relaylib.errors import RelayError
from relaylib.ui import server, snapshot


class ServerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = temp.name
        patch = mock.patch.dict(os.environ, {"RELAY_HOME": self.tmp})
        patch.start()
        self.addCleanup(patch.stop)
        self.cache = snapshot.Cache(builder=lambda: {"rows": [], "features": [], "usage": {"providers": {}}})
        from relaylib import runner
        self.addCleanup(reviewjobs._STOPPING.clear)  # closing a Runtime stops review jobs process-wide
        self.addCleanup(runner._STOPPED.clear)
        self.server = server.bind(0, "test-token", self.cache)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, method="GET", payload=None, token="test-token", host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Relay-Token"] = token
        if payload is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, json.dumps(payload) if payload is not None else None, headers)
        response = conn.getresponse()
        code, data = response.status, response.read().decode()
        conn.close()
        return code, data

    def test_token_host_and_page_checks(self):
        for path, token, host in (("/", None, None), ("/?t=wrong", None, None),
                                  ("/api/snapshot", None, None), ("/api/snapshot", "wrong", None),
                                  ("/?t=%C3%A9", None, None),
                                  ("/?t=test-token", "test-token", "evil.example")):
            self.assertEqual(self.request(path, token=token, host=host)[0], 403)
        code, page = self.request("/?t=test-token", token=None)
        self.assertEqual(code, 200)
        self.assertIn("relay", page)

    def test_page_sections_and_local_assets(self):
        code, page = self.request("/?t=test-token")
        self.assertEqual(code, 200)
        for section in ('id="inbox"', 'id="features"', 'id="usage"', 'id="detail"', 'id="confirm"'):
            self.assertIn(section, page)
        self.assertIn("X-Relay-Token", page)
        self.assertIn("history.replaceState", page)
        self.assertNotRegex(page, r'<(?:script|link)[^>]+(?:src|href)=["\']https?://')

    def test_page_is_compact(self):
        page = self.request("/?t=test-token")[1]
        for filler in ("A little oversight", "rail-note", "<footer", "The next move is yours", 'class="stats"'):
            self.assertNotIn(filler, page)
        for header in ('id="summary"', 'id="usage-strip"', 'id="refresh"', 'id="updated"'):
            self.assertIn(header, page)
        # Inbox cards offer only Merge PR; overrides and Release stay in the feature detail.
        self.assertIn("const CARD_ACTIONS=new Set(['merge']);", page)
        self.assertIn("actions(row,CARD_ACTIONS)", page)
        self.assertIn("actions(d)", page)

    def test_page_reads_like_an_inbox(self):
        page = self.request("/?t=test-token")[1]
        self.assertRegex(page, r'<link rel="icon" href="data:image/svg\+xml,')
        # Models (reviewers and usage) has its own screen; the summary and usage strip stay in the shared header.
        for view in ('id="projects-view"', 'id="usage-view"', 'id="tab-projects"', 'id="tab-models"'):
            self.assertIn(view, page)
        self.assertLess(page.index('id="usage-strip"'), page.index('id="projects-view"'))
        self.assertLess(page.index('id="projects-view"'), page.index('id="usage"'))
        for behavior in ("weekElapsed", "class=\"tick\"", "'selected'", "acted", "document.title"):
            self.assertIn(behavior.replace('class="tick"', "'tick'"), page)
        # Amber as soon as usage passes the week tick, and an action's lock clears only on a changed snapshot.
        self.assertIn("u>w?'amber'", page)
        self.assertIn("r.seen?.commit!==a.commit)acted.delete(k)", page)
        self.assertNotIn("Date.now()-a.at", page)

    def test_messages_share_one_reserved_row(self):
        page = self.request("/?t=test-token")[1]
        self.assertIn('id="message"', page)
        self.assertLess(page.index('id="message"'), page.index("</header>"))  # inside the sticky header
        for gone in ('id="status"', 'id="result"', 'class="notice"'):
            self.assertNotIn(gone, page)

    def test_endpoints_loading_refresh_and_file(self):
        code, text = self.request("/api/snapshot")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(text)["loading"])
        with mock.patch.object(self.cache, "refresh") as refresh:
            self.assertEqual(self.request("/api/refresh", "POST", {})[0], 202)
            refresh.assert_called_once()
        data = {"rows": [], "features": [{"repo_path": "/fixture", "feature": "demo"}], "usage": {"providers": {}}}
        with mock.patch.object(self.cache, "get", return_value={"data": data}):
            self.assertEqual(json.loads(self.request("/api/usage")[1]), data["usage"])
            self.assertEqual(json.loads(self.request("/api/feature?repo=/fixture&slug=demo")[1]), data["features"][0])
        with mock.patch("relaylib.ui.snapshot.read_file", return_value="Review text") as read:
            code, text = self.request("/api/file?repo=/fixture&slug=demo&ref=" + "a" * 40 + "&path=docs/relay/demo/reviews/spec-1.md")
            self.assertEqual((code, text), (200, "Review text"))
            self.assertEqual(read.call_args.args[-1], "docs/relay/demo/reviews/spec-1.md")
        with mock.patch("relaylib.ui.snapshot.read_file", side_effect=RelayError("outside feature")):
            self.assertEqual(self.request("/api/file?repo=/fixture&slug=demo&ref=x&path=README.md")[0], 400)

    def test_action_conflicts_errors_and_success(self):
        payload = {"action": "go", "repo": "/fixture", "slug": "demo", "seen": {"commit": "a" * 40}}
        with mock.patch("relaylib.ui.snapshot.allowed_repo", return_value="/fixture"), \
                mock.patch("relaylib.owneractions.run_override") as action, mock.patch.object(self.cache, "refresh") as refresh:
            for failure in (owneractions.Conflict("changed since you looked", {"commit": "b" * 40}),
                            owneractions.Conflict("busy")):
                action.side_effect = failure
                code, text = self.request("/api/action", "POST", payload)
                self.assertEqual(code, 409)
                self.assertEqual(json.loads(text)["fresh"], failure.fresh)
            action.side_effect = RelayError("push rejected")
            self.assertEqual(self.request("/api/action", "POST", payload)[0], 400)
            action.side_effect, action.return_value = None, {"message": "recorded"}
            self.assertEqual(self.request("/api/action", "POST", payload)[0], 200)
            self.assertEqual(refresh.call_count, 4)

    def test_review_requests_start_in_the_background(self):
        payload = {"action": "review", "repo": "/fixture", "slug": "demo", "seen": {"commit": "a" * 40},
                   "reviewer": "codex:gpt-6-astra"}
        job = mock.Mock()
        job.spec, job.id, job.slug = config.ModelSpec("codex", "gpt-6-astra"), "job-1", "demo"
        job.run.return_value = {"ok": True, "message": "done"}
        with mock.patch("relaylib.ui.snapshot.allowed_repo", return_value="/fixture"), \
                mock.patch("relaylib.reviewjobs.prepare", return_value=job) as prepare, \
                mock.patch.object(self.cache, "refresh") as refresh:
            code, text = self.request("/api/action", "POST", payload)
            self.assertEqual(code, 202, text)
            self.assertEqual(json.loads(text)["job"], "job-1")
            self.assertIn("codex:gpt-6-astra is reviewing demo", json.loads(text)["message"])
            prepare.assert_called_once_with("/fixture", "demo", payload["seen"], "codex:gpt-6-astra")
            for _ in range(100):
                if job.run.called and refresh.call_count >= 2:
                    break
                time.sleep(0.02)
            job.run.assert_called_once()
            self.assertGreaterEqual(refresh.call_count, 2)          # on acceptance and when the job ends
            prepare.side_effect = reviewjobs.Busy("a review is already running for this feature")
            self.assertEqual(self.request("/api/action", "POST", payload)[0], 409)
            prepare.side_effect = RelayError("the PR is not open")
            code, text = self.request("/api/action", "POST", payload)
            self.assertEqual((code, json.loads(text)["error"]), (400, "the PR is not open"))

    def test_roles_endpoints(self):
        cfg = os.path.join(self.tmp, "config.toml")
        with open(cfg, "w") as f:
            f.write('[review.prefer]\nclaude = ["codex:gpt-6-astra"]\n')
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with mock.patch.dict(os.environ, {"RELAY_CONFIG": cfg, "RELAY_ROOT": root}), \
                mock.patch.object(self.cache, "refresh") as refresh:
            seen = snapshot.reviewers()["revision"]
            body = {"author": "claude", "table": "timed", "entries": ["claude:claude-fable-5-1@high"],
                    "until": "23:59", "seen": seen}
            code, text = self.request("/api/roles", "POST", body)
            self.assertEqual(code, 200, text)
            result = json.loads(text)
            self.assertIn("until", result["message"])
            self.assertEqual(result["reviewers"]["authors"]["claude"]["entries"][0]["id"],
                             "claude:claude-fable-5-1@high")
            self.assertGreaterEqual(refresh.call_count, 1)
            code, text = self.request("/api/roles", "POST", body)                       # seen is now stale
            self.assertEqual(code, 409, text)
            fresh = json.loads(text)["fresh"]["revision"]
            for bad in ({"entries": ["gemini:x"]}, {"entries": []}, {"until": "2020-01-01T00:00Z"},
                        {"table": "permanent"}, {"entries": "codex:a"}, {"seen": 5}, {"author": "nobody"}):
                with self.subTest(bad=bad):
                    code, text = self.request("/api/roles", "POST", {**body, "seen": fresh, **bad})
                    self.assertEqual(code, 400, text)
            self.assertEqual(snapshot.reviewers()["revision"], fresh)                    # nothing written
            self.assertEqual(self.request("/api/roles/end", "POST", {"author": "claude", "seen": seen})[0], 409)
            self.assertEqual(self.request("/api/roles/end", "POST", {"author": "nobody", "seen": fresh})[0], 400)
            code, text = self.request("/api/roles/end", "POST", {"author": "claude", "seen": fresh})
            self.assertEqual(code, 200, text)
            self.assertEqual(json.loads(text)["reviewers"]["authors"]["claude"]["until"], None)
            code, text = self.request("/api/roles/end", "POST",
                                      {"author": "claude", "seen": snapshot.reviewers()["revision"]})
            self.assertEqual((code, json.loads(text)["message"]), (200, "no temporary table for claude"))
            self.assertEqual(self.request("/api/roles", "POST", body, token="wrong")[0], 403)

    def test_closing_the_server_stops_review_jobs(self):
        runtime = server.Runtime(0, cache=snapshot.Cache(builder=lambda: {"rows": []}))
        runtime.start()
        with mock.patch("relaylib.reviewjobs.shutdown") as shutdown:
            runtime.close()
        shutdown.assert_called_once()

    def test_page_offers_request_review(self):
        page = self.request("/?t=test-token")[1]
        for piece in ('<dialog id="review-request"', 'id="rr-reviewer"', "review:'Request review'",
                      "review_choices", "review_job", "Review running: "):
            self.assertIn(piece, page)
        # Outcomes are announced by job id, even when the page never saw the job running.
        for piece in ("announced.has(j.id)", "j.ended_at", "Last requested review"):
            self.assertIn(piece, page)
        self.assertNotIn("prev==='running'", page)

    def test_reviewer_activity_tables_sort_by_any_column(self):
        page = self.request("/?t=test-token")[1]
        for piece in ("ledgerSort", "aria-sort", "sortLedger(", "nextSort(", "data-sort-key", "aria-hidden"):
            self.assertIn(piece, page)
        # Focus returns to the same header after every re-render (a click or the periodic refresh).
        self.assertIn("sortKind&&", page)
        self.assertIn("focus({preventScroll:true})", page)   # a refresh never scrolls the page
        self.assertIn("ledgerShown", page)                    # unchanged data is not rebuilt on a refresh

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_sort_rules_run_in_node(self):
        page = self.request("/?t=test-token")[1]
        funcs = re.search(r"function sortLedger\(.*?(?=function renderLedger\()", page).group(0)
        script = funcs + """
const rows=[{name:'beta',runs:3},{name:'Alpha',runs:12},{name:'gamma',runs:3}];
const names=(k,d)=>sortLedger(rows,k,d).map(r=>r.name).join(',');
console.log(JSON.stringify({az:names('name','ascending'), most:names('runs','descending'),
  least:names('runs','ascending'), untouched:rows.map(r=>r.name).join(','),
  firstNum:nextSort({key:'name',dir:'ascending'},'runs'), firstName:nextSort({key:'runs',dir:'descending'},'name'),
  flip:nextSort({key:'runs',dir:'descending'},'runs')}));"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["az"], "Alpha,beta,gamma")            # names A to Z, ignoring case
        self.assertEqual(out["most"], "Alpha,beta,gamma")          # largest first; ties by name
        self.assertEqual(out["least"], "beta,gamma,Alpha")
        self.assertEqual(out["untouched"], "beta,Alpha,gamma")     # the data itself is not reordered
        self.assertEqual(out["firstNum"], {"key": "runs", "dir": "descending"})
        self.assertEqual(out["firstName"], {"key": "name", "dir": "ascending"})
        self.assertEqual(out["flip"], {"key": "runs", "dir": "ascending"})

    def test_models_tab_and_reviewers_panel(self):
        page = self.request("/?t=test-token")[1]
        for piece in ('id="tab-models"', ">Models</a>", "#view=models", "view==='usage'", 'id="reviewers"',
                      'id="reviewer-rows"', 'id="writer-roles"', "informational", "temporary until",
                      "End now", "Edit permanent", "Set temporary", "Edit temporary", "renderReviewers()",
                      "overrides your table for that project"):
            self.assertIn(piece, page)
        self.assertNotIn(">Usage</a>", page)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_an_expired_timed_table_stops_showing_without_the_server(self):
        page = self.request("/?t=test-token")[1]
        funcs = re.search(r"function liveTimed\(.*?(?=function renderReviewers\()", page, re.S).group(0)
        script = funcs + """
const a={until:1000,entries:[{id:'t'}],permanent:[{id:'p'}]};
console.log(JSON.stringify({before:shownEntries(a,999000).map(e=>e.id), after:shownEntries(a,1000000).map(e=>e.id),
  live:liveTimed(a,999000), none:liveTimed({until:null},0)}));"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, {"before": ["t"], "after": ["p"], "live": True, "none": False})

    def test_roles_editor_on_the_page(self):
        page = self.request("/?t=test-token")[1]
        for piece in ('<dialog id="roles-edit"', 'id="re-list"', '<label for="re-add">', 'list="re-known"',
                      '<label for="re-until">', 'id="re-save"', "'/api/roles'", "'/api/roles/end'",
                      "until_iso", "relay checks only its shape", "Changed since you looked",
                      "rolesReturn", "kind==='roles'"):
            self.assertIn(piece, page)
        # Escape on either dialog returns focus too, not only the Cancel buttons (R17).
        self.assertIn("$('confirm').addEventListener('close'", page)
        self.assertIn("$('roles-edit').addEventListener('close'", page)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_editor_list_rules_run_in_node(self):
        page = self.request("/?t=test-token")[1]
        funcs = re.search(r"function editList\(.*?(?=function openRolesEditor\()", page, re.S).group(0)
        script = funcs + """
const L=[{provider:'codex',model:'a',effort:null},{provider:'claude',model:'b',effort:'max'}];
const ids=r=>r.list.map(e=>e.provider+':'+e.model+(e.effort?'@'+e.effort:'')).join(',');
console.log(JSON.stringify({up:ids(editList(L,'up',1)), topUp:ids(editList(L,'up',0)), down:ids(editList(L,'down',0)),
  remove:ids(editList(L,'remove',0)), effort:ids(editList(L,'effort',0,'high')), clear:ids(editList(L,'effort',1,'')),
  add:ids(editList(L,'add',0,' claude:c ')), dup:editList(L,'add',0,'codex:a').error, bad:editList(L,'add',0,'gpt').error,
  untouched:ids({list:L}), efforts:effortChoices(L), plain:effortChoices([L[0]]),
  focus:focusTargets('claude:new'),
  keep:keepFresh({data:{revision:'new'},at:1000},{revision:'old'},2000).revision,
  caught:keepFresh({data:{revision:'new'},at:1000},{revision:'new',x:1},2000).x,
  expired:keepFresh({data:{revision:'new'},at:1000},{revision:'old'},47000).revision,
  none:keepFresh(null,{revision:'old'},0).revision}));"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["up"], "claude:b@max,codex:a")
        self.assertEqual(out["topUp"], "codex:a,claude:b@max")
        self.assertEqual(out["down"], "claude:b@max,codex:a")
        self.assertEqual(out["remove"], "claude:b@max")
        self.assertEqual(out["effort"], "codex:a@high,claude:b@max")
        self.assertEqual(out["clear"], "codex:a,claude:b")
        self.assertEqual(out["add"], "codex:a,claude:b@max,claude:c")
        self.assertIn("already", out["dup"])
        self.assertIn("provider:model", out["bad"])
        self.assertEqual(out["untouched"], "codex:a,claude:b@max")
        # The four offered values, plus any other value already in the list (here max), kept in place.
        self.assertEqual(out["efforts"], ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual(out["plain"], ["low", "medium", "high", "xhigh"])
        # After Set temporary saves, that button is gone: focus falls back to Edit temporary, then Edit permanent.
        self.assertEqual(out["focus"], ["claude:new", "claude:timed", "claude:permanent"])
        # A poll that lands before the snapshot rebuilds keeps the table the save returned (stale snapshot),
        # takes the snapshot once it shows the same revision, and gives up after 45 seconds.
        self.assertEqual((out["keep"], out["caught"], out["expired"], out["none"]), ("new", 1, "old", "old"))

    def test_activity_tables_say_they_count_review_runs_only(self):
        page = self.request("/?t=test-token")[1]
        for piece in ("<h3>Review runs</h3>", "'Review runs by project'", "'Review runs by model'",
                      "Writing sessions (spec, plan, build) are not counted here"):
            self.assertIn(piece, page)
        for old in ("Reviewer activity", "'By project'", "'By model'"):
            self.assertNotIn(old, page)

    def test_page_shows_session_health(self):
        page = self.request("/?t=test-token")[1]
        # Options appears only when there is an owner action to pick, never just for health.
        self.assertNotIn("||row.health_inbox)&&row.repo_path", page)
        for piece in ("row.health", "health_inbox", "session_hints", 'id="session-hints"', "d.unpushed",
                      "health-attention", "data.notes", "newest commit on origin", "withHealth()"):
            self.assertIn(piece, page)

    def test_ports_fallback_and_exhaustion(self):
        other = server.bind(self.port, "t", self.cache)
        self.addCleanup(other.server_close)
        self.assertGreater(other.server_address[1], self.port)
        with mock.patch("relaylib.ui.server.ThreadingHTTPServer", side_effect=OSError("busy")):
            with self.assertRaisesRegex(RelayError, "8765.*8775"):
                server.bind(8765, "t", self.cache)

    def test_listen_address_is_an_ipv4_address_and_reachable_on_loopback(self):
        # Only these two: the link and the running check reach the server at 127.0.0.1.
        for host in ("example.com", "::1", "", "127.0.0.1:80", "127.0.0.2", "192.168.1.5"):
            with self.subTest(host=host), self.assertRaisesRegex(RelayError, "--host"):
                server.validate_host(host)
        server.validate_host("0.0.0.0")
        wide = server.bind(0, "test-token", self.cache, host="0.0.0.0")
        self.addCleanup(wide.server_close)
        threading.Thread(target=wide.serve_forever, daemon=True).start()
        self.addCleanup(wide.shutdown)
        self.assertEqual(wide.server_address[0], "0.0.0.0")
        conn = http.client.HTTPConnection("127.0.0.1", wide.server_address[1], timeout=5)
        conn.request("GET", "/api/snapshot", headers={"X-Relay-Token": "test-token"})
        self.assertEqual(conn.getresponse().status, 200)        # Host and token checks are unchanged
        conn.close()

    def test_link_is_printed_when_no_browser_opens(self):
        info = {"port": 8765, "token": "tok", "pid": 1}
        with mock.patch("webbrowser.open", return_value=False), mock.patch("builtins.print") as out:
            server.open_page(info)
        out.assert_called_once_with("relay: dashboard at http://127.0.0.1:8765/?t=tok", flush=True)
        with mock.patch("webbrowser.open", return_value=True), mock.patch("builtins.print") as out:
            server.open_page(info)
        out.assert_not_called()

    def test_background_launch_passes_the_listen_address_to_the_child(self):
        with mock.patch.object(server, "running_info", side_effect=[None, {"port": 1, "token": "t", "pid": 1}]), \
                mock.patch("subprocess.Popen") as popen, mock.patch.object(server, "open_page"), \
                mock.patch("relaylib.identity.require_owner_terminal"):
            server.launch(8765, background=True, host="0.0.0.0")
        argv = popen.call_args[0][0]
        self.assertEqual(argv[argv.index("--host") + 1], "0.0.0.0")
        self.assertEqual(commands.build_parser().parse_args(["ui"]).host, "127.0.0.1")

    def test_owner_only_and_port_validation(self):
        for marker in identity.AGENT_MARKERS:
            with mock.patch.dict(os.environ, {marker: "agent"}):
                with self.assertRaisesRegex(RelayError, "owner-only"):
                    server.launch(8765)
        for port in (1023, 65526):
            with self.assertRaises(RelayError):
                server.validate_port(port)

    def test_simultaneous_start_stale_file_permissions_and_cleanup(self):
        with open(os.path.join(self.tmp, "ui.json"), "w") as f:
            json.dump({"pid": 99999999, "port": 1234, "token": "stale"}, f)
        self.assertIsNone(server.running_info())
        runtimes = []
        def start():
            with server.discovery_lock():
                existing = server.running_info()
                if existing:
                    return existing
                runtime = server.Runtime(0, cache=snapshot.Cache(builder=lambda: {"rows": []}))
                runtimes.append(runtime)
                runtime.start()
                return runtime.info
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: start(), range(2)))
            self.assertEqual(len(runtimes), 1)
            self.assertEqual(results[0], results[1])
            self.assertEqual(os.stat(os.path.join(self.tmp, "ui.json")).st_mode & 0o777, 0o600)
        finally:
            for runtime in runtimes:
                runtime.close()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ui.json")))

    def test_two_background_launches_publish_one_server_and_reuse_it(self):
        env = {k: v for k, v in os.environ.items() if k not in identity.AGENT_MARKERS}
        env.update(RELAY_ROOT=self.tmp, CODEX_HOME=os.path.join(self.tmp, "codex"))
        script = ("from unittest.mock import patch; from relaylib.ui.server import launch; "
                  f"\nwith patch('webbrowser.open'): launch({self.port}, background=True)")
        processes = [subprocess.Popen([sys.executable, "-c", script], env=env, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for process in processes:
                out, err = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, out + err)
            info = server.running_info()
            self.assertIsNotNone(info)
            self.assertNotEqual(info["pid"], os.getpid())
            with mock.patch.dict(os.environ, env, clear=True), mock.patch("webbrowser.open") as browser:
                server.launch(self.port)
                browser.assert_called_once_with(server.page_url(info))
            self.assertEqual(server.running_info(), info)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            info = server.running_info()
            if info and info["pid"] != os.getpid():
                os.kill(info["pid"], signal.SIGTERM)
                until = time.monotonic() + 4
                while os.path.exists(os.path.join(self.tmp, "ui.json")) and time.monotonic() < until:
                    time.sleep(.05)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ui.json")))
