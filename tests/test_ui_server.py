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
        # Inbox cards offer each ask's own buttons (waiting-visibility D7); Release stays in the feature detail.
        self.assertIn("actions(row,new Set(cardButtons(row)))", page)
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

    def test_session_endpoint_answers_only_for_listed_sessions(self):
        entry = {"provider": "claude", "session_id": "S1", "label": "proteindiary", "folder": "/opt/pd",
                 "state": "waiting", "since": 1.0, "pending_tools": [], "excerpt": None}
        data = {"rows": [], "features": [], "usage": {"providers": {}}, "other_sessions": [entry]}
        words = {"source": "agent", "text": "I still need the publisher JSON key path."}
        with mock.patch.object(self.cache, "get", return_value={"data": data}), \
                mock.patch("relaylib.agentask.last_words", return_value=words) as read:
            code, text = self.request("/api/session?provider=claude&session=S1")
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(text), {"provider": "claude", "session_id": "S1", "label": "proteindiary",
                                                "state": "waiting", "pending_tools": [], "source": "agent",
                                                "text": words["text"], "resume": "claude --resume S1",
                                                "not_running": False, "notes": []})
            read.reset_mock()
            self.assertEqual(self.request("/api/session?provider=claude&session=S2")[0], 404)
            self.assertEqual(self.request("/api/session?provider=codex&session=S1")[0], 404)
            read.assert_not_called()                                   # never reads a transcript it did not list
            self.assertEqual(self.request("/api/session?provider=claude")[0], 400)
            self.assertEqual(self.request("/api/session?provider=claude&session=S1", token=None)[0], 403)
        with mock.patch.object(self.cache, "get", return_value={"data": data}), \
                mock.patch("relaylib.agentask.last_words", return_value=None):
            gone = json.loads(self.request("/api/session?provider=claude&session=S1")[1])
            self.assertEqual((gone["text"], gone["source"]), (None, None))

    def test_session_endpoint_answers_for_left_off_sessions(self):
        home = os.path.realpath(os.path.join(self.tmp, "user"))
        app = os.path.join(home, "app")
        os.makedirs(app)
        entry = {"provider": "codex", "session_id": "C1", "folder": app, "checkout": None, "state": "ended",
                 "since": 1.0, "at": 2.0, "pending_tools": [], "excerpt": None, "feature": None,
                 "resume": "cd ~/app && codex resume C1"}
        data = {"rows": [], "features": [], "usage": {"providers": {}}, "other_sessions": [],
                "left_off": [{"project": "~/app", "last_active": 2.0, "more": 0, "sessions": [entry]}]}
        words = {"source": "agent", "text": "Done; the PR is up."}
        with mock.patch.object(self.cache, "get", return_value={"data": data}), \
                mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch("relaylib.agentask.last_words", return_value=words) as read:
            code, text = self.request("/api/session?provider=codex&session=C1")
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(text), {"provider": "codex", "session_id": "C1", "label": "~/app",
                                                "state": "ended", "pending_tools": [], "source": "agent",
                                                "text": words["text"], "resume": "cd ~/app && codex resume C1",
                                                "not_running": True, "notes": []})
            read.reset_mock()
            self.assertEqual(self.request("/api/session?provider=codex&session=C2")[0], 404)
            read.assert_not_called()

    def note_data(self, state="waiting", shown=None):
        entry = {"provider": "claude", "session_id": "S1", "label": "app", "folder": None, "state": state,
                 "since": 1.0, "pending_tools": [], "excerpt": None}
        if shown:
            entry["shown_state"] = shown
        return {"rows": [], "features": [], "usage": {"providers": {}}, "other_sessions": [entry]}

    def record_inbox(self, inbox):
        from relaylib import sessions
        record = sessions.apply(None, "claude", {"session_id": "S1", "hook_event_name": "Stop", "cwd": "/w"}, 1.0,
                                inbox)
        os.makedirs(sessions.folder(), exist_ok=True)
        with open(sessions.record_path("claude", "S1"), "w") as f:
            json.dump(record, f)

    def note(self, payload, path="/api/note", **kw):
        code, text = self.request(path, "POST", payload, **kw)
        return code, json.loads(text)

    def test_notes_are_queued_posted_and_removed_only_for_listed_sessions(self):
        from tests.test_notes import FakeInbox
        sock_dir = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(sock_dir.cleanup)
        fake = FakeInbox(sock_dir.name)
        self.addCleanup(fake.close)
        send = lambda text, sid="S1": self.note({"provider": "claude", "session_id": sid, "text": text})
        with mock.patch.object(self.cache, "get", return_value={"data": self.note_data()}), \
                mock.patch.object(self.cache, "refresh") as refresh:
            code, body = send("first")                                           # no inbox recorded: queued
            self.assertEqual((code, body["note"]["status"], body["message"]), (200, "queued", None))
            refresh.assert_called()
            self.record_inbox(fake.path)
            code, body = send("second")
            self.assertEqual((code, body["note"]["status"]), (200, "posted"))
            self.assertEqual([n["text"] for n in body["notes"]], ["second", "first"])
            self.assertEqual(len(fake.wait()), 1)
            queued, posted = body["notes"][1]["id"], body["notes"][0]["id"]
            remove = lambda nid, sid="S1": self.note({"provider": "claude", "session_id": sid, "id": nid},
                                                     "/api/note/remove")
            code, body = remove(posted)
            self.assertEqual((code, [n["status"] for n in body["notes"]]), (409, ["posted", "queued"]))
            self.assertEqual(remove("missing")[0], 409)
            code, body = remove(queued)
            self.assertEqual((code, [n["text"] for n in body["notes"]]), (200, ["second"]))
            self.assertEqual(send("hi", "S2")[0], 404)
            self.assertEqual(remove(queued, "S2")[0], 404)
            for bad in ({"provider": "claude", "session_id": "S1", "text": "  "},
                        {"provider": "claude", "session_id": "S1", "text": "x" * 2001},
                        {"provider": "claude", "session_id": "S1", "text": 5},
                        {"provider": "claude", "session_id": 5, "text": "hi"},
                        {"session_id": "S1", "text": "hi"}):
                self.assertEqual(self.note(bad)[0], 400, bad)
            for bad in ({"provider": "claude", "session_id": "S1"}, {"provider": "claude", "session_id": "S1", "id": 3},
                        {"provider": "claude", "id": "x"}):
                self.assertEqual(self.note(bad, "/api/note/remove")[0], 400, bad)
            self.assertEqual(self.request("/api/note", "POST", {"provider": "claude", "session_id": "S1",
                                                                "text": "hi"}, token=None)[0], 403)
            self.assertEqual(self.request("/api/note", "POST", {"provider": "claude", "session_id": "S1",
                                                                "text": "hi"}, host="evil.test")[0], 403)
        for state, shown in (("ended", None), ("working", "stopped")):          # not running: queued, never posted
            with mock.patch.object(self.cache, "get", return_value={"data": self.note_data(state, shown)}):
                self.assertEqual(send("later")[1]["note"]["status"], "queued")
        self.assertEqual(len(fake.got), 1)

    def test_a_note_that_cannot_be_stored_is_not_posted(self):
        from relaylib import notes, sessions
        from tests.test_notes import FakeInbox
        sock_dir = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(sock_dir.cleanup)
        fake = FakeInbox(sock_dir.name)
        self.addCleanup(fake.close)
        self.record_inbox(fake.path)
        payload = {"provider": "claude", "session_id": "S1", "text": "hi"}
        with mock.patch.object(self.cache, "get", return_value={"data": self.note_data()}):
            os.makedirs(notes.folder())
            fd = sessions._lock(os.path.join(notes.folder(), ".lock"), 1)
            try:
                with mock.patch.object(notes, "SEND_WAIT_S", 0.2):
                    code, body = self.note(payload)
            finally:
                os.close(fd)
            self.assertEqual(code, 400)
            self.assertIn("busy", body["error"])
            os.chmod(notes.folder(), 0o500)
            try:
                self.assertEqual(self.note(payload)[0], 400)
            finally:
                os.chmod(notes.folder(), 0o700)
            self.assertEqual(fake.got, [])
            with mock.patch.object(notes.os, "replace", side_effect=OSError("rename failed")):
                code, body = self.note(payload)                                   # F5: posted, not recorded
            self.assertEqual((code, body["note"]["status"], body["message"]), (200, "posted", notes.NOT_RECORDED))

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
            prepare.assert_called_once_with("/fixture", "demo", payload["seen"], "codex:gpt-6-astra", stage="build")
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
            prepare.side_effect, prepare.return_value = None, job                   # a spec re-review (stage-rereview)
            code, text = self.request("/api/action", "POST", dict(payload, stage="spec"))
            self.assertEqual(code, 202, text)
            self.assertEqual(prepare.call_args.kwargs, {"stage": "spec"})
            self.assertIn("is reviewing demo's spec", json.loads(text)["message"])
            code, text = self.request("/api/action", "POST", dict(payload, stage=7))
            self.assertEqual((code, json.loads(text)["error"]), (400, "stage must be spec, plan or build"))

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

    def test_page_shows_other_sessions(self):
        page = self.request("/?t=test-token")[1]
        for piece in ('<dialog id="session-dialog"', "claude:'Claude'", "codex:'Codex'", "' session'",
                      "'Waiting for approval: '", "'Resume with: '", "'No last message to show.'",
                      "'This session is no longer waiting.'", "data.other_sessions", "waitingCount(rows,others)",
                      "document.title=count?"):
            self.assertIn(piece, page)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_other_session_rules_run_in_node(self):
        page = self.request("/?t=test-token")[1]
        funcs = re.search(r"function ageText\(.*?(?=function cardFlags\()", page, re.S).group(0)
        script = """
const made=[];
class El{constructor(tag){this.tag=tag;this.children=[];this.className='';this.textContent='';}
  append(...xs){this.children.push(...xs);} querySelectorAll(){return [];}}
const document={createElement:t=>{const e=new El(t);made.push(e);return e;}};
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=text;if(cls)n.className=cls;return n;};
let calls=[];const notice=(t,err)=>calls.push(['notice',t,!!err]),load=async()=>calls.push(['load']);
let fail=null;const api=async()=>{if(fail)throw fail;return {};};const $=()=>({});
""" + funcs + """
(async()=>{
const now=1000000,feature={waiting_on_owner:true,wait_since:now-600,feature:'nba'};
const waitingOn={provider:'claude',session_id:'S1',label:'proteindiary',state:'waiting',since:now-7200,pending_tools:[]};
const approve={provider:'codex',session_id:'C1',label:'~/x',state:'permission',since:now-300,pending_tools:['Bash']};
const card=sessionCard(waitingOn);
fail=Object.assign(new Error('gone'),{status:404});await openSession(waitingOn);
const after404=calls;calls=[];fail=Object.assign(new Error('Failed to fetch'),{});await openSession(waitingOn);
console.log(JSON.stringify({
  waitingLine:sessionLine(waitingOn,now), approveLine:sessionLine(approve,now),
  order:inboxOrder([feature,approve,waitingOn]).map(x=>x.feature||x.session_id),
  counts:[waitingCount([feature],[]),waitingCount([{waiting_on_owner:false}],[waitingOn]),waitingCount([feature],[waitingOn,approve]),waitingCount([],[])],
  noText:sessionDialog(waitingOn,{state:'waiting',text:null,resume:'claude --resume S1'}),
  tools:sessionDialog(approve,{state:'permission',pending_tools:['Bash'],text:'x',resume:'codex resume C1'}).tools,
  buttons:made.filter(e=>e.tag==='button').length, cardKids:card.children.map(c=>c.className),
  after404, offline:calls}));
})();"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["waitingLine"], "Waiting on you · 2h")
        self.assertEqual(out["approveLine"], "Waiting for approval: Bash · 5m")
        self.assertEqual(out["order"], ["C1", "nba", "S1"])                     # one list, newest wait first
        self.assertEqual(out["counts"], [1, 1, 3, 0])
        self.assertEqual(out["noText"], {"title": "Claude session · proteindiary", "tools": "",
                                         "text": "No last message to show.", "resume": "Resume with: claude --resume S1"})
        self.assertEqual(out["tools"], "Waiting for approval: Bash")
        self.assertEqual(out["buttons"], 0)                                     # session cards act on nothing
        self.assertEqual(out["cardKids"], ["t", "reason"])
        self.assertEqual(out["after404"], [["notice", "This session is no longer waiting.", False], ["load"]])
        self.assertEqual(out["offline"], [["notice", "Failed to fetch", True]])

    def test_page_shows_where_you_left_off(self):
        page = self.request("/?t=test-token")[1]
        for piece in ('<section id="left-off"', "Where you left off", "'No sessions to show yet.'",
                      "'This session is no longer listed.'", "' more: run relay left --all'", "data.left_off",
                      "renderLeftOff();"):
            self.assertIn(piece, page)
        self.assertLess(page.index('id="inbox"'), page.index('id="left-off"'))
        self.assertLess(page.index('id="left-off"'), page.index('id="features"'))

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_left_off_rules_run_in_node(self):
        page = self.request("/?t=test-token")[1]
        funcs = (re.search(r"function ageText\(.*?(?=function cardFlags\()", page, re.S).group(0)
                 + re.search(r"const LEFT_STATE.*?(?=function renderRows\()", page, re.S).group(0))
        script = """
