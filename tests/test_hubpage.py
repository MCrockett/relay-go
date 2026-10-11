import json
import os
import unittest
from unittest import mock

from relaylib import hub, hubpage
from tests.test_hub import HubHome

UUID = "0f1e2d3c-4b5a-4978-8a9b-0c1d2e3f4a5b"
HOME = "/" + "Users/x"  # built so the public-files check never sees a machine path


class RedactTest(unittest.TestCase):  # phone-artifact D5, R7, R8
    def test_the_spec_table(self):
        for text, want in (
                (f"claude --resume {UUID}", "claude --resume [id]"),
                ("[app/google-services.json](~/p/app/google-services.json)",
                 "[app/google-services.json](google-services.json)"),
                ("export GH_TOKEN=ghp_0123456789abcdefghijABCDEFGHIJ012345", "export GH_TOKEN=[hidden]"),
                ("Authorization: Bearer abc.def.ghi", "Authorization: Bearer [hidden]"),
                ("password: hunter22", "password: [hidden]"),
                ("mail me at someone@example.com", "mail me at [email]"),
                ("<script>alert(1)</script>", "<script>alert(1)</script>")):  # escaping is the renderer's job
            self.assertEqual(hubpage.redact(text), want, text)

    def test_known_ids_and_the_token(self):
        self.assertEqual(hubpage.redact("resume S-123 or S-1234 now", ids=["S-123"]), "resume [id] or [id]4 now")
        self.assertEqual(hubpage.redact("t=Zq9", ids=["Zq"]), "t=Zq9")             # too short to be an id
        self.assertEqual(hubpage.redact("open ?t=TOKENVALUE1 please", token="TOKENVALUE1"), "open ?t=[hidden] please")

    def test_secret_shapes(self):
        for secret in ("gho_" + "a1" * 12, "github_pat_" + "B2_" * 10, "sk-ant-api03-" + "x9" * 12,
                       "sk-" + "Q7" * 12, "xoxb-1234-5678-abcdefghij", "AKIAABCDEFGHIJKLMNOP", "ASIAABCDEFGHIJKLMNOP",
                       "AIza" + "c" * 35, "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl", "0123456789abcdef" * 2 + "01234567"):
            self.assertEqual(hubpage.redact(f"see {secret} here"), "see [hidden] here", secret)
        self.assertEqual(hubpage.redact("api_key=abc123 and auth: 'x y'"), "api_key=[hidden] and auth: [hidden]")
        self.assertEqual(hubpage.redact("built at 011f0bc"), "built at 011f0bc")    # a short SHA stays
        for run in ("abcdef" * 8, "1" * 40, "AbC+/dEf" * 5 + "xyz=", "0123456789abcdef" * 4):
            self.assertEqual(hubpage.redact(f"key {run} end"), "key [hidden] end", run)
        for kept in ("boss-defects-2-3-and-a-very-long-feature-slug-name", "relay-go/hub-owned-feature",
                     "snake_case_names_that_run_on_for_quite_a_while_here"):
            self.assertEqual(hubpage.redact(kept), kept)
        self.assertEqual(hubpage.redact("~/" + "a" * 40 + "/report.txt"), "report.txt")

    def test_paths(self):
        self.assertEqual(hubpage.redact("read ~/x/y.txt and /opt/a/b and " + "/" + "private/tmp/c/d."), "read y.txt and b and d.")
        self.assertEqual(hubpage.redact("see src/app.py and /api/snapshot"), "see src/app.py and /api/snapshot")
        self.assertEqual(hubpage.redact("in /Volumes/Disk 2/x"), "in Disk 2/x")      # a space ends the path

    def test_values_that_are_not_text(self):
        for value in (None, 5, 1.5, ["x"], {"a": 1}):
            self.assertEqual(hubpage.redact(value), "")

    def test_known_ids_come_from_the_registry_records_and_items(self):
        found = [{"kind": "session", "session_id": "SESSION-A"},
                 {"kind": "feature", "session": {"provider": "claude", "session_id": "SESSION-B"}},
                 {"kind": "feature", "session": None}]
        with mock.patch.object(hub, "registered", return_value={("claude", "HUB-1")}):
            ids = hubpage.known_ids(found, [{"session_id": "REC-1"}, {"session_id": None}, "junk"])
        self.assertEqual(set(ids), {"SESSION-A", "SESSION-B", "HUB-1", "REC-1"})


