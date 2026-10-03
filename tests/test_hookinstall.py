import json
import os
import tempfile
import unittest
from unittest import mock

from relaylib import hookinstall, sessions
from relaylib.errors import RelayError

CLAUDE_CMD = "~/.local/bin/relay hook claude"


class InstallTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = temp.name
        patch = mock.patch.dict(os.environ, {"HOME": self.home, "RELAY_HOME": os.path.join(self.home, "relay")})
        patch.start()
        self.addCleanup(patch.stop)

    def test_first_install_creates_both_files_without_backups(self):
        lines = hookinstall.install(stamp="20261001T000000Z")
        claude = json.load(open(hookinstall.path_for("claude")))
        self.assertEqual(sorted(claude["hooks"]), sorted(e for e, _ in hookinstall.EVENTS["claude"]))
        idle = claude["hooks"]["Notification"][0]
        self.assertEqual((idle["matcher"], idle["hooks"][0]["command"]), ("idle_prompt", CLAUDE_CMD))
        self.assertEqual(claude["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"], 3)
        self.assertEqual(claude["hooks"]["Stop"][0]["hooks"][0]["timeout"], 5)
        codex = json.load(open(hookinstall.path_for("codex")))
        self.assertEqual(codex["hooks"]["Interrupt"][0]["hooks"][0]["timeout"], 3)
        self.assertFalse([n for n in os.listdir(os.path.dirname(hookinstall.path_for("claude"))) if "backup" in n])
        self.assertIn("Codex runs new hooks only after you trust them: open Codex and review them with /hooks.", lines)

    def test_install_keeps_existing_hooks_and_is_idempotent(self):
        path = hookinstall.path_for("claude")
        os.makedirs(os.path.dirname(path))
        mine = {"model": "x", "statusLine": {"type": "command", "command": "mine"},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}
        json.dump(mine, open(path, "w"))
        added = hookinstall.install_file("claude", path, "20261001T000000Z")
        self.assertIn("Stop", added)
        data = json.load(open(path))
        self.assertEqual((data["model"], data["statusLine"]), ("x", mine["statusLine"]))
        self.assertEqual([h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]], ["say done", CLAUDE_CMD])
        self.assertTrue(os.path.exists(path + ".relay-backup-20261001T000000Z"))
        self.assertEqual(hookinstall.install_file("claude", path, "20261001T000001Z"), [])   # nothing new
        self.assertEqual(sorted(hookinstall.configured("claude")), sorted(e for e, _ in hookinstall.EVENTS["claude"]))

    def test_install_refuses_bad_files_and_keeps_going(self):
        path = hookinstall.path_for("claude")
        os.makedirs(os.path.dirname(path))
        for text in ("{not json", "[]", json.dumps({"hooks": []}), json.dumps({"hooks": {"Stop": "x"}})):
            with open(path, "w") as f:
                f.write(text)
            with self.assertRaises(RelayError):
                hookinstall.install_file("claude", path, "s")
            self.assertEqual(open(path).read(), text)
        lines = hookinstall.install(stamp="s")
        self.assertTrue(any(path in line and "refused" in line for line in lines))
        self.assertTrue(os.path.exists(hookinstall.path_for("codex")))         # the other file still installs

    def test_a_file_changed_during_install_is_refused(self):
        path = hookinstall.path_for("claude")
        os.makedirs(os.path.dirname(path))
        json.dump({}, open(path, "w"))
        real_copy = hookinstall.shutil.copy2
        def copy_then_edit(src, dst):
            real_copy(src, dst)
            json.dump({"edited": True}, open(path, "w"))
        with mock.patch.object(hookinstall.shutil, "copy2", side_effect=copy_then_edit):
            with self.assertRaisesRegex(RelayError, "changed while installing"):
                hookinstall.install_file("claude", path, "s")
        self.assertEqual(json.load(open(path)), {"edited": True})

    def test_status_separates_configured_from_events_seen(self):
        lines = hookinstall.status_lines()
        self.assertTrue(any(l.startswith("claude: configured none; missing PermissionRequest") and
                            l.endswith("no events seen yet") for l in lines))
        path = hookinstall.path_for("claude")
        os.makedirs(os.path.dirname(path))
        json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": CLAUDE_CMD}]}]}}, open(path, "w"))
        claude = next(l for l in hookinstall.status_lines() if l.startswith("claude:"))
        self.assertTrue(claude.startswith("claude: configured Stop; missing PermissionRequest, Notification"))
        hookinstall.install(stamp="s")
        import io
        sessions.capture("codex", io.StringIO(json.dumps({"session_id": "a", "hook_event_name": "Stop"})))
        codex = next(l for l in hookinstall.status_lines() if l.startswith("codex:"))
        self.assertNotIn("missing", codex)
        self.assertIn("last event", codex)

    def test_unreadable_files_and_failed_writes_are_refused_per_file(self):
        path = hookinstall.path_for("claude")
        os.makedirs(os.path.dirname(path))
        json.dump({"model": "x"}, open(path, "w"))
        for target, error, reason in ((hookinstall.shutil, "copy2", "cannot back up"),
                                      (hookinstall.tempfile, "mkstemp", "cannot write")):
            with mock.patch.object(target, error, side_effect=OSError("disk full")):
                with self.assertRaisesRegex(RelayError, reason):
                    hookinstall.install_file("claude", path, "s")
            self.assertEqual(json.load(open(path)), {"model": "x"})
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o600)
        self.assertEqual(hookinstall.configured("claude"), [])           # the dashboard never sees an error
        lines = hookinstall.install(stamp="s")
        self.assertTrue(any(line.startswith("claude: refused") and "cannot read" in line for line in lines))
        self.assertTrue(os.path.exists(hookinstall.path_for("codex")))

    def test_an_edit_during_the_write_is_not_overwritten(self):
        path = hookinstall.path_for("claude")
        os.makedirs(os.path.dirname(path))
        json.dump({}, open(path, "w"))
        real_dump = hookinstall.json.dump
        def dump_then_edit(data, f, **kw):
            real_dump(data, f, **kw)
            with open(path, "w") as g:
                g.write('{"edited": true}')
        with mock.patch.object(hookinstall.json, "dump", side_effect=dump_then_edit):
            with self.assertRaisesRegex(RelayError, "changed while installing"):
                hookinstall.install_file("claude", path, "s")
        self.assertEqual(json.load(open(path)), {"edited": True})
        self.assertFalse([n for n in os.listdir(os.path.dirname(path)) if n.startswith(".relay-")])
