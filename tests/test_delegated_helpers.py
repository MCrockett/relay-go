import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from relaylib import events, sessions
from tests import helpers


class HelpersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="relaytest-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.work = helpers.make_repo(self.tmp)
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "relayhome")})
        p.start()
        self.addCleanup(p.stop)

    def push(self):
        helpers.write(os.path.join(self.work, "x.txt"), "x\n")
        helpers.sh(self.work, "git", "add", "x.txt")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "x")
        return subprocess.run(["git", "push", "origin", "develop"], cwd=self.work, capture_output=True, text=True)

    def test_fake_clock_advances_on_sleep(self):
        clock = helpers.FakeClock()
        clock.sleep(2)
        clock.sleep(2)
        self.assertEqual((clock.monotonic(), clock.sleeps), (4.0, [2, 2]))

    def test_post_receive_marker_appears_on_push(self):
        marker = os.path.join(self.tmp, "marker")
        helpers.mark_pushes(self.origin, marker)
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(self.push().returncode, 0)
        self.assertTrue(os.path.exists(marker))

    def test_reject_pushes_refuses_with_text(self):
        helpers.reject_pushes(self.origin, "only pull requests")
        p = self.push()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("only pull requests", p.stderr)

    def test_fake_gh_serves_the_sequence_only_after_the_marker(self):
        marker, seq = os.path.join(self.tmp, "marker"), os.path.join(self.tmp, "seq.json")
        info = os.path.join(self.tmp, "pr.json")
        helpers.write(info, json.dumps({"number": 7, "state": "OPEN"}))
        helpers.write(seq, json.dumps([{"state": "OPEN"}, {"state": "MERGED"}, {"__rc": 1}]))
        gh = helpers.fake_bin(self.tmp, "fake-gh", helpers.FAKE_GH)
        env = dict(os.environ, FAKE_GH_JSON=info, FAKE_GH_AFTER_PUSH=seq, FAKE_GH_PUSHED=marker)

        def view():
            p = subprocess.run([gh, "pr", "view", "7"], env=env, capture_output=True, text=True)
            return p.returncode, (json.loads(p.stdout)["state"] if p.stdout else p.stderr)
        self.assertEqual(view(), (0, "OPEN"))
        self.assertFalse(os.path.exists(seq + ".n"))  # the sequence is not touched before the marker
        open(marker, "w").close()
        self.assertEqual([view()[1] for _ in range(2)], ["OPEN", "MERGED"])
        self.assertEqual(view(), (1, "gh: could not reach GitHub\n"))  # the last entry repeats
        self.assertEqual(view()[0], 1)

    def test_live_session_is_live_for_events(self):
        self.assertIsNone(events._inbox("claude", "s1"))
        record = helpers.live_session("claude", "s1")
        self.assertTrue(os.path.exists(sessions.record_path("claude", "s1")))
        self.assertTrue(sessions._valid(record))
        self.assertEqual(helpers.live_session("claude", "s2", state="ended")["state"], "ended")