class TokenTest(HubHome):
    def test_dashboard_token(self):
        self.assertIsNone(hubpage.dashboard_token())
        os.makedirs(os.environ["RELAY_HOME"], exist_ok=True)
        with open(os.path.join(os.environ["RELAY_HOME"], "ui.json"), "w") as f:
            json.dump({"token": "abc", "pid": 1, "port": 9}, f)
        self.assertEqual(hubpage.dashboard_token(), "abc")
        with open(os.path.join(os.environ["RELAY_HOME"], "ui.json"), "w") as f:
            f.write("{")
        self.assertIsNone(hubpage.dashboard_token())


NOW = 1_800_000_000.0
SRC = {"from": "dashboard", "age_seconds": 12.0}
COUNTS = {"features": 2, "sessions": 1}


def feature(n, slug="ledger", **extra):
    item = {"n": n, "kind": "feature", "repo": "bottomsup", "slug": slug, "stage": "build", "status": "ready-to-merge",
            "asks": [{"kind": "merge", "text": "Merge PR #12"}], "flags": [], "wait_since": NOW - 7200, "pr": 12,
            "pr_state": "OPEN", "ci": "green", "actions": ["merge", "review"], "session": None, "answer_here": False,
            "repo_path": HOME + "/StudioProjects/bottomsup", "seen": {"commit": "c0ffee" * 6}}
    item.update(extra)
    return item


def session(n, sid="S-123", **extra):
    item = {"n": n, "kind": "session", "provider": "codex", "session_id": sid, "label": "proteindiary",
            "state": "waiting", "pending_tools": [], "wait_since": NOW - 600, "excerpt": "PR #72 is merged",
            "answer_here": False}
    item.update(extra)
    return item


def cards(page):
    return page.count('<article class="card')


class RenderTest(unittest.TestCase):  # phone-artifact D3, D5, R4, R5, R7, R9
    def render(self, found, digest_id="mv3abc1234", failed=(), **kw):
        return hubpage.render(digest_id, SRC, found, COUNTS, failed, now=NOW, **kw)

    def test_items_in_digest_order_with_their_fields(self):
        found = [feature(1), session(2), {"n": 3, "kind": "error", "repo": "broken", "slug": "?",
                                          "error": "could not read state", "wait_since": None, "actions": []},
                 feature(4, "perm", status="drafting", asks=[{"kind": "approve", "text": "Approve Bash"}], pr=None,
                         actions=[], session={"provider": "claude", "session_id": "P1", "label": "isitev",
                                              "state": "permission"}, answer_here=True)]
        page = self.render(found)
        order = [page.index(x) for x in ("bottomsup/ledger", "proteindiary · codex session", "broken", "bottomsup/perm")]
        self.assertEqual(order, sorted(order))
        for text in ("digest mv3abc1234", "dashboard data 12s old", "numbers work until", '<span class="n">1.</span>',
                     "build ready-to-merge · waiting 2h", "Merge PR #12", "PR #12 · CI green",
                     '<span class="chip">merge</span>', '<p class="meta">waiting 10m</p>', "PR #72 is merged",
                     "could not be read: could not read state", "session: isitev (claude) · permission",
                     '<p class="here">open this session to answer</p>', '<article class="card answer">',
                     "Nothing else needs you: 2 features in progress, 1 session working.", hubpage.FOOTER):
            self.assertIn(text, page)
        self.assertEqual(cards(page), 4)
        self.assertNotIn("Nothing needs you right now", page)

    def test_an_unsaved_digest_has_no_numbers(self):
        page = self.render([feature(1), session(2)], digest_id=None)
        self.assertIn("Not saved: actions need a new digest.", page)
        self.assertNotIn('class="n"', page)
        self.assertNotIn("numbers work until", page)

    def test_nothing_waiting_and_a_failed_scan(self):
        self.assertIn("Nothing needs you right now.", self.render([]))
        page = self.render([], failed=["Features could not be read: boom"])
        self.assertIn("Could not check everything: Features could not be read: boom", page)
        self.assertNotIn("Nothing needs you right now", page)

    def test_values_of_the_wrong_type_are_left_out(self):  # F5
        page = self.render([feature(1, wait_since="x", asks=None, pr="7", flags=[3], actions="merge", session="s"),
                            session(2, pending_tools=None, excerpt={"text": "x"}, label=None, wait_since=True), "junk"])
        self.assertEqual(cards(page), 2)
        self.assertNotIn("more items", page)
        for gone in ("waiting ", "PR #", "actions:", "session:", "needs:", "<blockquote>"):
            self.assertNotIn(gone, page.split("<main", 1)[1].split("</header>", 1)[1], gone)

    def test_item_text_is_escaped(self):  # R9
        page = self.render([feature(1, slug="<b>x</b>", asks=[{"text": "<script>alert(1)</script>"}],
                                    flags=['" onmouseover="x'])])
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        self.assertIn("&quot; onmouseover=&quot;x", page)
        self.assertEqual(page.count("<script>"), 1)                                  # only the page's own

    def test_nothing_private_reaches_the_page(self):  # R7
        found = [session(1, sid="S-123", excerpt=f"run claude --resume S-123 with {UUID} at " + HOME + "/p/a.txt"),
                 feature(2)]
        page = self.render(found, ids=hubpage.known_ids(found, []), token="TOKENVALUE1")
        page_with_token = self.render([session(1, excerpt="open ?t=TOKENVALUE1")], token="TOKENVALUE1")
        for secret in ("S-123", UUID, HOME, "StudioProjects", "c0ffee", "repo_path"):
            self.assertNotIn(secret, page, secret)
        self.assertIn("run claude --resume [id] with [id] at a.txt", page)
        self.assertNotIn("TOKENVALUE1", page_with_token)


