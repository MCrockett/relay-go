import json
import os
from unittest import mock

from relaylib import cimode, config, delegated, freshness, gitops, owneractions, reviewtables
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

    def set_base(self, text, base="develop"):
        """Push `text` as the base's relay config unless it is already there."""
        if getattr(self, "_base_texts", {}).get(base) == text:
            return
        self._base_texts = {**getattr(self, "_base_texts", {}), base: text}
        self.n += 1  # base_config names its clone by this counter and the text
        self.base_config(text, base)

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


class ReviewEvidenceTest(DelegatedBase):  # R6
    def setUp(self):
        super().setUp()
        self.ready_small_feature()
        self.pr()
        self.seen = owneractions.fingerprint(self.work, "tiny", fetch=False)
        self.commit = self.seen["commit"]
        self.reviews = "docs/relay/tiny/reviews"

    def st_at(self, commit=None):
        return owneractions.state_at(self.work, "tiny", commit or self.commit)

    def real_review(self):
        text = helpers.sh(self.work, "git", "show", f"{self.commit}:{self.reviews}/build-1.codex.md")
        meta = json.loads(text.split("---\n")[1])
        return meta, text

    def edit(self, files):
        """Publish changes to the feature branch ({path: text, or None to delete}); the new published commit."""
        other = self.other_clone()
        helpers.sh(other, "git", "switch", "-q", "fix/tiny")
        for path, text in files.items():
            if text is None:
                helpers.sh(other, "git", "rm", "-q", path)
            else:
                helpers.write(os.path.join(other, path), text)
        helpers.sh(other, "git", "add", "-A")
        helpers.sh(other, "git", "commit", "-q", "-m", "evidence")
        helpers.sh(other, "git", "push", "-q", "origin", "fix/tiny")
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        return helpers.sh(other, "git", "rev-parse", "HEAD").strip()

    def md(self, **over):
        meta, _ = self.real_review()
        meta = {**meta, **over}
        meta = {k: v for k, v in meta.items() if v is not None}
        return "---\n" + json.dumps(meta, indent=2, sort_keys=True) + "\n---\n\nReview.\n"

    def evidence(self, commit=None):
        commit = commit or self.commit
        return delegated.review_evidence(self.work, self.st_at(commit), commit)

    def table(self, meta, author="claude"):
        st = dict(self.st_at(), authors={"build": author})
        return delegated.table_reason(st, meta)

    REASON = "no review evidence for the build GO"
    NOT_IN_TABLE = "the build GO was not a review from the preference table"

    def test_a_codex_go_in_the_table_passes(self):
        meta, why = self.evidence()
        self.assertIsNone(why)
        self.assertEqual(meta["reviewer"], "codex")
        self.assertIsNone(delegated.table_reason(self.st_at(), meta))

    def test_same_provider_fallback_go_passes(self):
        self.assertIsNone(self.table({"reviewer": "claude", "model": "claude-fable-5-2"}))

    def test_a_model_not_in_the_table_is_refused(self):
        self.assertEqual(self.table({"reviewer": "codex", "model": "gpt-4"}), self.NOT_IN_TABLE)

    def test_a_provider_that_does_not_match_its_model_is_refused(self):
        self.assertEqual(self.table({"reviewer": "claude", "model": "gpt-6-astra"}), self.NOT_IN_TABLE)

    def test_effort_is_not_compared(self):
        meta, _ = self.evidence()
        self.assertIsNone(self.table(dict(meta, effort="low")))

    def test_a_repo_config_that_adds_the_reviewer_is_still_refused(self):
        self.base_config('[review.prefer]\nclaude = ["codex:gpt-4"]\n')
        self.assertEqual(self.table({"reviewer": "codex", "model": "gpt-4"}), self.NOT_IN_TABLE)

    def test_the_owner_timed_table_is_used(self):
        timed = ({"claude": {"entries": ["codex:gpt-4"]}}, None)
        with mock.patch.object(reviewtables, "read", return_value=timed):
            self.assertIsNone(self.table({"reviewer": "codex", "model": "gpt-4"}))

    def test_an_unreadable_owner_config_is_the_reason(self):
        helpers.write(self.cfg, "[limits\n")
        self.assertIn(self.cfg, self.table({"reviewer": "codex", "model": "gpt-6-astra"}))

    def test_a_model_less_entry_is_not_in_the_table(self):
        # config.parse_model_spec refuses an entry with no model, so R6's model-less branch cannot be reached
        with self.assertRaises(RelayError):
            config.parse_model_spec("codex")

    def test_a_reviews_folder_with_no_build_file_has_no_evidence(self):
        commit = self.edit({f"{self.reviews}/build-1.codex.md": None})
        self.assertEqual(self.evidence(commit), (None, self.REASON))

    def test_head_must_match_the_build_snapshot(self):
        commit = self.edit({f"{self.reviews}/build-1.codex.md": self.md(head="0" * 40)})
        self.assertEqual(self.evidence(commit), (None, self.REASON))

    def test_the_highest_round_is_used(self):
        nogo = self.md(verdict="NO-GO", round=2)
        commit = self.edit({f"{self.reviews}/build-2.codex.md": nogo})
        self.assertEqual(self.evidence(commit), (None, self.REASON))
        commit = self.edit({f"{self.reviews}/build-2.codex.md": self.md(round=2),
                            f"{self.reviews}/build-1.codex.md": self.md(verdict="NO-GO")})
        self.assertIsNone(self.evidence(commit)[1])

    def test_a_tie_is_broken_by_at_and_an_equal_or_bad_at_is_malformed(self):
        meta, _ = self.real_review()
        later = "2099-01-01T00:00:00-05:00"
        commit = self.edit({f"{self.reviews}/build-1r.codex.md": self.md(at=later, verdict="NO-GO")})
        self.assertEqual(self.evidence(commit), (None, self.REASON))  # the later NO-GO wins the tie
        commit = self.edit({f"{self.reviews}/build-1r.codex.md": self.md(at=later)})
        self.assertIsNone(self.evidence(commit)[1])  # the later GO wins it
        commit = self.edit({f"{self.reviews}/build-1r.codex.md": self.md(at=meta["at"])})
        self.assertEqual(self.evidence(commit), (None, self.REASON))  # an equal at is malformed
        commit = self.edit({f"{self.reviews}/build-1r.codex.md": self.md(at="soon")})
        self.assertEqual(self.evidence(commit), (None, self.REASON))

    def test_error_files_and_other_names_are_ignored(self):
        commit = self.edit({f"{self.reviews}/build-3.codex.error.md": self.md(verdict="error"),
                            f"{self.reviews}/build-stuck.md": "stuck\n"})
        self.assertIsNone(self.evidence(commit)[1])

    def test_a_suffixed_second_file_counts(self):
        commit = self.edit({f"{self.reviews}/build-1.codex-2.md": self.md(at="2099-01-01T00:00:00-05:00",
                                                                         verdict="NO-GO")})
        self.assertEqual(self.evidence(commit), (None, self.REASON))

    def test_missing_and_malformed_front_matter_refuse(self):
        meta, _ = self.real_review()
        bad = {"no fence": "just text\n", "bad json": "---\n{nope\n---\n\nx\n", "a list": "---\n[1]\n---\n\nx\n",
               "no verdict": self.md(verdict=None), "no model": self.md(model=None),
               "no reviewer": self.md(reviewer=None)}
        for name, text in bad.items():
            with self.subTest(name):
                commit = self.edit({f"{self.reviews}/build-1.codex.md": text})
                self.assertEqual(self.evidence(commit), (None, self.REASON))

    def test_evidence_is_read_from_the_published_commit_not_the_checkout(self):
        helpers.write(os.path.join(self.work, self.reviews, "build-1.codex.md"), self.md(verdict="NO-GO"))
        self.assertIsNone(self.evidence()[1])