const made=[];
class El{constructor(tag){this.tag=tag;this.children=[];this.className='';this.textContent='';}
  append(...xs){this.children.push(...xs);} replaceChildren(...xs){this.children=xs;} querySelectorAll(){return [];}}
const document={createElement:t=>{const e=new El(t);made.push(e);return e;}};
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=text;if(cls)n.className=cls;return n;};
let calls=[];const notice=(t,err)=>calls.push(['notice',t,!!err]),load=async()=>calls.push(['load']);
let fail=null;const api=async()=>{if(fail)throw fail;return {};};const list=new El('div');const $=()=>list;
let data={left_off:[]};
""" + funcs + """
(async()=>{
const now=1000000;
const e=(state,ago,extra)=>Object.assign({provider:'claude',session_id:'S'+state,state,at:now-ago,since:now-ago,
  pending_tools:[],checkout:null,feature:null,excerpt:null},extra);
const lines=[e('waiting',7200),e('permission',300,{pending_tools:['Bash']}),e('ended',3*3600,{checkout:'relay-go-dev'}),
  e('working',86400,{feature:{slug:'x',done:true}})].map(x=>leftLine(x,now));
renderLeftOff();const empty=list.children.map(c=>c.textContent);
data={left_off:[{project:'relay-go',last_active:Date.now()/1000-60,more:2,sessions:[e('ended',60)]}]};
renderLeftOff();const group=list.children[0];
const card=group.children[1].children[0];
fail=Object.assign(new Error('gone'),{status:404});await card.onclick();
console.log(JSON.stringify({lines,empty,heading:group.children[0].textContent,more:group.children[2].textContent,
  buttons:made.filter(x=>x.tag==='button').length,cardKids:card.children.map(c=>c.className),after404:calls}));
})();"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["lines"], ["Waiting on you · 2h ago", "Needs approval: Bash · 5m ago",
                                        "Ended · 3h ago · relay-go-dev", "Was working · 1d ago · feature x (done)"])
        self.assertEqual(out["empty"], ["No sessions to show yet."])
        self.assertEqual(out["heading"], "relay-go · last active 1m ago")
        self.assertEqual(out["more"], "Up to 2 more: run relay left --all")
        self.assertEqual(out["buttons"], 0)                                     # left-off cards act on nothing
        self.assertEqual(out["cardKids"], ["t", "reason"])
        self.assertEqual(out["after404"], [["notice", "This session is no longer listed.", False], ["load"]])

    def test_page_shows_running_now_and_sorting(self):
        page = self.request("/?t=test-token")[1]
        for piece in ("<h2>Running now</h2>", "'Nothing running.'", ">By project<", ">Most recent<",
                      "'Showing the newest 3 per project: run relay left --all for every session.'",
                      "data.running", "renderRunning();", "Stopped"):
            self.assertIn(piece, page)
        order = [page.index(f'id="{x}"') for x in ("running", "inbox", "left-off", "features")]
        self.assertEqual(order, sorted(order))

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_note_box_runs_in_node(self):
        page = self.request("/?t=test-token")[1]
        for text in ("Send a note", "sd-note-text", "sd-note-send"):
            self.assertIn(text, page)
        funcs = re.search(r"function ageText\(.*?(?=function cardFlags\()", page, re.S).group(0)
        script = """
class El{constructor(tag){this.tag=tag;this.children=[];this.className='';this.textContent='';this.value='';this.hidden=false;}
  append(...xs){this.children.push(...xs);} replaceChildren(...xs){this.children=xs;} showModal(){this.open=true;}}
const document={createElement:t=>new El(t)};
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=text;if(cls)n.className=cls;return n;};
const els={};const $=id=>els[id]||(els[id]=new El(id));
const notice=()=>{},load=async()=>{};let reply=null,sent=[];
const api=async(path,body)=>{sent.push([path,body]);if(reply instanceof Error)throw reply;return reply;};
""" + funcs + """
(async()=>{
const now=Date.now()/1000;const claude={provider:'claude',session_id:'S1'},codex={provider:'codex',session_id:'C1'};
const n=(status,extra)=>Object.assign({id:status,text:'hi '+status,at:now-120,status,status_at:now-120},extra);
const words={queuedClaude:noteWords(n('queued'),'claude',now),queuedCodex:noteWords(n('queued'),'codex',now),
  delivered:noteWords(n('delivered'),'claude',now),posted:noteWords(n('posted'),'claude',now)};
const hints=[noteHint({not_running:true}),noteHint({not_running:false})];
renderNotes(claude,[n('queued'),n('delivered'),n('posted')]);
const removable=$('sd-note-list').children.map(li=>li.children.filter(c=>c.tag==='button').length);
reply={provider:'claude',session_id:'S1',label:'app',state:'ended',pending_tools:[],text:'x',source:'agent',resume:'r',
  not_running:true,notes:[n('queued')]};
await openSession(claude);const shown=[$('sd-note-hint').textContent,$('sd-note-hint').hidden,$('sd-note-list').children.length];
$('sd-note-text').value='keep me';reply=Object.assign(new Error('notes are busy right now; try again'),{status:400});
await sendNote(claude);const failed=[$('sd-note-text').value,$('sd-note-result').textContent];
reply={note:n('posted'),message:'Posted to the session, but relay could not record it.',notes:[]};
await sendNote(claude);const f5=[$('sd-note-text').value,$('sd-note-result').textContent];
reply={note:n('queued'),message:null,notes:[n('queued')]};$('sd-note-text').value='later';
await sendNote(codex);const queued=$('sd-note-result').textContent;
reply=Object.assign(new Error('That note was already handed to the session or is gone.'),{status:409,notes:[n('delivered')]});
await removeNote(claude,'queued');const redraw=[$('sd-note-result').textContent,$('sd-note-list').children.length,
  $('sd-note-list').children[0].children.filter(c=>c.tag==='button').length];
console.log(JSON.stringify({words,hints,removable,shown,failed,f5,queued,redraw,sent:sent.map(x=>x[0])}));
})();"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["words"]["queuedClaude"], "Queued. It reaches the session with its next prompt or tool call.")
        self.assertEqual(out["words"]["queuedCodex"], "Queued. It reaches the session with your next prompt there.")
        self.assertEqual(out["words"]["delivered"], "Delivered to the session 2m ago.")
        self.assertTrue(out["words"]["posted"].startswith("Posted to the session's inbox 2m ago. Claude Code delivers"))
        self.assertIn("relay cannot see which happened.", out["words"]["posted"])
        self.assertEqual(out["hints"], ["This session is not running. The note waits until you resume it.", ""])
        self.assertEqual(out["removable"], [1, 0, 0])                           # Remove only on queued notes
        self.assertEqual(out["shown"], ["This session is not running. The note waits until you resume it.", False, 1])
        self.assertEqual(out["failed"], ["keep me", "notes are busy right now; try again"])
        self.assertEqual(out["f5"], ["", "Posted to the session, but relay could not record it."])
        self.assertEqual(out["queued"], "Queued. It reaches the session with your next prompt there.")
        self.assertEqual(out["redraw"], ["That note was already handed to the session or is gone.", 1, 0])
        self.assertEqual(out["sent"], ["/api/session?provider=claude&session=S1", "/api/note", "/api/note",
                                       "/api/note", "/api/note/remove"])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_running_and_sort_rules_run_in_node(self):
        page = self.request("/?t=test-token")[1]
        funcs = (re.search(r"function ageText\(.*?(?=function cardFlags\()", page, re.S).group(0)
                 + re.search(r"const LEFT_STATE.*?(?=function renderRows\()", page, re.S).group(0))
        script = """