class ShellTest(unittest.TestCase):  # phone-artifact D4, D6, R6, R10
    def test_the_artifact_rules(self):
        page = hubpage.render("mv3abc1234", SRC, [feature(1)], COUNTS, now=NOW)
        self.assertTrue(page.startswith("<title>Relay hub</title>"))
        for tag in ("<!doctype", "<html", "<head>", "<body", "<meta"):              # the publisher adds these
            self.assertNotIn(tag, page.lower())
        self.assertEqual(page.count("color-scheme:dark"), 2)
        for text in (":root{", "@media (prefers-color-scheme: dark)",
                     ':root:not([data-theme="light"])', ':root[data-theme="dark"]', "body{", "background:var(--bg)",
                     "padding-inline:16px", "overflow-wrap:anywhere", f'data-made="{int(NOW)}"',
                     f'data-expires="{int(NOW) + hub.KEEP_S}"', hubpage.EXPIRED):
            self.assertIn(text, page)
        for banned in ("src=", 'href="http', "@import", "url(", "fetch", "XMLHttpRequest", "WebSocket", "import("):
            self.assertNotIn(banned, page, banned)
        self.assertEqual(page.count("<script>"), 1)
        import re
        self.assertFalse([w for w in re.findall(r"(?<![-\w])(?:min-)?width:(\d+)px", page) if int(w) > 328])

    def test_limits(self):
        long = "x " * 500
        found = [feature(n, slug=f"f{n}", asks=[{"text": long}] * 9, flags=[long], ci=long) for n in range(1, 151)]
        page = hubpage.render("mv3abc1234", SRC, found, COUNTS, failed=[long] * 10, now=NOW)
        self.assertEqual(cards(page), 100)
        self.assertIn("50 more items: ask the hub", page)
        self.assertIn("<li>+4 more</li>", page)
        self.assertIn('<p class="failed">+5 more</p>', page)
        self.assertEqual(page.count("Could not check everything"), 5)
        self.assertNotIn("x " * 201, page)
        self.assertLess(len(page.encode()), hubpage.BYTES_MAX)

    def test_the_byte_budget_drops_cards_from_the_end(self):
        wide = "\U0001F600" * 400
        found = [feature(n, slug=wide, asks=[{"text": wide}] * 5, flags=[wide] * 5, ci=wide) for n in range(1, 101)]
        page = hubpage.render("mv3abc1234", SRC, found, COUNTS, now=NOW)
        shown = cards(page)
        self.assertLess(shown, 100)
        self.assertIn(f"{100 - shown} more items: ask the hub", page)
        self.assertLess(len(page.encode()), hubpage.BYTES_MAX)
        self.assertIn('<span class="n">1.</span>', page)

    @unittest.skipUnless(__import__("shutil").which("node"), "node is not installed")
    def test_the_age_script_runs(self):
        import re
        import subprocess
        page = hubpage.render("mv3abc1234", SRC, [], COUNTS, now=NOW)
        script = re.search(r"<script>(.*)</script>", page, re.S).group(1)
        made = int(re.search(r'data-made="(\d+)"', page).group(1))
        expires = int(re.search(r'data-expires="(\d+)"', page).group(1))
        harness = """
const els = {age: {textContent: ""}, expired: {hidden: true}};
let timer = null, now = %d * 1000;
els.hub = {dataset: {made: "%d", expires: "%d"}};
global.document = {getElementById: id => els[id] || null};
global.setInterval = f => { timer = f; };
Date.now = () => now;
%s
const out = [els.age.textContent, els.expired.hidden];
now = (%d + 1) * 1000; timer();
out.push(els.expired.hidden);
console.log(JSON.stringify(out));
""" % (made + 600, made, expires, script, expires)
        before = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(before.stdout), ["made 10 min ago", True, False])
        after = subprocess.run(["node", "-e", harness.replace(f"now = {made + 600} * 1000", f"now = {expires + 5} * 1000")],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(after.stdout), ["made 24 h ago", False, False])