class OverrideEvidenceTest(DelegatedBase):  # R6: an override GO has no review behind it
    def test_override_go_has_no_evidence(self):
        self.small_change()
        self.pr()
        self.open_prs("fix/tiny")
        self.enqueue_codex("NO-GO", ["a - x"])
        self.assertEqual(self.relay("submit"), 0, self.last_err)
        self.enqueue_codex("NO-GO", ["id: R1-1 a - x"], [("R1-1", "unresolved")])
        self.relay("submit")
        self.assertEqual(self.st("tiny")["status"], "waiting-owner")  # an override needs a stall or a review error
        self.pr()
        self.assertEqual(self.relay("override", "go", "--relayed"), 0, self.last_err)
        seen = owneractions.fingerprint(self.work, "tiny", fetch=False)
        st = owneractions.state_at(self.work, "tiny", seen["commit"])
        self.assertEqual(st["status"], "ready-to-merge")
        self.assertEqual(delegated.review_evidence(self.work, st, seen["commit"]),
                         (None, "no review evidence for the build GO"))


class ConditionsTest(DelegatedBase):  # R9: R3 to R8 in one function
    def setUp(self):
        super().setUp()
        self.ready_small_feature()
        self.pr()
        self.set_base(OPT_IN)
        seen = owneractions.fingerprint(self.work, "tiny", fetch=False)
        self.commit, self.pr_head = seen["commit"], seen["pr_head"]
        self.st_ = owneractions.state_at(self.work, "tiny", self.commit)

    def conditions(self, remote="owner/project", base="develop", pr_head=None):
        return delegated.conditions(self.work, self.st_, self.commit, remote, base, self.base_sha(base),
                                    pr_head or self.pr_head)

    def test_conditions_pass_when_every_one_holds(self):
        self.assertEqual(self.conditions(), (None, {"reviewer": "codex", "mode": "pr"}))

    def test_each_condition_fails_alone(self):
        touching = self.push_branch("agents", {"AGENTS.md": "x\n"})
        cases = {
            "no opt-in": (lambda: self.set_base('[build]\nci = "pr"\n'), {},
                          "project has not opted in: no [merge] production_branches on develop"),
            "control plane": (lambda: None, {"remote": "owner/hordle"}, "hordle is control plane; the owner merges it"),
            "production": (lambda: self.set_base('[merge]\nproduction_branches = ["develop"]\n'), {},
                           "develop is a production branch"),
            "no evidence": (lambda: None, {}, "no review evidence for the build GO"),
            "ci none": (lambda: self.set_base('[build]\nci = "none"\n' + OPT_IN), {},
                        "ci = none never qualifies for D1"),
            "protected path": (lambda: None, {"pr_head": touching}, "touches AGENTS.md, which the owner merges"),
        }
        for name, (setup, kwargs, reason) in cases.items():
            with self.subTest(name):
                setup()
                if name == "no evidence":
                    with mock.patch.object(delegated, "review_evidence", return_value=(None, reason)):
                        self.assertEqual(self.conditions(**kwargs), (reason, None))
                else:
                    self.assertEqual(self.conditions(**kwargs), (reason, None))
                self.set_base(OPT_IN)  # restore for the next case

    def test_the_first_failure_wins(self):
        touching = self.push_branch("agents", {"AGENTS.md": "x\n"})
        self.set_base('[merge]\nproduction_branches = ["develop"]\n')
        self.assertEqual(self.conditions(remote="owner/hordle", pr_head=touching)[0],
                         "hordle is control plane; the owner merges it")
        self.set_base("[build]\nci = \"none\"\n")  # no list at all: R3 speaks first
        self.assertEqual(self.conditions(remote="owner/hordle", pr_head=touching)[0],
                         "hordle has not opted in: no [merge] production_branches on develop")
        self.set_base('[merge]\nproduction_branches = ["develop"]\n')
        self.assertEqual(self.conditions(pr_head=touching)[0], "develop is a production branch")

    def test_a_toml_error_is_a_reason(self):
        self.set_base("[merge\n")
        reason, _ = self.conditions()
        self.assertTrue(reason.startswith("origin/develop:docs/relay/config.toml:"), reason)

    def test_a_pr_that_edits_the_list_is_refused_by_r8(self):
        head = self.push_branch("edits", {"docs/relay/config.toml": '[merge]\nproduction_branches = ["main", "x"]\n'})
        self.assertEqual(self.conditions(pr_head=head)[0],
                         "touches docs/relay/config.toml, which the owner merges")

    def test_a_pr_that_adds_the_list_is_not_opted_in(self):
        self.set_base('[build]\nci = "pr"\n')
        head = self.push_branch("adds", {"docs/relay/config.toml": OPT_IN})
        self.assertEqual(self.conditions(pr_head=head)[0],
                         "project has not opted in: no [merge] production_branches on develop")


