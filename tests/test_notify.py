import os, tempfile, unittest
from unittest import mock
from relaylib import notify
from tests import helpers


class NotifyTest(unittest.TestCase):
    def test_a_failing_notifier_reports_false(self):
        tmp = tempfile.mkdtemp()
        failing = helpers.fake_bin(tmp, "osascript", "#!/bin/sh\nexit 1\n")
        with mock.patch.dict(os.environ, {"RELAY_NOTIFY_BIN": failing}):
            self.assertFalse(notify.send({}, "t", "m"))

    def test_quotes_are_escaped(self):
        tmp = tempfile.mkdtemp()
        log = os.path.join(tmp, "log")
        ok = helpers.fake_bin(tmp, "osascript", f'#!/bin/sh\nprintf "%s" "$2" > {log}\n')
        with mock.patch.dict(os.environ, {"RELAY_NOTIFY_BIN": ok}):
            self.assertTrue(notify.send({}, 'say "hi"', 'a\\b'))
        with open(log) as f:
            self.assertIn('say \\"hi\\"', f.read())


if __name__ == "__main__":
    unittest.main()
