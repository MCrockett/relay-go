import json, os, shutil, tempfile, unittest
from unittest import mock
from relaylib import config, transcripts
from relaylib.usage import timestamp
from tests import helpers


def claude(mid, at, model="claude-opus-5-5", inp=10, cr=0, cw=0, out=5, branch="feat/x", text="SECRET TEXT"):
    return json.dumps({"type": "assistant", "timestamp": at, "gitBranch": branch, "message": {
        "id": mid, "model": model, "role": "assistant", "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": inp, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw,
                  "output_tokens": out}}})


def codex_ctx(model):
    return json.dumps({"timestamp": "2026-10-06T16:00:00Z", "type": "turn_context", "payload": {"model": model}})


def codex_count(at, inp, cached, out):
    return json.dumps({"timestamp": at, "type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out}}}})


class Homes(unittest.TestCase):
    """Temp RELAY_HOME, CLAUDE_CONFIG_DIR and CODEX_HOME for every test (R13)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "relay"),
                                         "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude"),
                                         "CODEX_HOME": os.path.join(self.tmp, "codex")})
        p.start()
        self.addCleanup(p.stop)


class ParseTest(Homes):
    def test_claude_dedupes_by_message_id_last_record_wins(self):
        fs = transcripts.FileState("claude")
        fs.parse([claude("m1", "2026-10-06T16:00:00Z", out=1), claude("m1", "2026-10-06T16:00:01Z", out=7),
                  claude("m2", "2026-10-06T16:01:00Z", inp=2, cr=100, cw=20)])
        self.assertEqual(len(fs.turns), 2)
        self.assertEqual(fs.turns["m1"]["output"], 7)
        self.assertEqual((fs.turns["m2"]["input"], fs.turns["m2"]["cached"]), (122, 100))
        fs.parse([claude("m1", "2026-10-06T16:00:02Z", out=9)])          # appended on a later refresh
        self.assertEqual(fs.turns["m1"]["output"], 9)
        self.assertEqual(len(fs.turns), 2)
        self.assertIsNone(fs.problem())

    def test_claude_skips_on_purpose_without_warning(self):
        fs = transcripts.FileState("claude")
        synthetic = json.loads(claude("m3", "2026-10-06T16:02:00Z", model="<synthetic>"))
        no_id = json.loads(claude("x", "2026-10-06T16:02:00Z"))
        del no_id["message"]["id"]
        fs.parse([claude("m1", "2026-10-06T16:00:00Z"), json.dumps(synthetic), json.dumps(no_id),
                  json.dumps({"type": "user", "message": {"content": "hi"}})])
        self.assertEqual(list(fs.turns), ["m1"])
        self.assertIsNone(fs.problem())

    def test_malformed_lines_and_missing_fields_are_reported_not_zero(self):
        missing = json.loads(claude("m2", "2026-10-06T16:00:00Z"))
        del missing["message"]["usage"]["cache_read_input_tokens"]
        fs = transcripts.FileState("claude")
        fs.parse([claude("m1", "2026-10-06T16:00:00Z"), "not json", json.dumps(missing)])
        self.assertEqual(list(fs.turns), ["m1"])
        self.assertEqual(fs.problem(), "partial: 1 of 2 usage records unreadable; 1 lines are not JSON")

    def test_claude_bad_usage_is_partial(self):
        bad = json.loads(claude("m2", "2026-10-06T16:00:00Z"))
        bad["message"]["usage"]["output_tokens"] = "lots"
        fs = transcripts.FileState("claude")
        fs.parse([claude("m1", "2026-10-06T16:00:00Z"), json.dumps(bad)])
        self.assertEqual(fs.problem(), "partial: 1 of 2 usage records unreadable")

    def test_codex_counts_increases_and_restarts_after_a_drop(self):
        fs = transcripts.FileState("codex")
        fs.parse([codex_ctx("gpt-6-astra"), codex_count("2026-10-06T16:00:00Z", 100, 80, 5),
                  codex_count("2026-10-06T16:00:01Z", 100, 80, 5),           # repeated: adds nothing
                  codex_count("2026-10-06T16:01:00Z", 250, 200, 9),
                  codex_count("2026-10-06T16:02:00Z", 40, 0, 2)])             # dropped: new count from zero
        got = sorted((t["input"], t["cached"], t["output"]) for t in fs.turns.values())
        self.assertEqual(got, [(40, 0, 2), (100, 80, 5), (150, 120, 4)])
        self.assertTrue(all(t["model"] == "gpt-6-astra" for t in fs.turns.values()))
        fs.parse([json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": None}})])
        self.assertIsNone(fs.problem())                                       # info: null is ignored

    def test_codex_context_without_model_is_partial(self):
        fs = transcripts.FileState("codex")
        fs.parse([json.dumps({"type": "turn_context", "payload": {}}), codex_count("2026-10-06T16:00:00Z", 1, 0, 1)])
        self.assertIn("partial", fs.problem())

    def test_no_records_and_format_change(self):
        fs = transcripts.FileState("claude")
        fs.parse([json.dumps({"type": "user"})] * 3)
        self.assertEqual(fs.problem(), "no usage records")
        fs = transcripts.FileState("claude")
        fs.parse([claude("m1", "2026-10-06T16:00:00Z")] + [json.dumps({"type": "assistant2"})] * 200)
        self.assertEqual(fs.problem(), "format changed: 200 lines without usage records")

    def test_state_round_trips_and_rejects_what_it_could_not_write(self):
        fs = transcripts.FileState("codex")
        fs.parse([codex_ctx("gpt-6-astra"), codex_count("2026-10-06T16:00:00Z", 100, 80, 5)])
        back = transcripts.FileState.from_json(json.loads(json.dumps(fs.to_json())))
        self.assertEqual((back.turns, back.last_total, back.model), (fs.turns, fs.last_total, fs.model))
        for change in ({"provider": "gemini"}, {"lines": "7"}, {"last_total": [1, "x"]}, {"turns": []},
                       {"had_valid": 1}, {"model": 3}):
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError, TypeError)):
                transcripts.FileState.from_json({**fs.to_json(), **change})

    def test_files_for_finds_both_layouts(self):
        projects, sessions = transcripts.claude_projects(), transcripts.codex_sessions()
        for path in (os.path.join(projects, "-a", "S1.jsonl"), os.path.join(projects, "-b", "S1", "subagents", "agent-1.jsonl"),
                     os.path.join(projects, "-a", "OTHER.jsonl"),
                     os.path.join(sessions, "2026", "10", "06", "rollout-2026-10-06T12-00-00-C1.jsonl")):
            helpers.write(path, "")
        self.assertEqual(len(transcripts.files_for("claude", "S1")), 2)
        self.assertEqual(len(transcripts.files_for("codex", "C1")), 1)
        self.assertEqual(transcripts.files_for("codex", "missing"), [])
        self.assertEqual(transcripts.files_for("owner", "S1"), [])


class IncrementalTest(Homes):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(transcripts.claude_projects(), "-a", "S1.jsonl")
        helpers.write(self.path, claude("m1", "2026-10-06T16:00:00Z") + "\n" + claude("m2", "2026-10-06T16:01:00Z") + "\n")
        self.cache = os.path.join(os.environ["RELAY_HOME"], "writer-usage.json")

    def turns(self):
        return transcripts.read_sessions({("claude", "S1")})["turns"][("claude", "S1")]

    def test_second_refresh_parses_nothing_new_and_appends_once(self):
        self.assertEqual(len(self.turns()), 2)
        with mock.patch.object(transcripts.FileState, "parse") as parse:
            transcripts.read_sessions({("claude", "S1")})
        parse.assert_not_called()                                        # unchanged file: no parsing
        with open(self.path, "a") as f:
            f.write(claude("m3", "2026-10-06T16:05:00Z") + "\n" + claude("m4", "2026-10-06T16:06:00Z")[:30])
        self.assertEqual(len(self.turns()), 3)                           # the partial last line waits
        with open(self.path, "a") as f:
            f.write(claude("m4", "2026-10-06T16:06:00Z")[30:] + "\n")
        got = transcripts.read_sessions({("claude", "S1")})
        self.assertEqual(len(got["turns"][("claude", "S1")]), 4)
        self.assertEqual(got["unreadable"], {})

    def test_replaced_or_shrunk_file_is_read_again(self):
        self.turns()
        with open(self.path, "w") as f:
            f.write(claude("z1", "2026-10-06T17:00:00Z") + "\n")
        self.assertEqual([t["at"] for t in self.turns()], [timestamp("2026-10-06T17:00:00Z")])

    def test_cache_holds_no_message_text_and_is_private(self):
        self.turns()
        with open(self.cache) as f:
            self.assertNotIn("SECRET TEXT", f.read())
        self.assertEqual(oct(os.stat(self.cache).st_mode & 0o777), "0o600")

    def test_corrupt_cache_is_rebuilt(self):
        for text in ("{oops", "[]", json.dumps({"version": 1, "files": {self.path: {"inode": "x"}}, "sessions": {}}),
                     json.dumps({"version": 1, "files": {self.path: {"inode": 1, "size": 1, "mtime_ns": 1,
                                 "offset": 0, "state": {"provider": "claude", "turns": []}}}, "sessions": {}})):
            with self.subTest(text=text[:20]):
                helpers.write(self.cache, text)
                self.assertEqual(len(self.turns()), 2)

    def test_complete_but_corrupt_cache_entries_are_rebuilt(self):
        self.turns()
        with open(self.cache) as f:
            good = json.load(f)
        first = next(iter(good["files"][self.path]["state"]["turns"]))
        for name, change in (("turn without at", lambda e: e["state"]["turns"][first].pop("at")),
                             ("string counter", lambda e: e["state"].__setitem__("lines", "7")),
                             ("bad baseline", lambda e: e["state"].__setitem__("last_total", [1, "x"])),
                             ("bad turn tokens", lambda e: e["state"]["turns"][first].__setitem__("input", -1)),
                             ("codex state for a claude log", lambda e: e["state"].__setitem__("provider", "codex"))):
            with self.subTest(name=name):
                broken = json.loads(json.dumps(good))
                change(broken["files"][self.path])
                broken["sessions"]["claude:S1"] = "not a list"
                helpers.write(self.cache, json.dumps(broken))
                got = transcripts.read_sessions({("claude", "S1")})
                self.assertEqual(len(got["turns"][("claude", "S1")]), 2)
                self.assertNotIn(("claude", "S1"), got["unreadable"])

    def test_large_file_is_read_in_bounded_chunks(self):
        with open(self.path, "a") as f:
            for n in range(3000):
                f.write(claude(f"big{n}", "2026-10-06T16:10:00Z", text="x" * 1000) + "\n")
        real = transcripts.FileState.parse
        with mock.patch.object(transcripts, "CHUNK", 64 * 1024), \
                mock.patch.object(transcripts.FileState, "parse", autospec=True, side_effect=real) as parse:
            got = self.turns()
        self.assertEqual(len(got), 3002)
        self.assertGreater(parse.call_count, 10)                                  # many bounded reads
        self.assertTrue(all(len(c.args[1]) < 200 for c in parse.call_args_list))  # never the whole file at once

    def test_inode_replacement_is_read_again(self):
        self.turns()
        os.rename(self.path, self.path + ".old")
        helpers.write(self.path, "".join(claude(f"n{i}", f"2026-10-06T18:0{i}:00Z") + "\n" for i in range(3)))
        got = self.turns()
        self.assertEqual(got[0]["at"], timestamp("2026-10-06T18:00:00Z"))
        self.assertEqual(len(got), 3)                                             # the old file's turns are gone

    def test_a_vanished_subagent_file_keeps_its_turns_and_is_reported(self):
        sub = os.path.join(os.path.dirname(self.path), "S1", "subagents", "agent-1.jsonl")
        helpers.write(sub, claude("s1", "2026-10-06T16:30:00Z") + "\n")
        self.assertEqual(len(self.turns()), 3)
        os.unlink(sub)
        for _ in range(2):                                                      # stays reported on later refreshes
            got = transcripts.read_sessions({("claude", "S1")})
            self.assertEqual(len(got["turns"][("claude", "S1")]), 3)
            self.assertIn("log not found", got["unreadable"][("claude", "S1")])

    def test_missing_log_and_partial_subagent_are_reported(self):
        bad = json.loads(claude("s2", "2026-10-06T16:31:00Z"))
        bad["message"]["usage"]["output_tokens"] = None
        sub = os.path.join(os.path.dirname(self.path), "S1", "subagents", "agent-1.jsonl")
        helpers.write(sub, claude("s1", "2026-10-06T16:30:00Z") + "\n" + json.dumps(bad) + "\n")
        got = transcripts.read_sessions({("claude", "S1"), ("codex", "gone")})
        self.assertEqual(got["unreadable"][("codex", "gone")], "log not found")
        self.assertIn("partial", got["unreadable"][("claude", "S1")])
        self.assertEqual(len(got["turns"][("claude", "S1")]), 3)            # what was read is still counted

    def test_unwritable_lock_or_cache_uses_memory_and_says_so(self):
        for target in ("write_lock", "atomic_write"):
            with self.subTest(target=target), mock.patch.object(config, target, side_effect=OSError("read-only")):
                got = transcripts.read_sessions({("claude", "S1")})
                self.assertEqual(len(got["turns"][("claude", "S1")]), 2)
                self.assertIn("usage cache not saved", got["notes"])

    def test_busy_lock_uses_memory_and_says_so(self):
        import fcntl
        os.makedirs(os.environ["RELAY_HOME"], exist_ok=True)
        with mock.patch.object(config, "LOCK_TIMEOUT_S", 0.05), \
                open(os.path.join(os.environ["RELAY_HOME"], "writer-usage.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            got = transcripts.read_sessions({("claude", "S1")})
        self.assertEqual(len(got["turns"][("claude", "S1")]), 2)
        self.assertIn("usage cache not saved", got["notes"])
        self.assertFalse(os.path.exists(self.cache))


if __name__ == "__main__":
    unittest.main()