class MergeAndPushTest(DelegatedBase):  # R10 steps 1, 2, 5, 6
    def setUp(self):
        super().setUp()
        self.ready_small_feature()
        self.pr()
        seen = owneractions.fingerprint(self.work, "tiny", fetch=False)
        self.pr_head = seen["pr_head"]
        self.base = self.base_sha()
        self.marker = os.path.join(self.tmp, "pushed")
        helpers.mark_pushes(self.origin, self.marker)

    def go(self, pr_head=None, base_sha=None):
        return delegated.merge_and_push(self.work, "owner/project", 7, "fix/tiny", pr_head or self.pr_head,
                                        "develop", base_sha or self.base)

    def origin_log(self, fmt, ref="develop"):
        return helpers.sh(self.origin, "git", "log", "-1", f"--format={fmt}", ref).strip()

    def worktrees(self):
        return helpers.sh(self.work, "git", "worktree", "list")

    def test_merge_commit_shape_and_push(self):
        sha = self.go()
        self.assertEqual(self.origin_log("%H"), sha)
        self.assertEqual(self.origin_log("%P"), f"{self.base} {self.pr_head}")
        self.assertEqual(self.origin_log("%s"), "Merge pull request #7 from owner/fix/tiny")
        self.assertIn("Delegated-merge: D1", self.origin_log("%b").splitlines())
        self.assertEqual(self.origin_log("%an"), "Relay Test")
        self.assertTrue(os.path.exists(self.marker))

    def test_the_temporary_worktree_is_removed_on_success_and_on_every_refusal(self):
        before = self.worktrees()
        self.go()
        self.assertEqual(self.worktrees(), before)
        with mock.patch.object(delegated, "reread_pr", return_value={"state": "CLOSED"}):
            with self.assertRaises(delegated.Refused):
                self.go(base_sha=self.origin_sha())
        self.assertEqual(self.worktrees(), before)

    def origin_sha(self):
        return helpers.sh(self.origin, "git", "rev-parse", "develop").strip()

    def test_a_conflict_is_refused_and_nothing_is_pushed(self):
        other = self.other_clone()
        helpers.write(os.path.join(other, "app.py"), "print(2)\n")  # the PR also adds app.py
        helpers.sh(other, "git", "add", "-A")
        helpers.sh(other, "git", "commit", "-q", "-m", "conflicting base")
        helpers.sh(other, "git", "push", "-q", "origin", "develop")
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        moved = self.base_sha()
        if os.path.exists(self.marker):
            os.remove(self.marker)  # the other clone's own push touched it
        with self.assertRaises(delegated.Refused) as cm:
            self.go(base_sha=moved)
        self.assertEqual(str(cm.exception), "not delegated (D1): the PR does not merge cleanly into develop")
        self.assertEqual(self.origin_sha(), moved)
        self.assertFalse(os.path.exists(self.marker))

    def test_a_moved_base_is_refused_by_the_lease(self):
        real = delegated.reread_pr

        def moves_the_base(*args):
            other = self.other_clone()
            helpers.write(os.path.join(other, "later.txt"), "later\n")
            helpers.sh(other, "git", "add", "-A")
            helpers.sh(other, "git", "commit", "-q", "-m", "somebody else")
            helpers.sh(other, "git", "push", "-q", "origin", "develop")
            return real(*args)
        with mock.patch.object(delegated, "reread_pr", side_effect=moves_the_base):
            with self.assertRaises(delegated.Refused) as cm:
                self.go()
        self.assertIn("not delegated (D1): GitHub refused the push to develop", str(cm.exception))
        self.assertEqual(self.origin_log("%s"), "somebody else")
        self.assertNotIn(self.pr_head, self.origin_log("%P"))

    def test_a_push_refused_by_a_rule_is_reported_with_its_message(self):
        helpers.reject_pushes(self.origin, "protected branch: only pull requests")
        with self.assertRaises(delegated.Refused) as cm:
            self.go()
        self.assertIn("protected branch: only pull requests", str(cm.exception))
        self.assertEqual(self.origin_sha(), self.base)

    def test_the_reread_refuses_a_closed_a_draft_a_retargeted_and_a_moved_pr(self):
        ok = {"state": "OPEN", "baseRefName": "develop", "headRefOid": self.pr_head, "isDraft": False}
        for name, change in (("closed", {"state": "CLOSED"}), ("draft", {"isDraft": True}),
                             ("retargeted", {"baseRefName": "other"}), ("moved", {"headRefOid": "f" * 40})):
            with self.subTest(name), mock.patch.object(delegated, "reread_pr", return_value={**ok, **change}):
                with self.assertRaises(delegated.Refused) as cm:
                    self.go()
                self.assertEqual(str(cm.exception), "not delegated (D1): the PR changed while it was checked")
                self.assertFalse(os.path.exists(self.marker))

    def test_an_unreadable_pr_before_the_push_is_refused(self):
        with mock.patch.object(delegated, "reread_pr", side_effect=RelayError("boom")):
            with self.assertRaises(delegated.Refused) as cm:
                self.go()
        self.assertEqual(str(cm.exception), "not delegated (D1): GitHub could not be read: boom")
        self.assertFalse(os.path.exists(self.marker))

    def test_the_reread_happens_after_the_merge_commit_and_before_the_push(self):
        calls, hookless, reread = [], gitops.hookless, delegated.reread_pr

        def spy_git(root, *args, **kw):
            if args[0] in ("merge", "push"):
                calls.append(args[0])
            return hookless(root, *args, **kw)

        def spy_reread(*args):
            calls.append("reread")
            return reread(*args)
        with mock.patch.object(gitops, "hookless", side_effect=spy_git), \
                mock.patch.object(delegated, "reread_pr", side_effect=spy_reread):
            self.go()
        self.assertEqual(calls, ["merge", "reread", "push"])

    def test_local_hooks_do_not_run(self):
        for name in ("pre-merge-commit", "commit-msg", "pre-push", "post-checkout", "pre-commit"):
            helpers.hook(os.path.join(self.work, ".git"), name, "exit 1\n")
        self.go()
        self.assertTrue(os.path.exists(self.marker))

    def test_head_already_in_the_base_is_refused(self):
        ancestor = helpers.sh(self.work, "git", "rev-parse", f"{self.base}~0").strip()  # the base itself
        with self.assertRaises(delegated.Refused) as cm:
            self.go(pr_head=ancestor)
        self.assertEqual(str(cm.exception),
                         "not delegated (D1): develop already holds the PR head; nothing to merge")
        self.assertFalse(os.path.exists(self.marker))

    def test_the_lease_names_the_checked_base_commit(self):
        seen, hookless = [], gitops.hookless

        def spy(root, *args, **kw):
            if args[0] == "push":
                seen.append(args)
            return hookless(root, *args, **kw)
        with mock.patch.object(gitops, "hookless", side_effect=spy):
            sha = self.go()
        (argv,) = seen
        self.assertIn(f"--force-with-lease=refs/heads/develop:{self.base}", argv)
        self.assertIn(f"{sha}:refs/heads/develop", argv)


