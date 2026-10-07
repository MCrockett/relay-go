import os, tempfile, unittest
from unittest import mock
from relaylib import config
from relaylib.errors import RelayError


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hub = os.path.join(self.tmp, "config.toml")
        with open(self.hub, "w") as f:
            f.write('[roles]\nbuild = "codex:gpt-6-astra"  # builder\n\n[roles.reviewer_models]\nclaude = "claude-sonnet-5"\n\n[limits]\nmax_rounds = 4\n')
        p = mock.patch.dict(os.environ, {"RELAY_CONFIG": self.hub, "RELAY_HOME": self.tmp})
        p.start()
        self.addCleanup(p.stop)

    def test_parse_full_spec(self):
        self.assertEqual(config.parse_model_spec("codex:gpt-6-astra@high"),
                         config.ModelSpec("codex", "gpt-6-astra", "high"))

    def test_parse_with_default_provider(self):
        self.assertEqual(config.parse_model_spec("claude-sonnet-5", "claude"),
                         config.ModelSpec("claude", "claude-sonnet-5", None))

    def test_parse_rejects_unknown_provider(self):
        with self.assertRaises(RelayError):
            config.parse_model_spec("gemini:pro")

    def test_load_merges_defaults_hub_and_repo(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, "docs", "relay"))
        with open(os.path.join(repo, "docs", "relay", "config.toml"), "w") as f:
            f.write("[limits]\nmax_rounds = 3\n")
        cfg = config.load(repo)
        self.assertEqual(cfg["roles"]["build"], "codex:gpt-6-astra")
        self.assertEqual(cfg["roles"]["spec"], "claude:claude-opus-5-5")  # default kept
        self.assertEqual(cfg["limits"]["max_rounds"], 3)                  # repo wins
        self.assertEqual(cfg["limits"]["handoff_context_pct"], 60)
        self.assertTrue(cfg["build"]["require_ci"])

    def test_reviewer_model(self):
        self.assertEqual(config.reviewer_model(config.load(), "claude").model, "claude-sonnet-5")

    def test_set_role_replaces_line_and_keeps_others(self):
        config.set_role(self.hub, "build", "claude:claude-sonnet-5")
        text = open(self.hub).read()
        self.assertIn('build = "claude:claude-sonnet-5"', text)
        self.assertIn("max_rounds = 4", text)
        self.assertEqual(config.load()["roles"]["build"], "claude:claude-sonnet-5")

    def test_set_reviewer_model_and_missing_section(self):
        config.set_role(self.hub, "reviewer.codex", "gpt-6-astra@medium")
        self.assertEqual(config.reviewer_model(config.load(), "codex").effort, "medium")

    def test_set_role_rejects_unknown_key(self):
        with self.assertRaises(RelayError):
            config.set_role(self.hub, "deploy", "codex:x")


    def test_set_a_review_preference_list(self):
        config.set_role(self.hub, "review.claude", "codex:gpt-6-astra@high, claude:claude-fable-5-1")
        prefs = config.review_preferences(config.load(), "claude")
        self.assertEqual([(p.provider, p.model) for p in prefs],
                         [("codex", "gpt-6-astra"), ("claude", "claude-fable-5-1")])
        self.assertIn('claude = ["codex:gpt-6-astra@high", "claude:claude-fable-5-1"]', open(self.hub).read())

    def test_a_bad_review_preference_is_rejected(self):
        with self.assertRaises(RelayError):
            config.set_role(self.hub, "review.claude", "gemini:pro")
        with self.assertRaises(RelayError):
            config.set_role(self.hub, "review.nobody", "codex:gpt-6-astra")

    def test_owner_config_lives_in_relay_home(self):
        with mock.patch.dict(os.environ, {"RELAY_HOME": self.tmp}):
            os.environ.pop("RELAY_CONFIG")
            self.assertEqual(config.config_path(), os.path.join(self.tmp, "config.toml"))
        target = os.path.join(self.tmp, "new", "config.toml")                 # set_role creates it
        config.set_role(target, "reviewer.claude", "claude-sonnet-5")
        self.assertIn('claude = "claude-sonnet-5"', open(target).read())

    def test_projects_root_env_then_config_then_default(self):
        from relaylib import status
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RELAY_ROOT", None)
            self.assertEqual(status.projects_root(), os.path.expanduser("~/Projects"))
            with open(self.hub, "a") as f:
                f.write('\n[projects]\nroot = "~/Code"\n')
            self.assertEqual(status.projects_root(), os.path.expanduser("~/Code"))
            os.environ["RELAY_ROOT"] = "/somewhere"
            self.assertEqual(status.projects_root(), "/somewhere")

    def test_editing_the_shipped_config_keeps_it_valid(self):
        import shutil
        shutil.copy(os.path.join(config.RELAY_HOME_DIR, "config.example.toml"), self.hub)
        config.set_role(self.hub, "review.claude", "claude:claude-fable-5-1")
        config.set_role(self.hub, "reviewer.claude", "claude-opus-5-5")
        cfg = config.load()  # would raise TOMLDecodeError on a duplicated table
        self.assertEqual([p.model for p in config.review_preferences(cfg, "claude")], ["claude-fable-5-1"])
        self.assertEqual(config.reviewer_model(cfg, "claude").model, "claude-opus-5-5")
        text = open(self.hub).read()
        self.assertEqual(text.count("[review.prefer]"), 1)
        self.assertEqual(text.count("[roles.reviewer_models]"), 1)

    def test_the_old_review_role_is_gone(self):
        with self.assertRaisesRegex(RelayError, "review.<author>"):
            config.set_role(self.hub, "review", "auto")

    def test_sonnet_is_the_first_claude_fallback(self):
        os.environ["RELAY_CONFIG"] = os.path.join(self.tmp, "none.toml")      # built-in defaults only
        self.assertEqual([f"{p.provider}:{p.model}" for p in config.review_preferences(config.load(), "claude")],
                         ["codex:gpt-6-astra", "claude:claude-sonnet-5", "claude:claude-fable-5-1"])

    def test_validate_reviewers_one_rule_set(self):
        self.assertEqual(config.validate_reviewers([" codex:gpt-6-astra@xhigh", "", "claude:claude-fable-5-1"]),
                         ["codex:gpt-6-astra@xhigh", "claude:claude-fable-5-1"])
        for bad, why in ((["gemini:pro"], "provider"), (["codex:"], "empty"), ([], "at least one"),
                         (["codex:a@High"], "lowercase"), (["codex:a@very high"], "lowercase"),
                         (["codex:a@low", "codex:a@high"], "twice")):
            with self.subTest(bad=bad), self.assertRaisesRegex(RelayError, why):
                config.validate_reviewers(bad)

    def test_set_role_refuses_duplicates_but_load_stays_lenient(self):
        with open(self.hub, "a") as f:
            f.write('\n[review.prefer]\nclaude = ["codex:a@High", "codex:a"]\n')
        self.assertEqual([p.model for p in config.review_preferences(config.load(), "claude")], ["a", "a"])
        with self.assertRaisesRegex(RelayError, "twice"):
            config.set_role(self.hub, "review.claude", "codex:a, codex:a@high")

    def test_set_role_is_atomic(self):
        before = open(self.hub).read()
        with mock.patch("os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            config.set_role(self.hub, "build", "claude:claude-sonnet-5")
        self.assertEqual(open(self.hub).read(), before)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith(".relay-")], [])

    def test_set_role_keeps_a_symlinked_config(self):
        link = os.path.join(self.tmp, "linked.toml")
        os.symlink(self.hub, link)
        config.set_role(link, "build", "claude:claude-sonnet-5")
        self.assertTrue(os.path.islink(link))
        self.assertIn("claude-sonnet-5", open(self.hub).read())

    def test_set_role_waits_for_the_lock_then_refuses(self):
        import fcntl
        with mock.patch.object(config, "LOCK_TIMEOUT_S", 0.05), \
                open(os.path.join(self.tmp, "roles.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(RelayError, "busy, try again"):
                config.set_role(self.hub, "build", "claude:claude-lock-test")
        self.assertNotIn("claude-lock-test", open(self.hub).read())

    def test_named_locks_are_separate(self):
        import fcntl
        with mock.patch.object(config, "LOCK_TIMEOUT_S", 0.05), \
                open(os.path.join(self.tmp, "writer-usage.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with config.write_lock():
                pass
            with self.assertRaisesRegex(RelayError, "busy, try again"):
                with config.write_lock(name="writer-usage.lock"):
                    pass

if __name__ == "__main__":
    unittest.main()