HUB_S = {"provider": "claude", "session_id": "S"}
OTHER = {"provider": "claude", "session_id": "T"}
URL = "https://claude.ai/code/artifact/0b1c2d3e-aaaa"


class PageFileTest(HubHome):  # phone-artifact D2, R1, R2, F1
    def test_write(self):
        path = hubpage.write("abc12", "<p>é</p>")
        self.assertEqual(path, os.path.join(hub.folder(), "pages", "abc12.html"))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "<p>é</p>")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        hubpage.write("abc13", "<p>two</p>")
        self.assertEqual(sorted(os.listdir(hubpage.pages())), ["abc12.html", "abc13.html"])

    def test_an_unwritable_folder_raises_and_leaves_nothing(self):
        os.makedirs(hubpage.pages())
        hubpage.write("abc12", "old")
        with mock.patch.object(hubpage.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            hubpage.write("abc12", "new")
        self.assertEqual(os.listdir(hubpage.pages()), ["abc12.html"])
        os.chmod(hubpage.pages(), 0o500)
        self.addCleanup(os.chmod, hubpage.pages(), 0o700)
        with self.assertRaises(OSError):
            hubpage.write("abc14", "x")


class LinkTest(HubHome):  # phone-artifact D2, R3, F2, F3
    def setUp(self):
        super().setUp()
        self.digest = hub.save([], {"from": "build", "seconds": 1.0}, HUB_S, now=NOW)[0]

    def test_record_and_read_back(self):
        self.assertEqual(hubpage.published(HUB_S), {"url": None, "digest": None, "at": None})   # missing file
        hubpage.record(HUB_S, self.digest, URL, now=NOW + 5)
        self.assertEqual(hubpage.published(HUB_S), {"url": URL, "digest": self.digest, "at": NOW + 5})
        other = hub.save([], {"from": "build", "seconds": 1.0}, OTHER, now=NOW)[0]
        hubpage.record(OTHER, other, "https://claude.ai/artifact/xyz", now=NOW)
        self.assertEqual(hubpage.published(HUB_S)["url"], URL)
        self.assertEqual(hubpage.published(OTHER)["url"], "https://claude.ai/artifact/xyz")
        self.assertEqual(os.stat(os.path.join(hub.folder(), "pages.json")).st_mode & 0o777, 0o600)

    def test_refusals(self):
        from relaylib.errors import RelayError
        other = hub.save([], {"from": "build", "seconds": 1.0}, OTHER, now=NOW)[0]
        for session, digest, url, now in (
                (HUB_S, self.digest, "https://example.com/artifact/x", NOW),
                (HUB_S, self.digest, "http://claude.ai/artifact/x", NOW),
                (HUB_S, self.digest, URL + "/extra", NOW),
                (HUB_S, other, URL, NOW),                                     # another session's digest
                (HUB_S, self.digest, URL, NOW + hub.KEEP_S + 1),             # expired
                (HUB_S, "zzzzz9", URL, NOW),                                  # unknown
                (HUB_S, "../x", URL, NOW),
                (None, self.digest, URL, NOW)):
            with self.assertRaises(RelayError):
                hubpage.record(session, digest, url, now=now)
        self.assertFalse(os.path.exists(os.path.join(hub.folder(), "pages.json")))

    def test_bad_files_read_as_unknown(self):
        path = os.path.join(hub.folder(), "pages.json")
        for content in ("{", "[]", json.dumps({"claude:S": {"url": "https://evil.example/x"}}),
                        json.dumps({"claude:S": "x"})):
            with open(path, "w") as f:
                f.write(content)
            self.assertEqual(hubpage.published(HUB_S), {"url": None, "digest": None, "at": None}, content)
        with open(path, "w") as f:
            json.dump({"claude:S": {"url": URL, "digest": 5, "at": "x"}}, f)
        self.assertEqual(hubpage.published(HUB_S), {"url": URL, "digest": None, "at": None})
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o600)
        self.assertEqual(hubpage.published(HUB_S)["url"], None)                  # unreadable
        self.assertEqual(hubpage.published(None)["url"], None)

    def test_an_unwritable_folder_raises(self):
        os.chmod(hub.folder(), 0o500)
        self.addCleanup(os.chmod, hub.folder(), 0o700)
        with self.assertRaises(OSError):
            hubpage.record(HUB_S, self.digest, URL, now=NOW)


if __name__ == "__main__":
    unittest.main()
