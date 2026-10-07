import json, os, shutil, tempfile, unittest
from unittest import mock
from relaylib import transcripts
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


if __name__ == "__main__":
    unittest.main()
