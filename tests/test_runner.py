import os, shutil, tempfile, time, unittest
from unittest import mock
from relaylib import runner
from relaylib.config import ModelSpec
from tests import helpers

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
CODEX = ModelSpec("codex", "gpt-6-astra", "high")
CLAUDE = ModelSpec("claude", "claude-sonnet-5", None)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.queue = os.path.join(self.tmp, "q")
        os.makedirs(self.queue)
        self.fake = helpers.fake_bin(self.tmp, "fake", helpers.FAKE_REVIEWER)
        self.env = {"PATH": os.environ["PATH"], "FAKE_OUT": self.queue, "RELAY_CODEX_BIN": self.fake,
                    "RELAY_CLAUDE_BIN": self.fake}
        self.saved = dict(os.environ)
        os.environ.update(self.env)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.saved)))

    def enqueue(self, text):
        helpers.write(os.path.join(self.queue, f"{len(os.listdir(self.queue)):03d}"), text)

    def test_codex_command(self):
        cmd = runner.build_command(CODEX, "PROMPT", "/repo")
        self.assertEqual(cmd[1:], ["exec", "--sandbox", "read-only", "--json", "-C", "/repo", "-m", "gpt-6-astra",
                                   "-c", "model_reasoning_effort=high", "PROMPT"])

    def test_claude_command_is_read_only(self):
        cmd = runner.build_command(CLAUDE, "PROMPT", "/repo")
        self.assertEqual(cmd[1:3], ["-p", "PROMPT"])
        self.assertIn("--output-format", cmd)
        tools = cmd[cmd.index("--allowedTools") + 1]
        self.assertNotIn("Write", tools)
        self.assertNotIn("Edit", tools)

    def test_extract_real_fixtures(self):
        text, usage, err = runner.extract_codex(open(os.path.join(FIXTURES, "codex_review.jsonl")).read())
        self.assertEqual(err, "")
        self.assertIn("verdict:", text)
        self.assertGreater(usage["input"], 0)
        text, usage, err = runner.extract_claude(open(os.path.join(FIXTURES, "claude_review.json")).read())
        self.assertEqual(err, "")
        self.assertIn("verdict:", text)

    def test_extract_codex_takes_final_message_only(self):
        text, _, _ = runner.extract_codex(helpers.codex_output("FINAL"))
        self.assertEqual(text, "FINAL")

    def test_extract_codex_failures(self):
        self.assertIn("did not complete", runner.extract_codex('{"type":"turn.started"}')[2])
        self.assertIn("codex reported", runner.extract_codex('{"type":"turn.failed","error":{"message":"auth"}}')[2])

    def test_extract_claude_error(self):
        self.assertIn("error", runner.extract_claude(helpers.claude_output("boom", is_error=True))[2])
        self.assertIn("not JSON", runner.extract_claude("oops")[2])

    def test_run_success(self):
        self.enqueue(helpers.codex_output("DONE"))
        res = runner.run_review(CODEX, "p", self.tmp, dict(os.environ), 10)
        self.assertTrue(res.ok, res.error)
        self.assertEqual((res.text, res.usage["cached"]), ("DONE", 800))

    def test_run_nonzero_exit(self):
        self.enqueue(helpers.codex_output("DONE"))
        env = dict(os.environ, FAKE_RC="3")
        res = runner.run_review(CODEX, "p", self.tmp, env, 10)
        self.assertFalse(res.ok)
        self.assertIn("exit code 3", res.error)

    def test_run_timeout_kills(self):
        self.enqueue(helpers.codex_output("LATE"))
        env = dict(os.environ, FAKE_SLEEP="30")
        res = runner.run_review(CODEX, "p", self.tmp, env, 1)
        self.assertTrue(res.timed_out)
        self.assertLess(res.duration_s, 15)

    def test_timeout_kills_grandchildren_too(self):
        self.enqueue(helpers.codex_output("LATE"))
        env = dict(os.environ, FAKE_SLEEP="30", FAKE_GRANDCHILD="1")
        res = runner.run_review(CODEX, "p", self.tmp, env, 1)
        self.assertTrue(res.timed_out)
        self.assertLess(res.duration_s, 15)

    def test_term_resistant_descendant_is_killed(self):
        pidfile = os.path.join(self.tmp, "stubborn.pid")
        self.enqueue(helpers.codex_output("LATE"))
        env = dict(os.environ, FAKE_SLEEP="30", FAKE_STUBBORN=pidfile)
        res = runner.run_review(CODEX, "p", self.tmp, env, 1)
        self.assertTrue(res.timed_out)
        with open(pidfile) as f:
            pid = int(f.read())
        for _ in range(60):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("a descendant that ignores TERM survived")

    def test_interrupt_stops_reviewer_and_reports(self):
        self.enqueue(helpers.codex_output("LATE"))
        env = dict(os.environ, FAKE_SLEEP="30")
        with mock.patch("subprocess.Popen.communicate", side_effect=[KeyboardInterrupt(), ("", "")]), \
                mock.patch("relaylib.runner._kill_group") as killed:
            res = runner.run_review(CODEX, "p", self.tmp, env, 60)
        self.assertEqual(res.error, "interrupted by the user")
        killed.assert_called_once()
        os.killpg(killed.call_args[0][0].pid, 9)  # the real fake is still asleep; clean it up

    def test_missing_binary(self):
        os.environ["RELAY_CODEX_BIN"] = os.path.join(self.tmp, "nope")
        res = runner.run_review(CODEX, "p", self.tmp, dict(os.environ), 5)
        self.assertIn("not found", res.error)


    def test_claude_reviewers_get_their_effort(self):
        cmd = runner.build_command(ModelSpec("claude", "claude-fable-5-1", "medium"), "PROMPT", "/repo")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "medium")
        self.assertNotIn("--effort", runner.build_command(CLAUDE, "PROMPT", "/repo"))

if __name__ == "__main__":
    unittest.main()
