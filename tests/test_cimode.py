import os, tempfile, unittest
from unittest import mock
from relaylib import cimode, gitops
from relaylib.errors import RelayError
from tests import helpers
from tests.helpers import sh, write


class CimodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.glob = os.path.join(self.tmp, "global.toml")
        p = mock.patch.dict(os.environ, {"RELAY_CONFIG": self.glob, "RELAY_HOME": self.tmp})
        p.start()
        self.addCleanup(p.stop)
        self.origin, self.work = helpers.make_repo(self.tmp)

    def push_base_config(self, text):
        write(os.path.join(self.work, "docs/relay/config.toml"), text)
        sh(self.work, "git", "add", "-A")
        sh(self.work, "git", "commit", "-q", "-m", "config")
        sh(self.work, "git", "push", "-q", "origin", "develop")

    def test_release_resolves_per_base(self):
        self.assertEqual(cimode.resolve("release", "main"), "pr")
        self.assertEqual(cimode.resolve("release", "develop"), "local")
        for m in ("pr", "local", "none"):
            self.assertEqual(cimode.resolve(m, "main"), m)

    def test_settings_read_base_not_worktree(self):
        self.push_base_config('[build]\nci = "local"\ntest_command = "true"\n')
        feature = helpers.clone(self.origin, os.path.join(self.tmp, "feature"))
        sh(feature, "git", "checkout", "-q", "-b", "feat/x")
        write(os.path.join(feature, "docs/relay/config.toml"), '[build]\nci = "none"\ntest_command = "false"\n')
        sh(feature, "git", "add", "-A")
        sh(feature, "git", "commit", "-q", "-m", "weaken")
        for root in (feature, self.work):
            s = cimode.settings(root, "develop")
            self.assertEqual((s.mode, s.test_command, s.timeout_min), ("local", "true", 60))

    def test_settings_global_only_when_base_has_no_config(self):
        write(self.glob, '[build]\nci = "release"\n')
        self.assertEqual(cimode.settings(self.work, "develop").mode, "local")
        self.assertEqual(cimode.settings(self.work, "develop").configured, "release")
        sh(self.work, "git", "push", "-q", "origin", "develop:main")
        sh(self.work, "git", "fetch", "-q", "origin")
        self.assertEqual(cimode.settings(self.work, "main").mode, "pr")

    def test_invalid_base_config_names_ref(self):
        self.push_base_config('[build]\nci = "x"\n')
        with self.assertRaisesRegex(RelayError, "origin/develop:docs/relay/config.toml"):
            cimode.settings(self.work, "develop")

    def test_require_command_message(self):
        s = cimode.settings(self.work, "develop")
        cimode.require_command(s)  # pr mode: fine
        s = cimode.Settings("local", "local", " ", 60, "develop")
        with self.assertRaises(RelayError) as cm:
            cimode.require_command(s)
        self.assertEqual(str(cm.exception),
                         'set [build] test_command in docs/relay/config.toml on develop, or set ci = "none"')

    def test_workflow_warning_scoped_to_pull_request(self):
        def warns(text, base="develop"):
            write(os.path.join(self.work, ".github/workflows/w.yml"), text)
            sh(self.work, "git", "add", "-A")
            sh(self.work, "git", "commit", "-q", "-m", "wf")
            return cimode.workflow_warnings(self.work, "HEAD", base)
        own = "name: test\non:\n  pull_request:\n    types: [opened]\n  push:\n    branches: [main]\njobs:\n  u:\n    runs-on: x\n"
        out = warns(own)
        self.assertEqual(out, [".github/workflows/w.yml: runs on pull_request to develop; "
                               "with ci = local that spends GitHub minutes"])
        self.assertEqual(warns("on:\n  pull_request:\n    branches: [main]\n"), [])
        self.assertEqual(len(warns("on:\n  pull_request:\n    branches: [develop]\n")), 1)
        self.assertEqual(len(warns("on:\n  pull_request:\n    branches:\n      - develop\n")), 1)
        self.assertEqual(len(warns("on: [push, pull_request]\n")), 1)
        self.assertEqual(warns("on:\n  push:\n    branches: [develop]\n"), [])
        self.assertEqual(warns("on:\n  push:\n    branches: [main]\n  pull_request_target:\n"), [])


if __name__ == "__main__":
    unittest.main()
