import fcntl
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from relaylib import sessions


def ev(name, **extra):
    return dict({"session_id": "s1", "hook_event_name": name, "cwd": "/work"}, **extra)


class ApplyTest(unittest.TestCase):
    def run_events(self, provider, events, start=1000.0):
        record = None
        for i, event in enumerate(events):
            new = sessions.apply(record, provider, event, start + i)
            record = new or record
        return record

    def test_states_follow_events(self):
        r = self.run_events("claude", [ev("UserPromptSubmit"), ev("Stop")])
        self.assertEqual((r["state"], r["since"], r["at"]), ("waiting", 1001.0, 1001.0))
        r = self.run_events("claude", [ev("SessionStart"), ev("Notification", notification_type="idle_prompt")])
        self.assertEqual(r["state"], "waiting")
        r = self.run_events("codex", [ev("UserPromptSubmit"), ev("Interrupt")])
        self.assertEqual(r["state"], "waiting")
        r = self.run_events("claude", [ev("UserPromptSubmit"), ev("SessionEnd")])
        self.assertEqual(r["state"], "ended")
        self.assertIsNone(sessions.apply(None, "claude", ev("Notification", notification_type="auth_success"), 1))
        self.assertIsNone(sessions.apply(None, "codex", ev("SessionStart"), 1))   # Codex SessionStart is not mapped

    def test_reopening_a_session_is_not_work(self):
        # kpi-collection, 2026-10-07: Stop, then the session was reopened with no prompt (waiting-visibility D1)
        for source in ("resume", "startup", "clear", None, "other"):
            start = ev("SessionStart", **({"source": source} if source else {}))
            r = self.run_events("claude", [ev("Stop"), start])
            self.assertEqual((r["state"], r["since"], r["at"], r["event"]), ("waiting", 1000.0, 1001.0, "SessionStart"))
        ask = ev("PermissionRequest", tool_name="Bash", tool_input={"command": "rm x"}, tool_use_id="t1")
        r = self.run_events("claude", [ev("UserPromptSubmit"), ask, ev("SessionStart", source="resume")])
        self.assertEqual((r["state"], r["since"], len(r["pending"])), ("permission", 1001.0, 1))
        r = self.run_events("claude", [ev("UserPromptSubmit"), ev("SessionStart", source="resume")])
        self.assertEqual(r["state"], "waiting")                 # at the prompt until the owner types

    def test_session_start_without_a_record_and_compact(self):
        self.assertEqual(sessions.apply(None, "claude", ev("SessionStart", source="startup"), 5)["state"], "waiting")
        self.assertEqual(sessions.apply(None, "claude", ev("SessionStart", source="compact"), 5)["state"], "working")
        r = self.run_events("claude", [ev("Stop"), ev("SessionStart", source="compact")])
        self.assertEqual(r["state"], "working")                 # compaction happens mid-turn
        r = self.run_events("claude", [ev("SessionStart", source="startup"), ev("UserPromptSubmit")])
        self.assertEqual(r["state"], "working")

    def test_since_keeps_its_value_while_the_state_holds(self):
        r = self.run_events("claude", [ev("Stop"), ev("Notification", notification_type="idle_prompt")])
        self.assertEqual((r["since"], r["at"]), (1000.0, 1001.0))

    def test_a_permission_is_cleared_by_its_own_tool(self):
        ask = ev("PermissionRequest", tool_name="Bash", tool_input={"command": "rm x"}, tool_use_id="t1")
        done = ev("PostToolUse", tool_name="Bash", tool_input={"command": "rm x"}, tool_use_id="t1")
        r = self.run_events("claude", [ev("UserPromptSubmit"), ask])
        self.assertEqual((r["state"], len(r["pending"])), ("permission", 1))
        r = self.run_events("claude", [ev("UserPromptSubmit"), ask, done])
        self.assertEqual((r["state"], r["pending"]), ("working", []))

    def test_parallel_tool_does_not_clear_a_pending_prompt(self):
        ask = ev("PermissionRequest", tool_name="Bash", tool_input={"command": "rm x"})
        other = ev("PostToolUse", tool_name="Read", tool_input={"file_path": "a"})
        same_other_id = ev("PostToolUse", tool_name="Bash", tool_input={"command": "rm x"}, tool_use_id="t9")
        ask_id = ev("PermissionRequest", tool_name="Bash", tool_input={"command": "rm x"}, tool_use_id="t1")
        r = self.run_events("codex", [ev("UserPromptSubmit"), ask, other])
        self.assertEqual(r["state"], "permission")
        r = self.run_events("claude", [ev("UserPromptSubmit"), ask_id, same_other_id])
        self.assertEqual(r["state"], "permission")              # ids differ: not its completion

    def test_empty_string_ids_are_ids(self):
        ask = ev("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="")
        other = ev("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="x")
        own = ev("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="")
        self.assertEqual(self.run_events("claude", [ask, other])["state"], "permission")
        self.assertEqual(self.run_events("claude", [ask, own])["state"], "working")

    def test_a_record_of_the_wrong_shape_is_replaced(self):
        good = sessions.apply(None, "claude", ev("Stop"), 1.0)
        entry = {"tool_use_id": None, "tool_name": "Bash", "input_sha1": "x"}
        no_id = {"tool_name": "Bash", "input_sha1": "x"}
        for broken in (dict(good, provider=None), dict(good, provider=[]), dict(good, provider={"a": 1}),
                       dict(good, pending=[{"tool_name": 1}]), dict(good, pending=[no_id]),
                       dict(good, pending=[dict(entry, extra=1)]), dict(good, at=True), dict(good, event=None),
                       dict(good, session_id="other"), dict(good, provider="codex"), [], "x", None):
            r = sessions.apply(broken, "claude", ev("UserPromptSubmit"), 2.0)
            self.assertEqual((r["state"], r["since"]), ("working", 2.0), broken)

    def test_two_identical_requests_need_two_completions(self):
        ask = ev("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
        done = ev("PostToolUse", tool_name="Bash", tool_input={"command": "ls"})
        r = self.run_events("codex", [ev("UserPromptSubmit"), ask, ask, done])
        self.assertEqual((r["state"], len(r["pending"])), ("permission", 1))
        r = self.run_events("codex", [ev("UserPromptSubmit"), ask, ask, done, done])
        self.assertEqual(r["state"], "working")

    def test_tool_activity_never_ends_waiting(self):
        r = self.run_events("claude", [ev("UserPromptSubmit"), ev("Stop"),
                                       ev("PostToolUse", tool_name="Read", tool_input={})])
        self.assertEqual((r["state"], r["since"], r["at"]), ("waiting", 1001.0, 1002.0))
        r = self.run_events("claude", [ev("SessionEnd"), ev("PostToolUse", tool_name="Read", tool_input={})])
        self.assertEqual(r["state"], "ended")

    def test_turn_ends_and_prompts_empty_the_pending_list(self):
        ask = ev("PermissionRequest", tool_name="Bash", tool_input={"command": "x"})
        for closer in ("Stop", "UserPromptSubmit", "SessionEnd"):
            r = self.run_events("claude", [ask, ev(closer)])
            self.assertEqual(r["pending"], [], closer)

    def test_frequent_tool_use_writes_at_most_every_30_seconds(self):
        tool = ev("PostToolUse", tool_name="Read", tool_input={})
        r = sessions.apply(None, "claude", ev("UserPromptSubmit"), 1000.0)
        self.assertIsNone(sessions.apply(r, "claude", tool, 1010.0))
        self.assertEqual(sessions.apply(r, "claude", tool, 1031.0)["at"], 1031.0)

    def test_cwd_is_resolved_and_kept_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = sessions.apply(None, "claude", ev("UserPromptSubmit", cwd=tmp), 1)
            self.assertEqual(r["cwd"], os.path.realpath(tmp))
            no_cwd = {"session_id": "s1", "hook_event_name": "Stop"}
            self.assertEqual(sessions.apply(r, "claude", no_cwd, 2)["cwd"], os.path.realpath(tmp))
        self.assertIsNone(sessions.apply(None, "claude", {"session_id": "s1", "hook_event_name": "Stop"}, 1)["cwd"])
        self.assertIsNone(sessions.apply(None, "claude", ev("Stop", cwd=""), 1)["cwd"])   # empty: no folder given

    def test_invalid_input_is_ignored(self):
        for bad in ({}, {"session_id": "", "hook_event_name": "Stop"}, {"session_id": 3, "hook_event_name": "Stop"},
                    ev("PermissionRequest"), ev("PostToolUse", tool_name=5), ev("Stop", cwd=7), ev("Stop", cwd=None),
                    [], "text"):
            self.assertIsNone(sessions.apply(None, "claude", bad, 1), bad)


class CaptureTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patch = mock.patch.dict(os.environ, {"RELAY_HOME": temp.name})
        patch.start()
        self.addCleanup(patch.stop)

    def test_capture_writes_one_private_record_per_session(self):
        self.assertEqual(sessions.capture("claude", io.StringIO(json.dumps(ev("Stop"))), now=5.0), 0)
        path = sessions.record_path("claude", "s1")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(json.load(open(path))["state"], "waiting")
        self.assertEqual([r["session_id"] for r in sessions.read_records()], ["s1"])
        self.assertNotEqual(sessions.record_path("codex", "s1"), path)

    def test_bad_input_and_busy_lock_write_nothing(self):
        for text in ("not json", json.dumps({"x": 1}), ""):
            self.assertEqual(sessions.capture("claude", io.StringIO(text)), 0)
        self.assertEqual(sessions.read_records(), [])
        os.makedirs(sessions.folder(), exist_ok=True)
        with open(os.path.join(sessions.folder(), ".lock"), "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(sessions.capture("claude", io.StringIO(json.dumps(ev("Stop")))), 0)
        self.assertEqual(sessions.read_records(), [])

    def test_a_damaged_record_is_replaced(self):
        os.makedirs(sessions.folder(), exist_ok=True)
        with open(sessions.record_path("codex", "x"), "w") as f:
            json.dump({"session_id": "x", "state": "waiting", "pending": [], "since": 1, "at": 1}, f)   # no provider
        with open(os.path.join(sessions.folder(), "list.json"), "w") as f:
            json.dump({"provider": [], "session_id": "y", "state": "waiting", "event": "Stop", "cwd": None,
                       "pending": [], "since": 1, "at": 1}, f)                                       # unhashable provider
        self.assertEqual(sessions.read_records(), [])
        sessions.capture("claude", io.StringIO(json.dumps(ev("PermissionRequest", tool_name="Bash", tool_input={}))))
        path = sessions.record_path("claude", "s1")
        data = json.load(open(path))
        del data["pending"][0]["tool_use_id"]                                                         # damaged entry
        json.dump(data, open(path, "w"))
        sessions.capture("claude", io.StringIO(json.dumps(ev("PostToolUse", tool_name="Bash", tool_input={}))))
        self.assertEqual(json.load(open(path))["state"], "working")                                   # replaced, not dropped
        with open(sessions.record_path("claude", "s1"), "w") as f:
            f.write("{broken")
        sessions.capture("claude", io.StringIO(json.dumps(ev("Stop"))), now=5.0)
        self.assertEqual(json.load(open(sessions.record_path("claude", "s1")))["state"], "waiting")

    def test_a_storage_failure_is_silent_and_keeps_the_old_record(self):
        sessions.capture("claude", io.StringIO(json.dumps(ev("Stop"))), now=5.0)
        before = open(sessions.record_path("claude", "s1")).read()
        with mock.patch.object(sessions.os, "replace", side_effect=OSError("disk full")):
            self.assertEqual(sessions.capture("claude", io.StringIO(json.dumps(ev("UserPromptSubmit"))), now=6.0), 0)
        self.assertEqual(open(sessions.record_path("claude", "s1")).read(), before)
        self.assertFalse([n for n in os.listdir(sessions.folder()) if n.startswith(".relay-")])

    def test_an_open_pipe_cannot_hold_the_agent(self):
        import time
        relay = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "relay")
        proc = subprocess.Popen([sys.executable, relay, "hook", "claude"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
        self.addCleanup(proc.stdin.close)
        proc.stdin.write(b'{"session_id": "s1", ')
        proc.stdin.flush()                                    # never closed: no EOF
        started = time.monotonic()
        self.assertEqual(proc.wait(timeout=5), 0)
        self.assertLess(time.monotonic() - started, 1.5)     # the read deadline, plus interpreter start
        self.assertEqual((proc.stdout.read(), proc.stderr.read()), (b"", b""))
        proc.stdout.close()
        proc.stderr.close()
        self.assertEqual(sessions.read_records(), [])

    def test_bin_relay_hook_is_silent_and_always_succeeds(self):
        relay = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "relay")
        for text in (json.dumps(ev("Stop")), "garbage"):
            p = subprocess.run([sys.executable, relay, "hook", "claude"], input=text, capture_output=True,
                               text=True, env=dict(os.environ), timeout=5)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        self.assertEqual(json.load(open(sessions.record_path("claude", "s1")))["state"], "waiting")

    def test_prune_deletes_old_records_under_the_folder_lock(self):
        sessions.capture("claude", io.StringIO(json.dumps(ev("Stop"))), now=1000.0)
        sessions.capture("codex", io.StringIO(json.dumps(ev("Stop", session_id="s2"))), now=1000.0 + 6 * 86400)
        lock_path = os.path.join(sessions.folder(), ".lock")
        with open(lock_path) as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with mock.patch.object(sessions, "LOCK_WAIT_PRUNE_S", 0.05):
                self.assertEqual(sessions.prune(now=1000.0 + 8 * 86400), 0)      # busy: skipped
        self.assertEqual(sessions.prune(now=1000.0 + 8 * 86400), 1)
        self.assertEqual([r["session_id"] for r in sessions.read_records()], ["s2"])
        self.assertTrue(os.path.exists(lock_path))                                 # the lock file stays
        with open(os.path.join(sessions.folder(), "junk.json"), "w") as f:
            f.write("{broken")
        self.assertEqual(sessions.prune(now=1000.0 + 30 * 86400), 1)               # only s2, now old
        self.assertTrue(os.path.exists(os.path.join(sessions.folder(), "junk.json")))  # unreadable: left alone


class InboxTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patch = mock.patch.dict(os.environ, {"RELAY_HOME": temp.name})
        patch.start()
        self.addCleanup(patch.stop)

    def capture(self, provider, env, name="UserPromptSubmit"):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_MESSAGING")}
        with mock.patch.dict(os.environ, dict(clean, **env), clear=True):
            sessions.capture(provider, io.StringIO(json.dumps(ev(name))), now=5.0)
        with open(sessions.record_path(provider, "s1")) as f:
            return json.load(f)

    def test_the_claude_hook_records_the_inbox_path_only(self):
        env = {"CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc/1.sock", "CLAUDE_CODE_MESSAGING_TOKEN": "secret-token"}
        record = self.capture("claude", env)
        self.assertEqual(record["inbox"], "/tmp/cc/1.sock")
        self.assertNotIn("secret-token", json.dumps(record))
        self.assertNotIn("inbox", self.capture("claude", {}, "Stop"))                     # unset drops it
        self.assertNotIn("inbox", self.capture("claude", {"CLAUDE_CODE_MESSAGING_SOCKET": "rel/1.sock"},
                                               "UserPromptSubmit"))
        self.assertNotIn("inbox", self.capture("codex", env))                             # never for Codex

    def test_shape_check(self):
        good = sessions.apply(None, "claude", ev("Stop"), 1.0)
        self.assertTrue(sessions._valid(good))
        self.assertTrue(sessions._valid(dict(good, inbox="/tmp/x.sock")))
        for bad in ("", "x.sock", 5, None):
            self.assertFalse(sessions._valid(dict(good, inbox=bad)))


class MergeNoticeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = os.path.join(temp.name, "home")
        os.makedirs(self.home)
        patch = mock.patch.dict(os.environ, {"RELAY_HOME": self.home})
        patch.start()
        self.addCleanup(patch.stop)
        self.repo = os.path.join(temp.name, "proj")
        os.makedirs(os.path.join(self.repo, "src"))
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "remote", "add", "origin", "https://example.test/proj.git"], check=True)
        from relaylib import state
        self.state = state

    def feature(self, slug, pr, session="s1", status="ready-to-merge"):
        st = self.state.new_state(slug, "proj", {"provider": "claude", "session": session}, f"feat/{slug}", small=True)
        st.update(status=status, pr=pr)
        self.state.write_state(self.state.state_path(self.repo, slug), st)

    def merged(self, *prs):
        with open(os.path.join(self.home, "merged.json"), "w") as f:
            json.dump({f"https://example.test/proj.git#{pr}": True for pr in prs}, f)

    def hook(self, provider="claude", name="UserPromptSubmit", session="s1", cwd=None):
        event = ev(name, session_id=session, cwd=cwd or os.path.join(self.repo, "src"))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(sessions.capture(provider, io.StringIO(json.dumps(event)), now=10.0), 0)
        return out.getvalue()

    def test_a_merged_pr_is_told_once_to_the_session_that_owns_it(self):
        self.feature("docker", 26)
        self.feature("other", 27)          # not merged
        self.feature("theirs", 28, session="s2")
        self.merged(26, 28)
        out = json.loads(self.hook())["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "UserPromptSubmit")
        self.assertIn("PR #26 (docker) was merged", out["additionalContext"])
        self.assertNotIn("#27", out["additionalContext"])
        self.assertNotIn("#28", out["additionalContext"])
        self.assertEqual(self.hook(), "")                       # once per session and PR
        self.merged(26, 27, 28)
        self.assertIn("PR #27 (other)", self.hook())
        self.assertEqual(self.hook(name="Stop"), "")            # only when the session starts work

    def test_codex_and_claude_session_start(self):
        self.feature("docker", 26)
        self.merged(26)
        self.assertEqual(self.hook(provider="codex", name="SessionStart"), "")   # not a working event for codex
        self.assertIn("PR #26", self.hook(provider="codex"))
        out = json.loads(self.hook(name="SessionStart"))["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")

    def test_nothing_without_a_merge_or_outside_a_repo_and_never_a_failure(self):
        self.feature("docker", 26)
        self.assertEqual(self.hook(), "")                       # no merge cache yet
        self.merged(26)
        self.assertEqual(self.hook(cwd=self.home), "")          # not inside the project
        with mock.patch("relaylib.state.parse_state", side_effect=RuntimeError("boom")):
            self.assertEqual(self.hook(session="s3"), "")

    def test_one_unreadable_feature_does_not_hide_the_others(self):
        self.feature("docker", 26)
        self.merged(26)
        broken = os.path.join(self.repo, "docs", "relay", "aaa-broken")
        os.makedirs(broken)
        with open(os.path.join(broken, "state.md"), "w") as f:
            f.write("not a state file")
        self.assertIn("PR #26 (docker)", self.hook())

    def test_a_capitalized_docs_folder_is_found(self):
        self.feature("docker", 26)
        self.merged(26)
        os.rename(os.path.join(self.repo, "docs"), os.path.join(self.repo, "Docs"))
        self.assertIn("PR #26 (docker)", self.hook())


T1, T2 = "Thu Oct  8 15:13:49 2026", "Fri Oct  9 09:00:00 2026"


def table(*rows):
    """ps lines: (pid, ppid, started, args)."""
    return [f"{pid:>6} {ppid:>6} {started} {args}" for pid, ppid, started, args in rows]


class ProcessTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        p = mock.patch.dict(os.environ, {"RELAY_HOME": tmp})
        p.start()
        self.addCleanup(p.stop)
        self.me = os.getpid()

    def chain(self, agent="/opt/agent/bin/claude --resume x", started=T1):
        return table((1, 0, T1, "/sbin/launchd"), (50, 1, T1, "ghostty"), (60, 50, started, agent),
                     (70, 60, T1, "/bin/sh -c relay hook claude"), (self.me, 70, T1, "python3.11 relay hook claude"))

    def test_the_walk_finds_the_agent_through_a_shell(self):
        self.assertEqual(sessions.find_process("claude", self.chain(), self.me), {"pid": 60, "started": T1})
        npm = self.chain("node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js")
        self.assertEqual(sessions.find_process("claude", npm, self.me), {"pid": 60, "started": T1})
        self.assertIsNone(sessions.find_process("codex", self.chain(), self.me))     # a Claude ancestor
        codex = self.chain("node /opt/node_modules/@openai/codex/bin/codex.js")
        self.assertEqual(sessions.find_process("codex", codex, self.me)["pid"], 60)
        self.assertIsNone(sessions.find_process("claude", self.chain("vim"), self.me))
        self.assertIsNone(sessions.find_process("claude", None, self.me))
        self.assertIsNone(sessions.find_process("claude", ["garbage"], self.me))

    def test_the_walk_stops_at_pid_1_and_after_20_steps(self):
        top = [(1, 0, T1, "claude"), (100, 1, T1, "sh"), (self.me, 100, T1, "python3.11")]
        self.assertIsNone(sessions.find_process("claude", table(*top), self.me))      # pid 1 is never the agent

        def depth(n):
            rows = [(1, 0, T1, "launchd"), (200, 1, T1, "claude")]
            rows += [(300 + i, 301 + i if i < n - 1 else 200, T1, "sh") for i in range(n)]
            return table(*rows, (self.me, 300, T1, "python3.11"))
        self.assertIsNone(sessions.find_process("claude", depth(25), self.me))
        self.assertEqual(sessions.find_process("claude", depth(5), self.me)["pid"], 200)

    def test_ps_failures_give_none(self):
        for effect in (subprocess.TimeoutExpired("ps", 0.3), FileNotFoundError("ps")):
            with mock.patch("subprocess.run", side_effect=effect):
                self.assertIsNone(sessions._ps("pid=", 0.3))
        for rc, out in ((1, "  1 x\n"), (0, "\n  \n")):
            with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], rc, out, "")):
                self.assertIsNone(sessions._ps("pid=", 0.3))

    def capture(self, name, lines, **extra):
        self.clock = getattr(self, "clock", 1000.0) + 60  # past the PostToolUse refresh interval
        with mock.patch.object(sessions, "_ps", return_value=lines):
            sessions.capture("claude", io.StringIO(json.dumps(ev(name, **extra))), now=self.clock)
        with open(sessions.record_path("claude", "s1")) as f:
            return json.load(f)

    def test_capture_records_keeps_drops_and_retries(self):
        self.assertEqual(self.capture("UserPromptSubmit", self.chain())["process"], {"pid": 60, "started": T1})
        with mock.patch.object(sessions, "_ps", side_effect=AssertionError("no walk")):
            sessions.capture("claude", io.StringIO(json.dumps(ev("PostToolUse", tool_name="Bash"))))
        with open(sessions.record_path("claude", "s1")) as f:
            self.assertEqual(json.load(f)["process"], {"pid": 60, "started": T1})    # kept without a walk
        resumed = self.capture("UserPromptSubmit", None)              # resumed elsewhere, discovery failed
        self.assertNotIn("process", resumed)
        again = self.capture("PostToolUse", self.chain(started=T2), tool_name="Edit")
        self.assertEqual(again["process"], {"pid": 60, "started": T2})  # the next tool event walks again
        self.assertNotIn("process", self.capture("SessionStart", self.chain("vim"), source="resume"))

    def test_record_shape(self):
        base = {"provider": "claude", "session_id": "s", "cwd": None, "state": "working", "since": 1.0, "at": 1.0,
                "event": "Stop", "pending": []}
        self.assertTrue(sessions._valid(base))
        self.assertTrue(sessions._valid(dict(base, process={"pid": 5, "started": T1})))
        for bad in ({"pid": 0, "started": T1}, {"pid": "5", "started": T1}, {"pid": 5, "started": ""},
                    {"pid": True, "started": T1}, {"pid": 5}, None):
            self.assertFalse(sessions._valid(dict(base, process=bad)), bad)

    def test_alive(self):
        records = [{"session_id": "a", "process": {"pid": 60, "started": T1}},
                   {"session_id": "reused", "process": {"pid": 61, "started": T1}},
                   {"session_id": "gone", "process": {"pid": 62, "started": T1}}, {"session_id": "none"}]
        with mock.patch.object(sessions, "_ps", return_value=[f"{60:>6} {T1}", f"{61:>6} {T2}"]):
            self.assertEqual(sessions.alive(records), {"a"})
        for out in (None, ["not ps output"]):
            with mock.patch.object(sessions, "_ps", return_value=out):
                self.assertIsNone(sessions.alive(records))