class WaitTest(DelegatedBase):  # R10 step 4
    HEAD = "a" * 40

    def wait(self, reads):
        """wait_for_merge with pr_state answering `reads` in turn (the last repeats); a RelayError instance raises."""
        clock = helpers.FakeClock()
        queue = list(reads)

        def pr_state(*args):
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return {"baseRefName": "develop", "headRefOid": self.HEAD, "isDraft": False, **item}
        with mock.patch.object(delegated, "clock", clock.monotonic), mock.patch.object(delegated, "sleep", clock.sleep), \
                mock.patch.object(gitops, "pr_state", side_effect=pr_state):
            outcome = delegated.wait_for_merge(self.work, "owner/project", 7, "develop", self.HEAD)
        self.clock = clock
        return outcome

    def test_merged_on_the_first_read_does_not_sleep(self):
        self.assertEqual(self.wait([{"state": "MERGED"}]).name, "merged")
        self.assertEqual(self.clock.sleeps, [])

    def test_open_for_a_few_reads_then_merged(self):
        outcome = self.wait([{"state": "OPEN"}] * 3 + [{"state": "MERGED"}])
        self.assertEqual(outcome.name, "merged")
        self.assertEqual(self.clock.sleeps, [2, 2, 2])

    def test_still_open_when_the_time_ends_is_a_timeout(self):
        self.assertEqual(self.wait([{"state": "OPEN"}]).name, "timeout")
        self.assertGreaterEqual(self.clock.now, 60)
        self.assertLess(self.clock.now, 63)

    def test_closed_is_reported(self):
        self.assertEqual(self.wait([{"state": "CLOSED"}]).name, "closed")

    def test_a_different_base_is_retargeted(self):
        outcome = self.wait([{"state": "OPEN", "baseRefName": "other"}])
        self.assertEqual((outcome.name, outcome.detail), ("retargeted", "other"))

    def test_a_different_head_is_head_moved(self):
        outcome = self.wait([{"state": "OPEN", "headRefOid": "b" * 40}])
        self.assertEqual((outcome.name, outcome.detail), ("head-moved", "b" * 40))

    def test_a_failed_read_is_retried_and_a_later_good_read_wins(self):
        self.assertEqual(self.wait([RelayError("gh down"), {"state": "OPEN"}, {"state": "MERGED"}]).name, "merged")

    def test_a_read_still_failing_at_the_end_is_read_failed(self):
        outcome = self.wait([RelayError("gh down")])
        self.assertEqual((outcome.name, outcome.detail), ("read-failed", "gh down"))

    def test_a_failure_then_a_good_open_read_that_stays_open_is_a_timeout(self):
        self.assertEqual(self.wait([RelayError("gh down"), {"state": "OPEN"}]).name, "timeout")

    def test_describe_texts_and_exit_codes(self):
        def d(name, detail=None):
            return delegated.describe(delegated.Outcome(name, detail), "develop", 7, "abcdef0123")
        self.assertEqual(d("merged"), ("merged into develop", 0))
        self.assertEqual(d("timeout"), ("merged into develop; GitHub has not marked PR #7 merged yet", 0))
        self.assertEqual(d("retargeted", "other"),
                         ("pushed the merge to develop; PR #7 now targets other and stays open: tell the owner", 1))
        self.assertEqual(d("head-moved", "b" * 40),
                         ("merged the checked head abcdef0 into develop; PR #7 has later commits that are not "
                          "merged and stays open: tell the owner", 1))
        self.assertEqual(d("closed"), ("merged into develop, but PR #7 was closed without GitHub marking it merged: "
                                       "tell the owner", 1))
        self.assertEqual(d("read-failed", "gh down"), ("GitHub could not be read to confirm PR #7: gh down", 1))


