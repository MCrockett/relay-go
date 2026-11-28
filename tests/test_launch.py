import os
import tempfile
import time
import unittest
from unittest import mock

from relaylib import gitops, launch
from tests import helpers


class LaunchTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.tmp = os.path.realpath(temp.name)
        self.log = os.path.join(self.tmp, "claude.log")
        self.bin = helpers.fake_claude(self.tmp)
        self.cwd = os.path.join(self.tmp, "wt")
        os.makedirs(self.cwd)
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "h"), "FAKE_LOG": self.log,
                                         "PATH": self.bin + os.pathsep + os.environ["PATH"]})
        p.start()
        self.addCleanup(p.stop)

    def env(self, **values):
        p = mock.patch.dict(os.environ, values)
        p.start()
        self.addCleanup(p.stop)

    def test_argv_has_no_bypass_flag(self):  # R5
        args = launch.argv("relay-go x", "u", "p")
        self.assertEqual(args, ["claude", "--bg", "--remote-control", "relay-go x", "--session-id", "u", "p"])
        self.assertFalse(set(args) & set(launch.BYPASS))

    def test_run_ok_in_cwd(self):
        self.assertEqual(launch.run(launch.argv("n", "u", "p"), self.cwd, "u"), ("ok", None))
        [call] = helpers.fake_claude_calls(self.log)
        self.assertEqual(call["argv"], ["--bg", "--remote-control", "n", "--session-id", "u", "p"])
        self.assertEqual(call["cwd"], self.cwd)

    def test_run_failed_first_line(self):  # R9
        self.env(FAKE_EXIT="3", FAKE_ERR="\nnot logged in\nmore")
        self.assertEqual(launch.run(launch.argv("n", "u", "p"), self.cwd, "u"), ("failed", "not logged in"))

    def test_run_failed_no_output(self):  # R9
        self.env(FAKE_EXIT="3")
        self.assertEqual(launch.run(launch.argv("n", "u", "p"), self.cwd, "u"),
                         ("failed", "claude --bg exited with code 3"))

    def test_run_oserror(self):  # R9
        self.env(PATH="/nonexistent")
        outcome, reason = launch.run(launch.argv("n", "u", "p"), self.cwd, "u")
        self.assertEqual(outcome, "failed")
        self.assertTrue(reason.startswith("cannot run claude:"), reason)

    def test_unconfirmed_leaves_process_running(self):  # R9a
        self.env(FAKE_SLEEP="5")
        started = time.time()
        with mock.patch.object(launch, "LAUNCH_WAIT_S", 1):
            self.assertEqual(launch.run(launch.argv("n", "u", "p"), self.cwd, "u"), ("unconfirmed", None))
        self.assertLess(time.time() - started, 3)
        [call] = helpers.fake_claude_calls(self.log)
        os.kill(call["pid"], 0)  # still running: raises if it is gone
        self.addCleanup(lambda: _kill(call["pid"]))

    def test_prompts(self):
        start = launch.start_prompt("q1", "s")
        self.assertIn("relay dispatch --show q1", start)
        self.assertIn("relay take --request q1 --slug s", start)
        self.assertIn("The owner merges.", start)
        clear = launch.clear_prompt("old", ["a", "b"])
        self.assertIn("session old", clear)
        self.assertIn("a, b", clear)
        self.assertIn("held: none.", launch.clear_prompt("old", []))
        for text in (start, clear):
            self.assertNotIn("—", text)

    def test_remove_if_pristine(self):
        os.makedirs(os.path.join(self.tmp, "r"))
        _, work = helpers.make_repo(os.path.join(self.tmp, "r"))
        main = work
        commit = gitops.head_sha(main, "origin/develop")
        clean = launch.worktree_path(main, "clean")
        self.assertEqual(launch.make_worktree(main, clean, "develop"), commit)
        self.assertTrue(launch.remove_if_pristine(main, clean, commit))
        self.assertFalse(os.path.exists(clean))
        dirty = launch.worktree_path(main, "dirty")
        launch.make_worktree(main, dirty, "develop")
        helpers.write(os.path.join(dirty, "new.txt"), "x")
        self.assertFalse(launch.remove_if_pristine(main, dirty, commit))
        self.assertTrue(os.path.isdir(dirty))
        moved = launch.worktree_path(main, "moved")
        launch.make_worktree(main, moved, "develop")
        helpers.write(os.path.join(moved, "f.txt"), "x")
        helpers.sh(moved, "git", "add", "f.txt")
        helpers.sh(moved, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
        self.assertFalse(launch.remove_if_pristine(main, moved, commit))
        self.assertTrue(os.path.isdir(moved))


def _kill(pid):
    try:
        os.kill(pid, 9)
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
