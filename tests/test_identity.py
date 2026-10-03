import unittest
from relaylib import identity
from relaylib.errors import RelayError


class IdentityTest(unittest.TestCase):
    def test_claude_only(self):
        me = identity.detect({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "c1"})
        self.assertEqual(me, identity.Identity("claude", "c1"))

    def test_codex_only_thread_id(self):
        self.assertEqual(identity.detect({"CODEX_THREAD_ID": "x9"}), identity.Identity("codex", "x9"))

    def test_both_markers_is_ambiguous(self):
        with self.assertRaisesRegex(RelayError, "both"):
            identity.detect({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "c", "CODEX_SESSION_ID": "x"})

    def test_no_markers_needs_by(self):
        with self.assertRaisesRegex(RelayError, "--by"):
            identity.detect({})

    def test_relay_provider_wins_over_inherited_markers(self):
        env = {"RELAY_PROVIDER": "codex", "RELAY_SESSION": "r1", "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "c"}
        self.assertEqual(identity.detect(env), identity.Identity("codex", "r1"))

    def test_by_owner(self):
        me = identity.detect({}, "owner")
        self.assertEqual(me.provider, "owner")
        self.assertTrue(me.session.startswith("owner@"))

    def test_by_rejects_unknown(self):
        with self.assertRaises(RelayError):
            identity.detect({}, "gemini")

    def test_require_owner_terminal(self):
        with self.assertRaisesRegex(RelayError, "owner-only"):
            identity.require_owner_terminal({"CODEX_THREAD_ID": "x"}, "relay override go")
        identity.require_owner_terminal({"PATH": "/bin"}, "relay override go")

    def test_child_env_strips_parent_markers(self):
        parent = {"PATH": "/bin", "CODEX_HOME": "/c", "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "c",
                  "CLAUDE_CODE_ENTRYPOINT": "cli", "RELAY_PROVIDER": "claude", "RELAY_SESSION": "s1"}
        child = identity.child_env(parent, "codex")
        self.assertEqual(child["RELAY_PROVIDER"], "codex")
        self.assertTrue(child["RELAY_SESSION"].startswith("review-"))
        for gone in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT"):
            self.assertNotIn(gone, child)
        self.assertEqual(child["CODEX_HOME"], "/c")  # config location is kept
        self.assertEqual(identity.detect(child).provider, "codex")


if __name__ == "__main__":
    unittest.main()
