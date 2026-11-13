import os
import shutil
import tempfile
import unittest
from unittest import mock

from relaylib import gitops, scancache
from relaylib.errors import RelayError
from tests import helpers


class CountingTest(unittest.TestCase):
    """Counts the git processes the code under test starts, through gitops.run and gitops.cat_files."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = temp.name
        self.origin, self.work = helpers.make_repo(self.tmp)
        self.calls = []
        real_run, real_cat = gitops.run, gitops.cat_files

        def run(args, *a, **k):
            self.calls.append(list(args))
            return real_run(args, *a, **k)

        def cat(root, specs):
            self.calls.append(["git", "cat-file", "--batch", *specs])
            return real_cat(root, specs)

        for name, fn in (("run", run), ("cat_files", cat)):
            p = mock.patch.object(gitops, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    def git_calls(self, *words):
        return [c for c in self.calls if c[0] == "git" and all(w in c for w in words)]


class FactsTest(CountingTest):
    def setUp(self):
        super().setUp()
        helpers.sh(self.work, "git", "switch", "-qc", "feat/x")
        helpers.sh(self.work, "git", "push", "-qu", "origin", "feat/x")
        self.tree = os.path.join(self.tmp, "tree")
        helpers.sh(self.work, "git", "worktree", "add", "-q", "-b", "feat/y", self.tree)
        helpers.sh(self.tree, "git", "commit", "-q", "--allow-empty", "-m", "y")
        self.store = scancache.Store()

    def read(self, checkout=None):
        checkout = checkout or self.work
        with scancache.use(self.store):
            return {"branch": gitops.current_branch(checkout), "head": gitops.head_sha(checkout),
                    "url": gitops.origin_url(checkout), "common": gitops.common_dir(checkout),
                    "trees": gitops.worktrees(checkout), "remote": gitops.remote_branches(checkout),
                    "x": gitops.head_sha(checkout, "origin/feat/x")}

    def test_second_read_starts_no_git(self):
        first = self.read()
        self.read(self.tree)
        self.calls.clear()
        self.assertEqual(self.read(), first)
        self.read(self.tree)
        self.assertEqual(self.calls, [])
        self.assertEqual(first["branch"], "feat/x")
        self.assertEqual(first["remote"], ["origin/develop", "origin/feat/x"])
        self.assertEqual(len(first["trees"]), 2)

    def test_answers_match_git(self):
        got = self.read(self.tree)
        self.assertEqual(got["branch"], "feat/y")
        self.assertEqual(got["head"], helpers.sh(self.tree, "git", "rev-parse", "HEAD").strip())
        self.assertEqual(got["common"], os.path.realpath(os.path.join(self.work, ".git")))
        self.assertEqual(got["url"], self.origin)

    def test_changes_are_seen(self):
        self.read()
        helpers.write(os.path.join(self.work, "a.txt"), "a")
        helpers.sh(self.work, "git", "add", "a.txt")
        helpers.sh(self.work, "git", "commit", "-qm", "a")
        self.assertEqual(self.read()["head"], helpers.sh(self.work, "git", "rev-parse", "HEAD").strip())
        helpers.sh(self.work, "git", "switch", "-q", "develop")
        self.assertEqual(self.read()["branch"], "develop")
        other = os.path.join(self.tmp, "other")
        helpers.sh(self.work, "git", "worktree", "add", "-q", "-b", "feat/z", other)
        self.assertEqual(len(self.read()["trees"]), 3)
        helpers.sh(self.work, "git", "remote", "set-url", "origin", self.origin + "/")
        self.assertEqual(self.read()["url"], self.origin + "/")

    def test_push_from_elsewhere_is_seen_after_fetch(self):
        before = self.read()["x"]
        peer = helpers.clone(self.origin, os.path.join(self.tmp, "peer"))
        helpers.sh(peer, "git", "switch", "-q", "feat/x")
        helpers.write(os.path.join(peer, "b.txt"), "b")
        helpers.sh(peer, "git", "add", "b.txt")
        helpers.sh(peer, "git", "commit", "-qm", "b")
        helpers.sh(peer, "git", "push", "-q", "origin", "feat/x")
        helpers.sh(self.work, "git", "pack-refs", "--all")  # the move lands in packed-refs or a loose ref
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        after = self.read()["x"]
        self.assertNotEqual(before, after)
        self.assertEqual(after, helpers.sh(peer, "git", "rev-parse", "HEAD").strip())

    def test_fetch_that_moves_nothing_keeps_facts(self):
        helpers.sh(self.work, "git", "fetch", "-q", "origin")  # a first fetch may add origin/HEAD
        self.read()
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        self.calls.clear()
        self.read()
        self.assertEqual(self.calls, [])

    def test_stamps_are_taken_once_per_build_until_restamp(self):
        with scancache.use(self.store):
            first = gitops.head_sha(self.work, "origin/feat/x")
            helpers.sh(self.work, "git", "update-ref", "refs/remotes/origin/feat/x", "feat/y")
            self.assertEqual(gitops.head_sha(self.work, "origin/feat/x"), first)  # stamps of this build
            scancache.restamp(gitops.common_dir(self.work))
            self.assertNotEqual(gitops.head_sha(self.work, "origin/feat/x"), first)

    def test_deleted_checkout_surfaces_git_error(self):
        self.read(self.tree)
        shutil.rmtree(self.tree)
        with scancache.use(self.store), self.assertRaises(Exception):
            gitops.current_branch(self.tree)

    def test_missing_stamp_file_counts_as_change(self):
        a = scancache.stamp([os.path.join(self.work, "nope")])
        self.assertEqual(a[0][1], None)

    def test_no_store_reads_git(self):
        gitops.current_branch(self.work)
        self.assertEqual(len(self.git_calls("rev-parse")), 1)
        self.calls.clear()
        gitops.current_branch(self.work)
        self.assertEqual(len(self.git_calls("rev-parse")), 1)

    def test_disabled_store_reads_git(self):
        store = scancache.Store(enabled=False)
        with scancache.use(store):
            gitops.current_branch(self.work)
            gitops.current_branch(self.work)
        self.assertEqual(len(self.git_calls("rev-parse")), 2)

    def test_remote_ref_exists(self):
        with scancache.use(self.store):
            self.assertTrue(gitops.remote_ref_exists(self.work, "origin/feat/x"))
            self.assertFalse(gitops.remote_ref_exists(self.work, "origin/nope"))
        self.assertTrue(gitops.remote_ref_exists(self.work, "origin/feat/x"))
        self.assertFalse(gitops.remote_ref_exists(self.work, "origin/nope"))


if __name__ == "__main__":
    unittest.main()
