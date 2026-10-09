import json, os, unittest
from unittest import mock
from relaylib import agentask
from tests.test_transcripts import Homes


def said(text, kind="text"):
    block = {"type": "text", "text": text} if kind == "text" else {"type": "tool_use", "name": "Bash", "input": {}}
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [block]}})


def away(text):
    return json.dumps({"type": "system", "subtype": "away_summary", "content": text})


def prompt(text):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


def done(text):
    return json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": text}})


def reply(text):
    return json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                            "content": [{"type": "output_text", "text": text}]}})


class LastWordsTest(Homes):
    def claude_log(self, lines, session="S1", tail="\n"):
        path = os.path.join(self.tmp, "claude", "projects", "-work", session + ".jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(lines) + tail)
        return path

    def codex_log(self, lines, session="C1"):
        path = os.path.join(self.tmp, "codex", "sessions", "2026", "10", "07", f"rollout-2026-10-07T10-00-00-{session}.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def test_claude_last_assistant_text(self):
        self.claude_log([prompt("go"), said("first"), said("How do you want to treat the pi window?")])
        self.assertEqual(agentask.last_words("claude", "S1"),
                         {"source": "agent", "text": "How do you want to treat the pi window?"})

    def test_claude_newest_candidate_wins_whatever_its_kind(self):
        self.claude_log([said("Still open: start the build?"), away("Next is your call on the build.")])
        self.assertEqual(agentask.last_words("claude", "S1"), {"source": "summary", "text": "Next is your call on the build."})
        self.claude_log([away("old summary"), prompt("ok"), said("new answer")])
        self.assertEqual(agentask.last_words("claude", "S1")["text"], "new answer")

    def test_claude_tool_use_only_is_not_a_candidate_and_subagents_are_ignored(self):
        self.claude_log([said("Shall I continue?"), said("", kind="tool")])
        sub = os.path.join(self.tmp, "claude", "projects", "-work", "S1", "subagents", "a.jsonl")
        os.makedirs(os.path.dirname(sub))
        with open(sub, "w") as f:
            f.write(said("subagent words") + "\n")
        self.assertEqual(agentask.last_words("claude", "S1")["text"], "Shall I continue?")

    def test_codex_task_complete_and_assistant_messages(self):
        self.codex_log([reply("thinking out loud"), done("Should I start the build?")])
        self.assertEqual(agentask.last_words("codex", "C1"), {"source": "agent", "text": "Should I start the build?"})
        self.codex_log([reply("only a reply")], session="C2")
        self.assertEqual(agentask.last_words("codex", "C2")["text"], "only a reply")
        self.codex_log([done("last turn's answer"), reply("this turn: approve the command?")], session="C3")
        self.assertEqual(agentask.last_words("codex", "C3")["text"], "this turn: approve the command?")
        self.codex_log([json.dumps({"type": "event_msg", "payload": {"type": "token_count"}})], session="C4")
        self.assertIsNone(agentask.last_words("codex", "C4"))

    def test_only_the_last_window_is_read_and_partial_lines_are_skipped(self):
        filler = said("x" * 1000)
        self.claude_log([said("too old")] + [filler] * 1100 + [said("the real question?")])
        self.assertEqual(agentask.last_words("claude", "S1")["text"], "the real question?")
        self.claude_log([said("complete answer"), '{"type": "assistant", "mess'], tail="")
        self.assertEqual(agentask.last_words("claude", "S1")["text"], "complete answer")

    def test_missing_unreadable_or_unknown_logs_give_none(self):
        self.assertIsNone(agentask.last_words("claude", "nobody"))
        self.assertIsNone(agentask.last_words("owner", "me@host"))
        self.claude_log(["not json", "[1, 2]", json.dumps({"type": "assistant", "message": "odd"})])
        self.assertIsNone(agentask.last_words("claude", "S1"))
        path = self.claude_log([said("hidden")], session="S2")
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o600)
        self.assertIsNone(agentask.last_words("claude", "S2"))


class TextLimitsTest(unittest.TestCase):
    def test_excerpt_collapses_and_cuts(self):
        self.assertEqual(agentask.excerpt("  How do\n\n you   want  it? "), "How do you want it?")
        cut = agentask.excerpt("word " * 100)
        self.assertEqual(len(cut), 161)
        self.assertTrue(cut.endswith("…"))

    def test_full_is_capped(self):
        self.assertEqual(agentask.full("a\nb"), "a\nb")
        self.assertEqual(len(agentask.full("y" * 5000)), 4000)


def started(entrypoint):
    return json.dumps({"type": "user", "entrypoint": entrypoint, "message": {"role": "user", "content": "go"}})


def meta(originator="codex-tui", source="cli"):
    return json.dumps({"type": "session_meta", "payload": {"id": "C1", "originator": originator, "source": source}})


class InteractiveTest(LastWordsTest):
    def test_claude_entrypoints(self):
        for entry, want in (("cli", True), ("claude-vscode", True), ("sdk-cli", False), ("sdk-ts", False)):
            self.claude_log([json.dumps({"type": "summary"}), started(entry), said("hi")], session=entry)
            self.assertEqual(agentask.interactive("claude", entry), want, entry)

    def test_codex_origins(self):
        for sid, line, want in (("C1", meta(), True), ("C2", meta("codex_exec", "exec"), False),
                                ("C3", meta("other", "exec"), False), ("C4", reply("no meta first"), False)):
            self.codex_log([line, reply("hi")], session=sid)
            self.assertEqual(agentask.interactive("codex", sid), want, sid)

    def test_missing_empty_and_markerless_transcripts(self):
        self.assertFalse(agentask.interactive("claude", "nope"))
        self.assertFalse(agentask.interactive("codex", "nope"))
        self.claude_log([], session="empty", tail="")
        self.assertFalse(agentask.interactive("claude", "empty"))
        self.claude_log([said("no marker")], session="bare")
        self.assertFalse(agentask.interactive("claude", "bare"))
        self.claude_log([started("cli")], session="bare")
        self.assertTrue(agentask.interactive("claude", "bare"))  # not cached while inconclusive

    def test_unreadable_transcript_is_retried(self):
        self.claude_log([started("cli")])
        real = open

        def blocked(path, *a, **k):
            if str(path).endswith("S1.jsonl"):
                raise PermissionError(path)
            return real(path, *a, **k)
        with mock.patch("builtins.open", blocked):
            self.assertFalse(agentask.interactive("claude", "S1"))
        self.assertTrue(agentask.interactive("claude", "S1"))

    def test_incomplete_marker_line_is_retried(self):
        line = started("cli")
        path = self.claude_log([line[:20]], tail="")
        self.assertFalse(agentask.interactive("claude", "S1"))
        with open(path, "w") as f:
            f.write(line + "\n")
        self.assertTrue(agentask.interactive("claude", "S1"))
        first = meta()
        path = self.codex_log([])
        with open(path, "w") as f:
            f.write(first[:30])
        self.assertFalse(agentask.interactive("codex", "C1"))
        with open(path, "w") as f:
            f.write(first + "\n")
        self.assertTrue(agentask.interactive("codex", "C1"))

    def test_a_conclusive_answer_is_cached(self):
        path = self.claude_log([started("sdk-cli")])
        self.assertFalse(agentask.interactive("claude", "S1"))
        with open(path, "w") as f:
            f.write(started("cli") + "\n")
        self.assertFalse(agentask.interactive("claude", "S1"))


def marker(kind):
    return json.dumps({"type": "event_msg", "payload": {"type": kind}})


def noise(n=1):
    return json.dumps({"type": "event_msg", "payload": {"type": "token_count", "pad": "x" * n}})


class CodexTurnTest(LastWordsTest):
    def turn(self):
        return agentask.codex_turn("C1")

    def test_started_and_both_kinds_of_ended(self):
        path = self.codex_log([marker("task_started"), reply("working"), noise()])
        os.utime(path, (1000.0, 1000.0))
        self.assertEqual(self.turn(), {"marker": "started", "mtime": 1000.0})
        self.codex_log([marker("task_started"), done("ok"), noise()])
        self.assertEqual(self.turn()["marker"], "ended")
        self.codex_log([marker("task_started"), marker("turn_aborted")])
        self.assertEqual(self.turn()["marker"], "ended")

    def test_the_newest_marker_wins(self):
        self.codex_log([marker("task_started"), done("a"), marker("task_started"), reply("b")])
        self.assertEqual(self.turn()["marker"], "started")
        self.codex_log([marker("task_started"), marker("task_started"), done("c")])
        self.assertEqual(self.turn()["marker"], "ended")

    def test_bad_lines_are_skipped_and_a_partial_last_line_ignored(self):
        path = self.codex_log([marker("task_started"), "not json", json.dumps({"type": "event_msg", "payload": "x"}),
                               json.dumps([1, 2]), json.dumps({"type": "event_msg"})])
        with open(path, "a") as f:
            f.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})[:-1])  # still being written
        self.assertEqual(self.turn()["marker"], "started")

    def test_none_when_it_cannot_be_told(self):
        self.assertIsNone(self.turn())                                  # no rollout
        self.codex_log([reply("hi"), noise()])
        self.assertIsNone(self.turn())                                  # no marker
        self.codex_log([marker("task_started")])
        with mock.patch("builtins.open", side_effect=OSError("denied")):
            self.assertIsNone(self.turn())

    def test_reads_back_in_chunks_up_to_the_limit(self):
        with mock.patch.object(agentask, "TURN_CHUNK", 64), mock.patch.object(agentask, "TURN_SCAN", 2048):
            self.codex_log([done("x"), marker("task_started")] + [noise(10)] * 20)       # about 1,100 bytes back
            self.assertEqual(self.turn()["marker"], "started")
            self.codex_log([marker("task_started")] + [noise(10)] * 60)                  # past 2,048 bytes
            self.assertIsNone(self.turn())
            self.codex_log([noise(10)] * 60)                                              # large, no markers at all
            self.assertIsNone(self.turn())
            self.codex_log([marker("turn_aborted"), noise(200)])  # a line longer than a chunk is joined whole
            self.assertEqual(self.turn()["marker"], "ended")
            self.codex_log([noise(200), marker("task_started")])
            self.assertEqual(self.turn()["marker"], "started")

    def test_the_newest_of_several_rollouts(self):
        old = self.codex_log([marker("task_started")])
        os.utime(old, (100.0, 100.0))
        new = os.path.join(self.tmp, "codex", "sessions", "2026", "10", "08", "rollout-2026-10-08T10-00-00-C1.jsonl")
        os.makedirs(os.path.dirname(new))
        with open(new, "w") as f:
            f.write(done("x") + "\n")
        os.utime(new, (200.0, 200.0))
        self.assertEqual(self.turn(), {"marker": "ended", "mtime": 200.0})


if __name__ == "__main__":
    unittest.main()