class DelegatedCommandTest(DelegatedBase):  # R2, R9: the release role's `relay merge`, up to the conditions
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {"HORDLE_ROLE": "release"})
        p.start()
        self.addCleanup(p.stop)
        self.ready_small_feature()
        self.pr()
        self.set_base(OPT_IN)
        self.before = self.origin_develop()

    def origin_develop(self):
        return helpers.sh(self.origin, "git", "rev-parse", "develop").strip()

    def gh_calls(self, prefix):
        if not os.path.exists(self.gh_log):
            return []
        with open(self.gh_log) as f:
            return [l for l in f if l.startswith(prefix)]

    def refused(self, reason):
        self.before = self.origin_develop()
        self.assertEqual(self.relay("merge", "7"), 1, self.last_out)
        self.assertIn(f"relay: not delegated (D1): {reason}", self.last_err)
        self.assertEqual(self.gh_calls("pr merge"), [])
        self.assertEqual(self.origin_develop(), self.before)

    def test_each_condition_refuses_through_the_command_with_nothing_merged(self):
        with mock.patch.object(delegated, "merge_and_push") as pushed:
            self.set_base('[build]\nci = "pr"\n')
            self.refused("project has not opted in: no [merge] production_branches on develop")  # R3
            self.set_base(OPT_IN)
            with mock.patch("relaylib.owneractions.github_repo", return_value="owner/hordle"):
                self.refused("hordle is control plane; the owner merges it")  # R4
            self.set_base('[merge]\nproduction_branches = ["develop"]\n')
            self.refused("develop is a production branch")  # R5
            self.set_base(OPT_IN)
            with mock.patch.object(delegated, "review_evidence", return_value=(None, delegated.NO_EVIDENCE)):
                self.refused("no review evidence for the build GO")  # R6
            with mock.patch.object(delegated, "protected_path", return_value="AGENTS.md"):
                self.refused("touches AGENTS.md, which the owner merges")  # R8
            self.set_base('[build]\nci = "none"\n' + OPT_IN)
            self.refused("ci = none never qualifies for D1")  # R7
            pushed.assert_not_called()

    def test_readiness_failures_are_prefixed(self):
        real = owneractions.state_at
        cases = [
            ("stale GO: develop moved", mock.patch.object(freshness, "check", return_value=(False, "develop moved"))),
            ("a review is still running",
             mock.patch.object(owneractions, "state_at", side_effect=lambda *a: {**real(*a), "confirming": True})),
        ]
        for reason, patch in cases:
            with self.subTest(reason), patch:
                self.refused(reason)
        for reason, over in (("PR is not open", {"state": "CLOSED"}),
                             ("CI is failing; wait for green", None),
                             ("PR code differs from the published revision", {"headRefOid": "f" * 40})):
            with self.subTest(reason):
                if over is None:
                    self.pr(runs=[{"status": "completed", "conclusion": "failure"}])
                else:
                    self.pr(**over)
                self.refused(reason)
        self.pr()

    def test_a_state_that_changed_is_refused_with_the_prefix(self):
        seen = owneractions.fingerprint(self.work, "tiny", fetch=False)
        with self.assertRaises(owneractions.Conflict) as cm:
            owneractions.merge_delegated(self.work, "tiny", dict(seen, commit="0" * 40))
        self.assertTrue(str(cm.exception).startswith("not delegated (D1): changed since you looked"))
        self.assertIsNotNone(cm.exception.fresh)

    def test_a_draft_is_refused(self):
        self.pr(isDraft=True)
        self.refused("the PR is a draft")

    def test_conditions_run_after_readiness_and_before_the_merge(self):
        calls = []
        readiness, conditions = owneractions.merge_readiness, delegated.conditions

        def spy_readiness(*a, **k):
            calls.append("readiness")
            return readiness(*a, **k)

        def spy_conditions(*a, **k):
            calls.append("conditions")
            return conditions(*a, **k)
        with mock.patch.object(owneractions, "merge_readiness", side_effect=spy_readiness), \
                mock.patch.object(delegated, "conditions", side_effect=spy_conditions):
            self.relay("merge", "7")
        self.assertEqual(calls, ["readiness", "conditions"])

    def test_a_base_that_moves_while_checking_is_refused(self):
        readiness = owneractions.merge_readiness

        def moves(*a, **k):
            info = readiness(*a, **k)
            other = self.other_clone()
            helpers.write(os.path.join(other, "later.txt"), "x\n")
            helpers.sh(other, "git", "add", "-A")
            helpers.sh(other, "git", "commit", "-q", "-m", "base moves")
            helpers.sh(other, "git", "push", "-q", "origin", "develop")
            helpers.sh(self.work, "git", "fetch", "-q", "origin")
            return info
        with mock.patch.object(owneractions, "merge_readiness", side_effect=moves):
            self.assertEqual(self.relay("merge", "7"), 1)
        self.assertIn("relay: not delegated (D1): the PR changed while it was checked", self.last_err)

    def test_a_busy_action_lock_is_refused(self):
        child = helpers.hold_action_lock()
        try:
            self.assertEqual(self.relay("merge", "7"), 1)
        finally:
            helpers.release(child)
        self.assertIn("busy", self.last_err)
        self.assertEqual(self.gh_calls("pr merge"), [])

    def test_the_merge_is_not_built_before_the_record_is(self):
        # task 13 stops after the conditions: no intermediate commit can push a merge without its record
        self.assertEqual(self.relay("merge", "7"), 1)
        self.assertIn("not delegated (D1): the delegated merge is not built yet", self.last_err)
        self.assertEqual(self.origin_develop(), self.before)


def _only_own_tests():
    """Run only this file's tests, not the CommandsTest ones the classes inherit."""
    for cls in [c for c in globals().values() if isinstance(c, type) and issubclass(c, test_commands.CommandsTest)]:
        for name in dir(test_commands.CommandsTest):
            if name.startswith("test_") and name not in cls.__dict__ and not any(
                    name in base.__dict__ and base is not test_commands.CommandsTest and base.__module__ == __name__
                    for base in cls.__mro__):
                setattr(cls, name, None)


_only_own_tests()