const made=[];
class El{constructor(tag){this.tag=tag;this.children=[];this.className='';this.textContent='';}
  append(...xs){this.children.push(...xs);} replaceChildren(...xs){this.children=xs;} querySelectorAll(){return [];}}
const document={createElement:t=>{const e=new El(t);made.push(e);return e;}};
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=text;if(cls)n.className=cls;return n;};
let calls=[];const notice=(t,err)=>calls.push(['notice',t,!!err]),load=async()=>calls.push(['load']);
let fail=null;const api=async()=>{if(fail)throw fail;return {};};const list=new El('div');const $=()=>list;
let store={},broken=false;
globalThis.localStorage={getItem:k=>{if(broken)throw new Error('denied');return store[k]??null;},
  setItem:(k,v)=>{if(broken)throw new Error('denied');store[k]=v;}};
let data={running:[],left_off:[]};
""" + funcs + """
(async()=>{
const now=1000000,t=Date.now()/1000;
const run={provider:'codex',session_id:'R1',project:'relay-go',since:now-1500,at:now-10,checkout:'relay-go-dev',
  feature:{slug:'x',done:false},excerpt:null,state:'working',pending_tools:[]};
renderRunning();const none=list.children.map(c=>c.textContent);
data={running:[run],left_off:[]};renderRunning();const card=list.children[0];
fail=Object.assign(new Error('gone'),{status:404});await card.onclick();
const s=(id,at,extra)=>Object.assign({provider:'claude',session_id:id,state:'ended',at:t-at,since:t-at,pending_tools:[],
  checkout:null,feature:null,excerpt:null},extra);
data={left_off:[{project:'a',last_active:t-60,more:1,sessions:[s('a1',60),s('a2',5000)]},
                {project:'b',last_active:t-600,more:0,sessions:[s('b1',600,{state:'working',shown_state:'stopped'})]}]};
const before=leftSort();setLeftSort('recent');renderLeftOff();
const recent=list.children[0].children.map(c=>[c.children[0].children[0].textContent,c.children[1].children[0].textContent]);
const note=list.children[1].textContent;
broken=true;const fallback=leftSort();setLeftSort('project');
console.log(JSON.stringify({line:runLine(run,now),none,buttons:made.filter(x=>x.tag==='button').length,
  kids:card.children.map(c=>c.className),after404:calls,before,recent,note,fallback}));
})();"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["line"], "Running 25m · relay-go-dev · feature x")
        self.assertEqual(out["none"], ["Nothing running."])
        self.assertEqual(out["buttons"], 0)                                     # running cards act on nothing
        self.assertEqual(out["kids"], ["t", "reason"])
        self.assertEqual(out["after404"], [["notice", "This session is no longer listed.", False], ["load"]])
        self.assertEqual(out["before"], "project")
        self.assertEqual(out["recent"], [["a", "Ended · 1m ago"], ["b", "Stopped · 10m ago"], ["a", "Ended · 1h ago"]])
        self.assertEqual(out["note"], "Showing the newest 3 per project: run relay left --all for every session.")
        self.assertEqual(out["fallback"], "project")                             # storage blocked: the default

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
        # Usage and review runs come first; the reviewer tables follow them (owner, 2026-10-06).
        self.assertLess(page.index('id="usage"'), page.index('id="reviewers"'))
        self.assertLess(page.index('id="ledger-tables"'), page.index('id="reviewers"'))

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
        for piece in ("<h3>Review runs</h3>", "'Review runs by project'", "'Review runs by model'"):
            self.assertIn(piece, page)
        for old in ("Reviewer activity", "'By project'", "'By model'", "are not counted here"):
            self.assertNotIn(old, page)

    def test_writing_session_tables(self):
        page = self.request("/?t=test-token")[1]
        for piece in ('id="writing-tables"', 'id="writing-notes"', "'Writing sessions by feature'",
                      "'Writing sessions by model'", "'No writing sessions in this period.'", "Unattributed",
                      "Unreadable", "renderWriting()", "activityTable(", "activityTable('features'",
                      "activityTable('wmodels'", "writingShown"):
            self.assertIn(piece, page)
        # After Review runs, before the reviewer tables, on the same period select.
        self.assertLess(page.index('id="ledger-tables"'), page.index('id="writing-tables"'))
        self.assertLess(page.index('id="writing-tables"'), page.index('id="reviewers"'))

    def test_page_shows_session_health(self):
        page = self.request("/?t=test-token")[1]
        for piece in ("row.health", "session_hints", 'id="session-hints"', "d.unpushed",
                      "health-attention", "data.notes", "newest commit on origin"):
            self.assertIn(piece, page)

    def test_inbox_cards_show_asks_words_and_real_buttons(self):
        page = self.request("/?t=test-token")[1]
        for piece in ("ASK_ACTIONS", "row.asks", "row.excerpt", "wait_since", "inboxOrder(waiting.concat(others))", "askLine(row",
                      "cardButtons(row)", "cardFlags(row)", "Agent’s last message", "Summary while you were away", "d.agent_text",
                      "d.pending_tools", "Waiting for approval: "):
            self.assertIn(piece, page)
        self.assertNotIn("'Options'", page)                                  # the button that only opened details
        self.assertIn("node('pre',d.agent_text.text,'agent-text')", page)    # textContent through node(), never HTML
        self.assertNotIn("innerHTML", page)
        self.assertIn("for(const row of rows.filter(r=>$('show-done').checked", page)   # the table keeps row order

    def test_re_review_buttons_live_in_the_details_only(self):
        page = self.request("/?t=test-token")[1]
        for piece in ("'review-spec':'Re-review spec'", "'review-plan':'Re-review plan'", "openReviewRequest(row,reviewStage(action))",
                      "$('rr-feature').textContent=d.feature+' / '+stage", "stage:reviewTargetStage",
                      "(d.review_defaults||{})[stage]", 'id="rr-title"'):
            self.assertIn(piece, page)
        # Never on inbox cards: relay does not suggest a re-review (stage-rereview D7).
        self.assertIn("const ASK_ACTIONS={decide:['go','extra-round','reset-rounds'],'review-failed':['go','review'],merge:['merge']};", page)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_review_stage_runs_in_node(self):
        page = self.request("/?t=test-token")[1]
        funcs = re.search(r"function reviewStage\([^\n]*?\}", page).group(0)
        out = subprocess.run(["node", "-e", funcs + "console.log(JSON.stringify([reviewStage('review-spec'),"
                              "reviewStage('review-plan'),reviewStage('review')]))"],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), ["spec", "plan", "build"])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_card_rules_run_in_node(self):
        page = self.request("/?t=test-token")[1]
        funcs = re.search(r"const ASK_ACTIONS=.*?function cardFlags\(.*?\n", page, re.S).group(0)
        script = funcs + """
const ask=(kind,text,since)=>({kind,text,since});
const merge={asks:[ask('merge','Merge PR #6',100),ask('answer','Answer the claude session',2800)],wait_since:100,
  actions:['merge','review','release']};
const decide={asks:[ask('decide','Decide: build review stopped',50)],wait_since:50,actions:['go','extra-round','reset-rounds','release']};
const failed={asks:[ask('review-failed','Review failed',70)],wait_since:70,actions:['go','review','release']};
const answer={asks:[ask('answer','Answer the claude session',3000)],wait_since:3000,actions:['release']};
const broken={asks:[ask('error','Fix: cannot read',null)],wait_since:null,actions:[]};
console.log(JSON.stringify({merge:cardButtons(merge),decide:cardButtons(decide),failed:cardButtons(failed),
  answer:cardButtons(answer),line:askLine(merge,7300),broken:askLine(broken,7300),
  order:inboxOrder([answer,broken,merge,decide]).map(r=>r.asks[0].kind),labels:[excerptLabel('summary'),excerptLabel('agent')],
  ages:[ageText(59),ageText(61),ageText(7200),ageText(200000)],
  flags:cardFlags({asks:[ask('merge','Merge PR #6 · fallback GO: confirm with codex before merging (codex out; your call)',1),
                         ask('take','Take the handoff: open a session and run relay take',1)],
                   flags:['fallback GO: confirm with codex before merging (codex out; your call)',
                          'handoff: open a session and run `relay take`','local changes not published',
                          'plan review: same provider as the author']})}));"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["merge"], ["merge"])                            # no Request review next to Merge
        self.assertEqual(out["decide"], ["go", "extra-round", "reset-rounds"])
        self.assertEqual(out["failed"], ["go", "review"])
        self.assertEqual(out["answer"], [])                                   # the answer is in the session; no Release
        self.assertEqual(out["line"], "Merge PR #6 · Answer the claude session · waiting 2h")
        self.assertEqual(out["broken"], "Fix: cannot read")
        self.assertEqual(out["order"], ["answer", "merge", "decide", "error"])  # newest wait first, undated last
        self.assertEqual(out["labels"], ["Summary", "Agent"])
        self.assertEqual(out["ages"], ["59s", "1m", "2h", "2d"])
        # Warnings stay on the card (owner, 2026-10-07); only notes the asks already say are left out.
        self.assertEqual(out["flags"], ["local changes not published", "plan review: same provider as the author"])

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
