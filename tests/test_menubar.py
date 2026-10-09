import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import helpers

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("menubar", ROOT / "menubar/relay.30s.py")
menubar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(menubar)


class MenubarTest(unittest.TestCase):
    def test_empty_and_error(self):
        self.assertTrue(menubar.render([], None).startswith("relay ✓\n---\n"))
        with mock.patch.object(menubar.status, "scan", side_effect=RuntimeError("unreadable | repo")):
            output = menubar.output()
        self.assertIn("relay ?", output)
        self.assertIn("unreadable ¦ repo", output)
        self.assertIn("Open relay UI", output)

    def test_waiting_links_ui_pr_folder_and_non_github(self):
        row = {"repo": "project", "feature": "demo", "stage": "spec", "status": "waiting-owner",
               "flags": ["needs a decision"], "pr": 7, "waiting_on_owner": True, "checkout": "/repo"}
        with mock.patch.object(menubar.owneractions, "github_repo", return_value="owner/repo"), \
                mock.patch.object(menubar.owneractions, "published", return_value=("sha", {"branch": "feat/demo"})):
            output = menubar.render([row], {"port": 8765, "token": "token", "pid": os.getpid()})
            self.assertIn("relay 1", output)
            self.assertIn("#repo=%2Frepo&slug=demo", output)
            self.assertIn("needs a decision", output)
            self.assertIn("https://github.com/owner/repo/pull/7", menubar.render([row], None))
            row["pr"] = None
            self.assertIn("https://github.com/owner/repo/tree/feat/demo/docs/relay/demo", menubar.render([row], None))
        with mock.patch.object(menubar.owneractions, "github_repo", side_effect=menubar.RelayError("not GitHub")):
            self.assertNotIn("href=", menubar.render([row], None))

    def test_one_session_waiting_on_two_features_counts_once(self):
        def held(feature, updated):
            return {"repo": "project", "feature": feature, "stage": "spec", "status": "drafting", "flags": [],
                    "pr": None, "checkout": None, "updated": updated, "waiting_on_owner": True, "wait_since": 5.0,
                    "asks": [{"kind": "answer", "text": "Answer the claude session", "since": 5.0,
                              "session": "claude:S"}]}
        rows = [held("one", "2026-10-07T09:00:00-04:00"), held("two", "2026-10-07T10:00:00-04:00")]
        with mock.patch.object(menubar.status, "scan", return_value=rows), \
                mock.patch.object(menubar, "running_info", return_value=None):
            output = menubar.output()
        self.assertIn("relay 1\n", output)
        self.assertIn("project / two", output)
        self.assertNotIn("project / one", output)

    def test_stale_discovery_is_not_linked(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"RELAY_HOME": tmp}):
            helpers.write(os.path.join(tmp, "ui.json"), json.dumps({"pid": 99999999, "port": 8765, "token": "old"}))
            with mock.patch.object(menubar.status, "scan", return_value=[]):
                self.assertNotIn("?t=old", menubar.output())

    def test_install_links_plugin_or_explains_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            plugin_dir = os.path.join(tmp, "SwiftBar plugins")
            os.makedirs(plugin_dir)
            helpers.fake_bin(bin_dir, "defaults", '#!/bin/sh\n[ -n "$PLUGIN_TEST_DIR" ] || exit 1\nprintf "%s\\n" "$PLUGIN_TEST_DIR"\n')
            env = {**os.environ, "HOME": tmp, "PATH": bin_dir + os.pathsep + os.environ["PATH"], "PLUGIN_TEST_DIR": plugin_dir}
            run = subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue(os.path.islink(os.path.join(plugin_dir, "relay.30s.py")))
            self.assertIn('printf', run.stdout)
            env["PLUGIN_TEST_DIR"] = ""
            run = subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True)
            self.assertIn("PluginDirectory", run.stdout)

    @unittest.skipUnless(shutil.which("jq"), "the owner's status line uses jq")
    def test_status_line_output_is_unchanged(self):
        command = (ROOT / "tests/fixtures/status-line.sh").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            helpers.fake_bin(os.path.join(tmp, ".local/bin"), "relay", '#!/bin/sh\ncat >/dev/null\n')
            payload = json.dumps({"workspace": {"current_dir": tmp}, "model": {"display_name": "Claude"},
                                  "context_window": {"current_usage": {"input_tokens": 10, "output_tokens": 2,
                                   "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}, "context_window_size": 100}})
            snippet = 'printf \'%s\' "$input" | ~/.local/bin/relay usage-snapshot claude >/dev/null 2>&1 &'
            original = subprocess.run(["bash", "-c", command], input=payload, text=True, capture_output=True,
                                      env={**os.environ, "HOME": tmp})
            extended = subprocess.run(["bash", "-c", command.replace('input=$(cat);', 'input=$(cat); '+snippet)],
                                      input=payload, text=True, capture_output=True, env={**os.environ, "HOME": tmp})
            self.assertEqual(original.returncode, 0, original.stderr)
            self.assertEqual((extended.returncode, extended.stdout, extended.stderr),
                             (original.returncode, original.stdout, original.stderr))
