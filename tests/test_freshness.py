import os, shutil, tempfile, unittest
from unittest import mock
from relaylib import freshness, gitops, state
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


class DataPathsTest(unittest.TestCase):
    """data-paths-freshness: declared data paths on the base do not stale a build GO."""

    LIST = '[freshness]\ndata_paths = ["kpi/snapshots.csv", "marketing/packs/", "AGENTS.md", ".github/w.yml", "naïve data.csv"]\n'

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.work = helpers.make_repo(self.tmp)
        self.other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        self.st = state.new_state("f", "r", {}, "feat/f")
        self.st["stage"] = "build"

    def base_commit(self, files, remove=()):
        """Commit files ({path: text}) to develop from the second clone, push, and fetch in work."""
        helpers.sh(self.other, "git", "pull", "-q", "origin", "develop")
        for path, text in files.items():
            helpers.write(os.path.join(self.other, path), text)
        for path in remove:
            helpers.sh(self.other, "git", "rm", "-q", path)
        helpers.sh(self.other, "git", "add", "-A")
        helpers.sh(self.other, "git", "commit", "-q", "-m", "base")
        helpers.sh(self.other, "git", "push", "-q", "origin", "develop")
        helpers.sh(self.work, "git", "fetch", "-q", "origin")

    def declare(self, text=None):
        self.base_commit({"docs/relay/config.toml": text or self.LIST, "kpi/snapshots.csv": "a\n",
                          "marketing/packs/w1.md": "p\n", "AGENTS.md": "rules\n", ".github/w.yml": "on: push\n",
                          "naïve data.csv": "n\n", "app.py": "print(0)\n"})

    def go(self, change=None, remove=()):
        helpers.sh(self.work, "git", "pull", "-q", "origin", "develop")
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/f")
        for path, text in (change or {"feature.py": "print(1)\n"}).items():
            helpers.write(os.path.join(self.work, path), text)
        for path in remove:
            helpers.sh(self.work, "git", "rm", "-q", path)
        helpers.sh(self.work, "git", "add", "-A")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "code")
        self.st["reviewed"]["build"] = freshness.snapshot(self.work, self.st, "origin/develop")
        return self.st["reviewed"]["build"]

    def check(self):
        return freshness.check(self.work, self.st, "build")

    # Task 1: reading and validating

    def test_valid_entries_and_what_is_data(self):
        self.declare()
        base = gitops.head_sha(self.work, "origin/develop")
        entries, why = freshness.data_paths(self.work, base)
        self.assertIsNone(why)
        self.assertIn("marketing/packs/", entries)
        is_data = lambda p: freshness.is_data(p, entries)
        self.assertTrue(is_data("kpi/snapshots.csv"))
        self.assertTrue(is_data("marketing/packs/w2.yaml"))
        self.assertFalse(is_data("marketing/packs/tool.py"))            # code under a data folder stays
        self.assertFalse(is_data("kpi/Snapshots.csv"))                   # case-sensitive entries (D7)
        self.assertFalse(is_data("AGENTS.md"))                           # never data, even when listed
        self.assertFalse(is_data(".github/w.yml"))
        self.assertFalse(freshness.is_data("sub/Claude.md", ["sub/"]))   # never-data names ignore case
        self.assertFalse(freshness.is_data(".GitHub/x.yml", [".GitHub/"]))
        self.assertFalse(freshness.is_data("docs/relay/x.md", ["docs/"]))

    def test_each_invalid_form_gives_no_entries_and_a_reason(self):
        bad = ['data_paths = "kpi/"', 'data_paths = [3]', 'data_paths = ["/kpi/a.csv"]', 'data_paths = ["../a.csv"]',
               'data_paths = [""]', 'data_paths = ["kpi/*.csv"]', 'data_paths = ["kpi/a?.csv"]',
               'data_paths = ["kpi/[a].csv"]', 'data_paths = ["tool.py"]', 'data_paths = ["./a.csv"]',
               'data_paths = ["a//b.csv"]', 'data_paths = ["a\\\\b.csv"]', 'data_paths = [" x.csv"]',
               'data_paths = ["a/./b.csv"]', 'data_paths = = ']
        for line in bad:
            helpers.write(os.path.join(self.work, "docs/relay/config.toml"), f"[freshness]\n{line}\n")
            helpers.sh(self.work, "git", "add", "-A")
            helpers.sh(self.work, "git", "commit", "-q", "-m", line)
            entries, why = freshness.data_paths(self.work, "HEAD")
            self.assertEqual(entries, [], line)
            self.assertTrue(why, line)

    def test_no_file_or_key_is_no_data_paths(self):
        self.assertEqual(freshness.data_paths(self.work, "HEAD"), ([], None))
        helpers.write(os.path.join(self.work, "docs/relay/config.toml"), "[build]\nci = \"none\"\n")
        helpers.sh(self.work, "git", "add", "-A")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "config")
        self.assertEqual(freshness.data_paths(self.work, "HEAD"), ([], None))

    # Task 2: digest, snapshot and check

    def test_a_base_commit_to_a_declared_file_keeps_the_go_fresh(self):
        self.declare()
        self.assertTrue(self.go()["data_paths"])
        self.base_commit({"kpi/snapshots.csv": "a\nb\n", "marketing/packs/w2.md": "new pack\n",
                          "naïve data.csv": "changed\n"})
        self.assertEqual(self.check(), (True, "code and merge result unchanged"))

    def test_a_base_commit_to_an_undeclared_path_makes_it_stale(self):
        self.declare()
        self.go()
        self.base_commit({"app.py": "print(2)\n"})
        self.assertEqual(self.check(), (False, "merge result against origin/develop changed"))

    def test_code_under_a_declared_folder_or_never_data_files_still_count(self):
        for path in ("marketing/packs/tool.py", "AGENTS.md", ".github/w.yml"):
            with self.subTest(path=path):
                self.setUp()
                self.declare()
                self.go()
                self.base_commit({path: "changed\n"})
                self.assertFalse(self.check()[0])

    def test_a_pr_that_touches_data_keeps_the_whole_tree(self):
        cases = [({"kpi/snapshots.csv": "pr edit\n"}, ()), ({"feature.py": "x\n"}, ("kpi/snapshots.csv",)),
                 ({"feature.py": "x\n"}, ("marketing/packs/w1.md",))]
        for change, remove in cases:
            with self.subTest(change=change, remove=remove):
                self.setUp()
                self.declare()
                if remove == ("marketing/packs/w1.md",):  # a rename out of a declared folder
                    helpers.sh(self.work, "git", "pull", "-q", "origin", "develop")
                    change = {"moved.md": "p\n"}
                self.assertEqual(self.go(change, remove)["data_paths"], [])
                self.base_commit({"marketing/packs/w3.md": "x\n"})
                self.assertFalse(self.check()[0])

    def test_a_list_on_the_pr_branch_alone_has_no_effect(self):
        self.base_commit({"kpi/snapshots.csv": "a\n"})
        self.assertEqual(self.go({"docs/relay/config.toml": self.LIST, "feature.py": "x\n"})["data_paths"], [])
        self.base_commit({"kpi/snapshots.csv": "b\n"})
        self.assertFalse(self.check()[0])

    def test_a_list_added_changed_or_removed_after_the_go_is_stale_once(self):
        self.go()
        self.declare()
        self.assertEqual(self.check(), (False, "data paths changed since the GO"))
        self.setUp()
        self.declare()
        self.go()
        self.base_commit({"docs/relay/config.toml": '[freshness]\ndata_paths = ["kpi/"]\n'})
        self.assertEqual(self.check(), (False, "data paths changed since the GO"))
        self.setUp()
        self.declare()
        self.go()
        self.base_commit({}, remove=("docs/relay/config.toml",))
        self.assertEqual(self.check(), (False, "data paths changed since the GO"))

    def test_an_invalid_list_on_the_base_fails_closed_with_a_reason(self):
        self.declare('[freshness]\ndata_paths = ["kpi/*.csv"]\n')
        self.assertEqual(self.go()["data_paths"], [])
        self.base_commit({"kpi/snapshots.csv": "b\n"})
        fresh, why = self.check()
        self.assertFalse(fresh)
        self.assertTrue(why.startswith("merge result against origin/develop changed; data paths on origin/develop not used:"))
        helpers.write(os.path.join(self.work, "feature.py"), "print(9)\n")
        helpers.sh(self.work, "git", "commit", "-q", "-am", "more code")
        self.assertTrue(self.check()[1].startswith("code changed since the GO; data paths on origin/develop not used:"))

    def test_no_merge_base_means_the_whole_tree(self):
        self.declare()
        real = gitops.git

        def git(root, *args, **k):
            if args[:1] == ("merge-base",):
                return real(root, "rev-parse", "--verify", "-q", "no-such-ref", check=False)
            return real(root, *args, **k)
        with mock.patch.object(gitops, "git", side_effect=git):
            self.assertEqual(self.go()["data_paths"], [])

    def test_an_older_go_without_the_record_and_no_list_stays_fresh(self):
        snap = self.go()
        del snap["data_paths"]
        self.assertEqual(self.check(), (True, "code and merge result unchanged"))

    def test_a_missing_base_ref_with_a_list_is_stale_without_raising(self):
        self.declare()
        self.go()
        helpers.sh(self.work, "git", "update-ref", "-d", "refs/remotes/origin/develop")
        self.assertEqual(self.check(), (False, "merge result against origin/develop changed"))
