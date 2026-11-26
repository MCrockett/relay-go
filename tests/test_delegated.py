import json
import os
from unittest import mock

from relaylib import cimode, delegated, gitops
from relaylib.errors import RelayError
from tests import helpers
from tests import test_commands

OPT_IN = '[merge]\nproduction_branches = ["main"]\n'


class DelegatedBase(test_commands.CommandsTest):
    """A repo with a ready small feature and a PR (as MergeCommandTest), for the D1 conditions and mechanics."""

    def setUp(self):
        super().setUp()
        self.gh_log = os.path.join(self.tmp, "gh.log")
        os.environ["FAKE_GH_LOG"] = self.gh_log
        p = mock.patch("relaylib.owneractions.github_repo", return_value="owner/project")  # origin is a bare repo
        p.start()
        self.addCleanup(p.stop)

    def other_clone(self):
        self.n += 1
        other = helpers.clone(self.origin, os.path.join(self.tmp, f"other-{self.n}"))
        helpers.sh(other, "git", "switch", "-q", "develop")
        return other

    def push_branch(self, branch, files=None, renames=(), base="origin/develop"):
        """A branch off `base` changing files ({path: text}) and renaming (old, new) pairs, pushed from another
        clone; returns its head (fetched into the work repo)."""
        other = self.other_clone()
        helpers.sh(other, "git", "switch", "-q", "-c", branch, base)
        for old, new in renames:
            os.makedirs(os.path.dirname(os.path.join(other, new)) or other, exist_ok=True)
            helpers.sh(other, "git", "mv", old, new)
        for path, text in (files or {}).items():
            helpers.write(os.path.join(other, path), text)
        helpers.sh(other, "git", "add", "-A")
        helpers.sh(other, "git", "commit", "-q", "-m", f"change on {branch}")
        helpers.sh(other, "git", "push", "-q", "origin", branch)
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        return helpers.sh(other, "git", "rev-parse", "HEAD").strip()

    def base_sha(self, base="develop"):
        return helpers.sh(self.work, "git", "rev-parse", f"origin/{base}").strip()


class OptInTest(DelegatedBase):  # R3, R4, R5, R7
    def listed(self, base="develop", sha=None):
        return delegated.production_branches(self.work, sha or self.base_sha(base), base, "project")

    def test_opt_in_lists_the_production_branches(self):
        self.base_config(OPT_IN)
        self.assertEqual(self.listed(), (["main"], None))

    def test_no_config_file_is_not_opted_in(self):
        self.assertEqual(self.listed(), (None, "project has not opted in: no [merge] production_branches on develop"))

    def test_no_merge_table_and_no_key_are_not_opted_in(self):
        for text in ('[build]\nci = "pr"\n', "[merge]\n"):
            with self.subTest(text):
                self.base_config(text)
                self.assertEqual(self.listed()[1],
                                 "project has not opted in: no [merge] production_branches on develop")

    def test_a_non_list_or_empty_list_or_blank_entry_is_refused(self):
        for value in ('"main"', "[]", '["main", ""]', "[1]"):
            with self.subTest(value):
                self.base_config(f"[merge]\nproduction_branches = {value}\n")
                self.assertEqual(self.listed(),
                                 (None, "[merge] production_branches on develop must list at least one branch"))

    def test_a_toml_error_names_the_file(self):
        self.base_config("[merge\n")
        with self.assertRaises(RelayError) as cm:
            self.listed()
        self.assertTrue(str(cm.exception).startswith("origin/develop:docs/relay/config.toml:"), str(cm.exception))

    def test_the_list_is_read_from_the_base_not_the_pr_branch(self):
        helpers.sh(self.work, "git", "switch", "-q", "-c", "fix/x")
        helpers.write(os.path.join(self.work, "docs/relay/config.toml"), OPT_IN)
        helpers.sh(self.work, "git", "add", "-A")
        helpers.sh(self.work, "git", "commit", "-q", "-m", "adds the list")
        helpers.sh(self.work, "git", "push", "-q", "origin", "fix/x")
        self.assertIsNone(self.listed()[0])

    def test_the_list_is_read_at_the_pinned_commit(self):
        pinned = self.base_sha()
        self.base_config(OPT_IN)
        self.assertIsNone(self.listed(sha=pinned)[0])
        self.assertEqual(self.listed()[0], ["main"])

    def test_control_plane_names_match_the_github_name_ignoring_case(self):
        for name in ("owner/hordle", "owner/Relay-Go", "owner/UpstreamPartners"):
            self.assertTrue(delegated.control_plane(name), name)
        for name in ("owner/hordle-app", "owner/project"):
            self.assertFalse(delegated.control_plane(name), name)

    def test_the_folder_name_is_not_used(self):
        # the origin folder is named after the test repo, the GitHub name says hordle
        self.assertTrue(delegated.control_plane("someone/hordle"))
        self.assertFalse(delegated.control_plane("someone/project"))

    def test_the_three_small_checks_return_reasons(self):
        self.assertEqual(delegated.not_control_plane("owner/hordle"), "hordle is control plane; the owner merges it")
        self.assertIsNone(delegated.not_control_plane("owner/project"))
        self.assertEqual(delegated.not_production("main", ["main"]), "main is a production branch")
        self.assertIsNone(delegated.not_production("develop", ["main"]))

    def test_ci_none_never_qualifies(self):
        self.base_config('[build]\nci = "none"\n')
        self.assertEqual(delegated.ci_reason(cimode.settings(self.work, "develop")),
                         "ci = none never qualifies for D1")

    def test_release_mode_base_is_local_for_a_non_main_base(self):
        self.base_config('[build]\nci = "release"\ntest_command = "true"\n')
        self.assertEqual(cimode.settings(self.work, "develop").mode, "local")
        self.assertIsNone(delegated.ci_reason(cimode.settings(self.work, "develop")))
        self.assertEqual(cimode.settings(self.work, "main").mode, "pr")
        self.assertIsNone(delegated.ci_reason(cimode.settings(self.work, "main")))


