import unittest
from relaylib import prompts


class PromptsTest(unittest.TestCase):
    def test_spec_prompt(self):
        text = prompts.render("spec", {"feature_dir": "docs/relay/x", "round": 2, "prior_ids": "R1-1, R1-2"})
        self.assertIn("docs/relay/x", text)
        self.assertIn("R1-1, R1-2", text)
        self.assertIn("[introduced-by-revision]", text)
        self.assertIn("verdict: GO", text)
        self.assertNotIn("$feature_dir", text)

    def test_every_prompt_renders(self):
        ctx = {"feature_dir": "d", "round": 1, "prior_ids": "none", "pr": 3, "base_ref": "origin/develop",
               "base_sha": "a", "head_sha": "b", "small": "no"}
        for name in ("spec", "plan", "build", "release"):
            self.assertNotIn("$", prompts.render(name, ctx).split("## Owner rules")[0], name)

    def test_release_review_checks_submission_declarations_build_numbers_and_runbook(self):
        ctx = {"feature_dir": "d", "pr": 3, "base_ref": "origin/main", "base_sha": "a", "head_sha": "b"}
        text = prompts.render("release", ctx).split("## Owner rules")[0]
        for piece in ("privacy manifests", "export compliance", "only go up", "runbook"):
            self.assertIn(piece, text)


class RulesTest(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        from unittest import mock
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home, self.repo = os.path.join(temp.name, "home"), os.path.join(temp.name, "repo")
        os.makedirs(os.path.join(self.repo, "docs", "relay"))
        os.makedirs(self.home)
        patch = mock.patch.dict(os.environ, {"RELAY_HOME": self.home})
        patch.start()
        self.addCleanup(patch.stop)
        self.ctx = {"feature_dir": "d", "round": 1, "prior_ids": "none"}

    def write(self, path, text):
        with open(path, "w") as f:
            f.write(text)

    def test_global_rules_come_first_then_the_projects(self):
        import os
        self.write(prompts.global_rules_path(), "# Owner rules\n\n- global one\n")
        self.write(prompts.project_rules_path(self.repo), "# Project rules\n\n- project one\n")
        text = prompts.render("spec", self.ctx, root=self.repo)
        self.assertIn("## Owner rules (apply these)", text)
        self.assertLess(text.index("- global one"), text.index("- project one"))
        self.assertNotIn("- project one", prompts.render("spec", self.ctx))     # no project: global only
        self.assertEqual(prompts.project_rules_path(self.repo), os.path.join(self.repo, "docs", "relay", "RULES.md"))

    def test_no_rules_no_section(self):
        self.assertNotIn("Owner rules", prompts.render("spec", self.ctx, root=self.repo))
        self.assertEqual(prompts.rules_text(self.repo), "")


if __name__ == "__main__":
    unittest.main()
