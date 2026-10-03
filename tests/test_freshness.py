import os, shutil, tempfile, unittest
from relaylib import freshness, state
from tests import helpers


class FreshnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.work = helpers.make_repo(self.tmp)
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/f")
        self.dir = state.feature_dir(self.work, "f")
        helpers.write(os.path.join(self.dir, "spec.md"), "spec v1\n")
        helpers.write(os.path.join(self.dir, "plan.md"), "plan v1\n")
        self.st = state.new_state("f", "r", {}, "feat/f")

    def commit_all(self, msg):
        helpers.sh(self.work, "git", "add", "-A")
        helpers.sh(self.work, "git", "commit", "-q", "-m", msg)

    def build_go(self):
        helpers.write(os.path.join(self.work, "app.py"), "print(1)\n")
        self.commit_all("code")
        helpers.sh(self.work, "git", "push", "-q", "-u", "origin", "feat/f")
        self.st["stage"] = "build"
        self.st["reviewed"]["build"] = freshness.snapshot(self.work, self.st, "origin/develop")

    def test_spec_fresh_then_stale(self):
        self.st["stage"] = "spec"
        self.st["reviewed"]["spec"] = freshness.snapshot(self.work, self.st)
        self.assertTrue(freshness.check(self.work, self.st, "spec")[0])
        helpers.write(os.path.join(self.dir, "spec.md"), "spec v2\n")
        self.assertEqual(freshness.check(self.work, self.st, "spec"), (False, "spec.md changed since the GO"))

    def test_plan_binds_spec(self):
        self.st["stage"] = "plan"
        self.st["reviewed"]["plan"] = freshness.snapshot(self.work, self.st)
        helpers.write(os.path.join(self.dir, "spec.md"), "spec v2\n")
        self.assertFalse(freshness.check(self.work, self.st, "plan")[0])

    def test_build_ignores_bookkeeping_commits(self):
        self.build_go()
        helpers.write(os.path.join(self.dir, "reviews", "build-1.codex.md"), "GO\n")
        helpers.write(os.path.join(self.dir, "state.md"), "---\n{}\n---\n")
        self.commit_all("relay: build GO (codex)")
        self.assertEqual(freshness.check(self.work, self.st, "build"), (True, "code and merge result unchanged"))

    def test_build_stale_on_code_change(self):
        self.build_go()
        helpers.write(os.path.join(self.work, "app.py"), "print(2)\n")
        self.commit_all("more code")
        self.assertEqual(freshness.check(self.work, self.st, "build"), (False, "code changed since the GO"))

    def test_build_stale_when_base_moves(self):
        self.build_go()
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.write(os.path.join(other, "unrelated.txt"), "x\n")
        helpers.sh(other, "git", "add", "-A")
        helpers.sh(other, "git", "commit", "-q", "-m", "develop moves")
        helpers.sh(other, "git", "push", "-q", "origin", "develop")
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        fresh, why = freshness.check(self.work, self.st, "build")
        self.assertFalse(fresh)
        self.assertIn("merge result against origin/develop changed", why)

    def test_build_stale_when_pr_base_changes(self):
        self.build_go()
        self.assertEqual(freshness.check(self.work, self.st, "build", "origin/main")[0], False)

    def test_small_binds_idea(self):
        helpers.write(os.path.join(self.dir, "idea.md"), "fix typo\n")
        st = state.new_state("f", "r", {}, "feat/f", small=True)
        self.assertEqual(set(freshness.input_hashes(self.work, st, "build")), {"idea"})
        self.build_go()  # uses self.st; build a separate small snapshot:
        st["reviewed"]["build"] = freshness.snapshot(self.work, st, "origin/develop")
        self.assertTrue(freshness.check(self.work, st, "build")[0])
        helpers.write(os.path.join(self.dir, "idea.md"), "fix two typos\n")
        self.assertEqual(freshness.check(self.work, st, "build"), (False, "idea.md changed since the GO"))

    def test_upstream_invalidated(self):
        self.st["stage"] = "spec"
        self.st["reviewed"]["spec"] = freshness.snapshot(self.work, self.st)
        self.st["verdicts"]["spec"] = "GO"
        self.st["stage"] = "plan"
        self.assertIsNone(freshness.upstream_invalidated(self.work, self.st))
        helpers.write(os.path.join(self.dir, "spec.md"), "spec v2\n")
        self.assertEqual(freshness.upstream_invalidated(self.work, self.st), "spec")


    def test_another_features_bookkeeping_on_develop_does_not_stale_a_go(self):
        self.build_go()
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        helpers.write(os.path.join(other, "docs/relay/lower/state.md"), "---\n{}\n---\n")
        helpers.write(os.path.join(other, "docs/relay/lower/reviews/build-1.codex.md"), "GO\n")
        helpers.sh(other, "git", "add", "-A")
        helpers.sh(other, "git", "commit", "-q", "-m", "a stacked PR below merges")
        helpers.sh(other, "git", "push", "-q", "origin", "develop")
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        self.assertEqual(freshness.check(self.work, self.st, "build"), (True, "code and merge result unchanged"))

if __name__ == "__main__":
    unittest.main()