class ProtectedPathTest(DelegatedBase):  # R8
    def protected(self, files=None, renames=(), seed=None):
        if seed:  # files that already exist on the base
            other = self.other_clone()
            for path, text in seed.items():
                helpers.write(os.path.join(other, path), text)
            helpers.sh(other, "git", "add", "-A")
            helpers.sh(other, "git", "commit", "-q", "-m", "seed")
            helpers.sh(other, "git", "push", "-q", "origin", "develop")
            helpers.sh(self.work, "git", "fetch", "-q", "origin")
        self.n += 1
        head = self.push_branch(f"pr{self.n}", files, renames)
        return delegated.protected_path(self.work, self.base_sha(), head)

    def test_agents_claude_and_workflow_md_in_any_folder(self):
        for path in ("AGENTS.md", "sub/CLAUDE.md", "a/b/workflow.md"):
            with self.subTest(path):
                self.assertEqual(self.protected({path: "x\n"}), path)

    def test_relay_config_and_github_folder(self):
        for path in ("docs/relay/config.toml", ".github/workflows/test.yml"):
            with self.subTest(path):
                self.assertEqual(self.protected({path: "x\n"}), path)

    def test_a_rename_counts_both_sides(self):
        self.assertEqual(self.protected(renames=[("AGENTS.md", "notes.md")], seed={"AGENTS.md": "a\n"}), "AGENTS.md")
        self.assertEqual(self.protected(renames=[("notes2.md", "sub/AGENTS.md")], seed={"notes2.md": "b\n"}),
                         "sub/AGENTS.md")

    def test_other_paths_pass(self):
        for path in ("app.py", "docs/relay/demo/spec.md", "docs/other.md", "docs/relay/config.toml.bak"):
            with self.subTest(path):
                self.assertIsNone(self.protected({path: "x\n"}))

    def test_diff_is_from_the_merge_base(self):
        head = self.push_branch("early", {"app.py": "1\n"})
        other = self.other_clone()  # AGENTS.md changes on the base after the PR branched
        helpers.write(os.path.join(other, "AGENTS.md"), "later\n")
        helpers.sh(other, "git", "add", "-A")
        helpers.sh(other, "git", "commit", "-q", "-m", "base moves")
        helpers.sh(other, "git", "push", "-q", "origin", "develop")
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        self.assertIsNone(delegated.protected_path(self.work, self.base_sha(), head))

    def test_the_first_path_in_order_is_named(self):
        self.assertEqual(self.protected({"b/CLAUDE.md": "x\n", ".github/a.yml": "y\n"}), ".github/a.yml")


def _only_own_tests():
    """Run only this file's tests, not the CommandsTest ones the classes inherit."""
    for cls in [c for c in globals().values() if isinstance(c, type) and issubclass(c, test_commands.CommandsTest)]:
        for name in dir(test_commands.CommandsTest):
            if name.startswith("test_") and name not in cls.__dict__ and not any(
                    name in base.__dict__ and base is not test_commands.CommandsTest and base.__module__ == __name__
                    for base in cls.__mro__):
                setattr(cls, name, None)


_only_own_tests()
