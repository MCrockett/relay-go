import os
import shutil
import tempfile
import unittest

from relaylib import tidy
from tests import helpers


class TidyTest(unittest.TestCase):
    """A feature branch merged into develop on origin, as GitHub leaves it after a merge (merge-and-tidy D5)."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="relaytidy-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.work = helpers.make_repo(self.tmp)
        helpers.write(os.path.join(self.work, ".gitignore"), "*.db\nsecret.cfg\nlogs\nconf/\n")
        self.commit(self.work, ".gitignore", "ignore local data")
        helpers.sh(self.work, "git", "push", "-q", "origin", "develop")
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/x")
        helpers.write(os.path.join(self.work, "app.py"), "print(1)\n")
        self.commit(self.work, "app.py", "feature")
        helpers.sh(self.work, "git", "push", "-q", "-u", "origin", "feat/x")
        self.pr_head = self.rev(self.work, "HEAD")
        self.github = helpers.clone(self.origin, os.path.join(self.tmp, "github"))  # where the merge happens
        helpers.sh(self.github, "git", "merge", "-q", "--no-ff", "-m", "Merge PR #7", "origin/feat/x")
        helpers.sh(self.github, "git", "push", "-q", "origin", "develop")
        helpers.sh(self.github, "git", "push", "-q", "origin", "--delete", "feat/x")

    def commit(self, repo, path, message, force=False):
        helpers.sh(repo, "git", "add", *(["-f"] if force else []), path)
        helpers.sh(repo, "git", "commit", "-q", "-m", message)

    def rev(self, repo, ref):
        return helpers.sh(repo, "git", "rev-parse", ref).strip()

    def branch(self, repo):
        return helpers.sh(repo, "git", "branch", "--show-current").strip()

    def tidy(self, cwd=None):
        return tidy.run(self.work, "feat/x", "develop", self.pr_head, cwd)

    def linked(self):
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        path = os.path.join(self.tmp, "wt-x")
        helpers.sh(self.work, "git", "worktree", "add", "-q", path, "feat/x")
        return path

    def test_clean_linked_worktree_removed_main_pulled_branch_deleted(self):
        path = self.linked()
        r = self.tidy(cwd=os.path.join(path, "sub"))
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.rev(self.work, "develop"), self.rev(self.work, "origin/develop"))
        self.assertNotIn("feat/x", helpers.sh(self.work, "git", "branch"))
        self.assertEqual(r.left, [])
        self.assertIn(f"removed worktree {path}", r.lines)
        self.assertIn("pulled develop", r.lines)
        self.assertIn("deleted local branch feat/x", r.lines)
        self.assertIn("pruned worktrees", r.lines)
        self.assertEqual(r.lines[-1], f"the folder you ran this from was removed; continue in {self.work}")

    def test_worktree_with_anything_git_does_not_hold_is_kept(self):
        for name, setup in (("edited", lambda p: helpers.write(os.path.join(p, "app.py"), "print(2)\n")),
                            ("untracked", lambda p: helpers.write(os.path.join(p, "notes.txt"), "x\n")),
                            ("ignored", lambda p: helpers.write(os.path.join(p, "local.db"), "rows\n"))):
            with self.subTest(name):
                path = self.linked()
                setup(path)
                r = self.tidy()
                self.assertTrue(os.path.isdir(path))
                (line,) = [l for l in r.lines if l.startswith("kept worktree")]
                self.assertIn(f"git worktree remove --force {path}", line)
                self.assertIn("kept local branch feat/x: a worktree still has it checked out", r.lines)
                helpers.sh(self.work, "git", "worktree", "remove", "--force", path)

    def test_ignored_file_paths_are_listed(self):
        path = self.linked()
        helpers.write(os.path.join(path, "local.db"), "rows\n")
        line = next(l for l in self.tidy().lines if l.startswith("kept worktree"))
        self.assertIn("local.db", line)

    def test_main_checkout_on_the_feature_branch_is_switched_and_pulled(self):
        r = self.tidy()
        self.assertEqual(self.branch(self.work), "develop")
        self.assertEqual(self.rev(self.work, "HEAD"), self.rev(self.work, "origin/develop"))
        self.assertIn("switched the main checkout to develop", r.lines)
        self.assertIn("deleted local branch feat/x", r.lines)

    def test_main_checkout_with_uncommitted_changes_stays(self):
        helpers.write(os.path.join(self.work, "app.py"), "print(3)\n")
        r = self.tidy()
        self.assertEqual(self.branch(self.work), "feat/x")
        self.assertTrue(any("uncommitted changes" in l for l in r.lines))
        self.assertIn("main checkout", r.left)

    def assert_not_switched_and_kept(self, path, content):
        r = self.tidy()
        self.assertEqual(self.branch(self.work), "feat/x")
        with open(os.path.join(self.work, path)) as f:
            self.assertEqual(f.read(), content)
        self.assertTrue(any("would overwrite ignored files" in l and path in l for l in r.lines), r.lines)

    def test_ignored_file_the_local_base_tracks_blocks_the_switch(self):
        helpers.sh(self.work, "git", "switch", "-q", "develop")          # local develop ahead of origin
        helpers.write(os.path.join(self.work, "secret.cfg"), "theirs\n")
        self.commit(self.work, "secret.cfg", "track a config", force=True)
        helpers.sh(self.work, "git", "switch", "-q", "feat/x")
        helpers.write(os.path.join(self.work, "secret.cfg"), "mine\n")   # ignored on feat/x
        self.assert_not_switched_and_kept("secret.cfg", "mine\n")

    def test_ignored_file_where_the_target_has_a_folder_blocks_the_switch(self):
        helpers.sh(self.work, "git", "switch", "-q", "develop")          # the switch target is local develop
        os.makedirs(os.path.join(self.work, "logs"))
        helpers.write(os.path.join(self.work, "logs", "a.txt"), "a\n")
        self.commit(self.work, "logs/a.txt", "a logs folder", force=True)
        helpers.sh(self.work, "git", "switch", "-q", "feat/x")
        helpers.write(os.path.join(self.work, "logs"), "my log\n")       # an ignored file named logs
        self.assert_not_switched_and_kept("logs", "my log\n")

    def test_ignored_file_under_a_path_the_target_has_as_a_file_blocks_the_switch(self):
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        helpers.write(os.path.join(self.work, "conf"), "a file\n")
        self.commit(self.work, "conf", "conf is a file", force=True)
        helpers.sh(self.work, "git", "switch", "-q", "feat/x")
        os.makedirs(os.path.join(self.work, "conf"))
        helpers.write(os.path.join(self.work, "conf", "x"), "mine\n")     # ignored folder conf/
        self.assert_not_switched_and_kept(os.path.join("conf", "x"), "mine\n")

    def test_a_pull_that_would_overwrite_an_ignored_file_is_skipped(self):
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        helpers.sh(self.github, "git", "pull", "-q")
        helpers.write(os.path.join(self.github, "secret.cfg"), "theirs\n")
        self.commit(self.github, "secret.cfg", "track a config", force=True)
        helpers.sh(self.github, "git", "push", "-q", "origin", "develop")
        helpers.write(os.path.join(self.work, "secret.cfg"), "mine\n")
        r = self.tidy()
        with open(os.path.join(self.work, "secret.cfg")) as f:
            self.assertEqual(f.read(), "mine\n")
        self.assertTrue(any(l.startswith("did not pull develop: it would overwrite ignored files") for l in r.lines))
        self.assertIn("pull develop", r.left)

    def test_a_different_upstream_is_not_pulled(self):  # build review R1-1
        helpers.sh(self.github, "git", "pull", "-q")
        helpers.sh(self.github, "git", "switch", "-q", "-c", "other")
        helpers.write(os.path.join(self.github, "secret.cfg"), "theirs\n")
        self.commit(self.github, "secret.cfg", "other tracks a config", force=True)
        helpers.sh(self.github, "git", "push", "-q", "origin", "other")
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        helpers.sh(self.work, "git", "branch", "-q", "--set-upstream-to=origin/other", "develop")
        helpers.write(os.path.join(self.work, "secret.cfg"), "mine\n")
        r = self.tidy()
        with open(os.path.join(self.work, "secret.cfg")) as f:
            self.assertEqual(f.read(), "mine\n")
        self.assertEqual(self.rev(self.work, "develop"), self.rev(self.work, "origin/develop"))
        self.assertIn("pulled develop", r.lines)

    def test_a_remote_that_moves_after_the_check_is_not_pulled_past_it(self):
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        helpers.write(os.path.join(self.work, "secret.cfg"), "mine\n")
        checked = tidy.ignored_collisions

        def check_then_move(checkout, target):
            hits = checked(checkout, target)
            helpers.sh(self.github, "git", "pull", "-q")
            helpers.write(os.path.join(self.github, "secret.cfg"), "theirs\n")
            self.commit(self.github, "secret.cfg", "track a config", force=True)
            helpers.sh(self.github, "git", "push", "-q", "origin", "develop")
            helpers.sh(self.work, "git", "fetch", "-q", "origin")   # even a fetch in between changes nothing
            return hits
        from unittest import mock
        with mock.patch.object(tidy, "ignored_collisions", side_effect=check_then_move):
            r = self.tidy()
        with open(os.path.join(self.work, "secret.cfg")) as f:
            self.assertEqual(f.read(), "mine\n")
        self.assertIn("pulled develop", r.lines)
        self.assertNotEqual(self.rev(self.work, "develop"), self.rev(self.work, "origin/develop"))

    def test_main_checkout_on_another_branch_stays_and_base_fast_forwards(self):
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/other", "develop")
        before = self.rev(self.work, "develop")
        r = self.tidy()
        self.assertEqual(self.branch(self.work), "feat/other")
        self.assertNotEqual(self.rev(self.work, "develop"), before)
        self.assertEqual(self.rev(self.work, "develop"), self.rev(self.work, "origin/develop"))
        self.assertIn("fast-forwarded local develop (the main checkout stays on feat/other)", r.lines)
        self.assertIn("deleted local branch feat/x", r.lines)

    def test_local_branch_with_unpushed_commits_is_kept(self):
        helpers.write(os.path.join(self.work, "more.py"), "x\n")
        self.commit(self.work, "more.py", "not pushed")
        r = self.tidy()
        self.assertEqual(self.branch(self.work), "develop")
        self.assertIn("feat/x", helpers.sh(self.work, "git", "branch"))
        self.assertTrue(any(l.startswith("kept local branch feat/x: it has commits the merged PR does not")
                            for l in r.lines))

    def test_a_failing_step_is_reported_not_raised(self):
        helpers.sh(self.work, "git", "remote", "set-url", "origin", os.path.join(self.tmp, "missing.git"))
        r = self.tidy()
        self.assertTrue(r.lines[0].startswith("fetch failed"))
        self.assertIn("fetch", r.left)
        self.assertIn("pruned worktrees", r.lines)


if __name__ == "__main__":
    unittest.main()
