import json, os, unittest
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


if __name__ == "__main__":
    unittest.main()
