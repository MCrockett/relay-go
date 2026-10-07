import datetime, json, os, shutil, tempfile, unittest
from unittest import mock
from relaylib import config, gitops, holds, state
from tests import helpers

T0 = 1791316800.0  # 2026-10-06 20:00 UTC


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()


class HoldsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "home"),
                                         "RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh", "#!/bin/sh\nexit 1\n")})
        p.start()
        self.addCleanup(p.stop)
        self.origin, self.work = helpers.make_repo(self.tmp)

    def commit(self, st, subject, at, push=True):
        """Write st on its own branch (created from develop when new) and commit it at time `at`."""
        branch = st["branch"]
        if branch not in helpers.sh(self.work, "git", "branch", "--format=%(refname:short)").split():
            helpers.sh(self.work, "git", "checkout", "-q", "-b", branch, "develop")
        else:
            helpers.sh(self.work, "git", "checkout", "-q", branch)
        with mock.patch.object(state, "now_iso", return_value=iso(at)):   # like relay, every commit touches state
            state.write_state(state.state_path(self.work, st["feature"]), st)
        helpers.sh(self.work, "git", "add", "-A")
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_DATE": f"@{int(at)} +0000",
                                          "GIT_AUTHOR_DATE": f"@{int(at)} +0000"}):
            helpers.sh(self.work, "git", "commit", "-q", "-m", subject)
        if push:
            helpers.sh(self.work, "git", "push", "-q", "-u", "origin", branch)
        return helpers.sh(self.work, "git", "rev-parse", "HEAD").strip()

    def merge(self, branch, at, delete=True):
        helpers.sh(self.work, "git", "checkout", "-q", "develop")
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_DATE": f"@{int(at)} +0000",
                                          "GIT_AUTHOR_DATE": f"@{int(at)} +0000"}):
            helpers.sh(self.work, "git", "merge", "-q", "--no-ff", "-m", f"merge {branch}", branch)
        helpers.sh(self.work, "git", "push", "-q", "origin", "develop")
        if delete:
            helpers.sh(self.work, "git", "push", "-q", "origin", "--delete", branch)

    def feature(self, slug="a", session="S1", provider="claude", since=T0):
        return state.new_state(slug, "work", {"provider": provider, "session": session, "since": iso(since)},
                               f"feat/{slug}")

    def test_take_moves_the_window_and_stage_follows_commits(self):
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        st.update(stage="spec")
        self.commit(st, "relay: submit idea", T0 + 100)
        st["owner"] = {"provider": "codex", "session": "C1", "since": iso(T0 + 200)}
        self.commit(st, "relay: codex takes a", T0 + 250)
        out = holds.windows([self.work])
        w = {x["session"]: x for x in out["windows"]}
        self.assertEqual((w["S1"]["start"], w["S1"]["end"]), (T0, T0 + 250))
        self.assertEqual(w["S1"]["stages"], [[T0, "idea"], [T0 + 100, "spec"]])
        self.assertEqual((w["C1"]["start"], w["C1"]["end"]), (T0 + 200, None))   # since, not commit time
        self.assertEqual((w["C1"]["repo"], w["C1"]["slug"], w["C1"]["branch"], w["C1"]["provider"]),
                         ("origin", "a", "feat/a", "codex"))
        self.assertEqual(out["flags"], [])

    def test_handoff_on_one_feature_closes_the_sessions_other_windows(self):
        a, b = self.feature("a"), self.feature("b")
        self.commit(a, "relay: new a", T0)
        self.commit(b, "relay: new b", T0 + 10)
        self.commit(a, "relay: handoff a", T0 + 300)
        ends = {x["slug"]: x["end"] for x in holds.windows([self.work])["windows"] if x["session"] == "S1"}
        self.assertEqual(ends, {"a": T0 + 300, "b": T0 + 300})

    def test_merge_ends_the_window_even_though_state_says_ready(self):
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        st.update(stage="build", status="ready-to-merge", pr=7)
        self.commit(st, "relay: build GO", T0 + 100)
        self.merge("feat/a", T0 + 500)
        (w,) = holds.windows([self.work])["windows"]
        self.assertEqual(w["end"], T0 + 500)

    def test_unknown_merge_time_closes_at_the_last_state_commit_and_flags(self):
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        st.update(stage="build", status="ready-to-merge", pr=7)
        self.commit(st, "relay: build GO", T0 + 100)
        os.makedirs(os.environ["RELAY_HOME"])
        helpers.write(os.path.join(os.environ["RELAY_HOME"], "merged.json"),
                      json.dumps({f"{gitops.origin_url(self.work)}#7": True}))
        out = holds.windows([self.work])
        self.assertEqual(out["windows"][0]["end"], T0 + 100)
        self.assertEqual(out["windows"][0]["last_commit"], T0 + 100)
        self.assertEqual(out["flags"], ["merge time unknown for origin a"])

    def test_merged_without_a_pr_number_or_gh_uses_origin_history(self):
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        st.update(stage="build", status="ready-to-merge")
        self.commit(st, "relay: build GO", T0 + 100)
        self.merge("feat/a", T0 + 500)
        (w,) = holds.windows([self.work])["windows"]
        self.assertEqual(w["end"], T0 + 500)        # found only on origin/develop, so merged; fallback reachable

    def test_merged_branch_left_on_origin_is_found_without_gh(self):
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        st.update(stage="build", status="ready-to-merge")
        self.commit(st, "relay: build GO", T0 + 100)
        self.merge("feat/a", T0 + 500, delete=False)
        (w,) = holds.windows([self.work])["windows"]
        self.assertEqual(w["end"], T0 + 500)

    def test_open_feature_is_not_merged(self):
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        (w,) = holds.windows([self.work])["windows"]
        self.assertIsNone(w["end"])

    def test_merge_never_extends_an_earlier_end(self):
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        self.commit(st, "relay: handoff a", T0 + 300)
        st["owner"] = {"provider": "codex", "session": "C1", "since": iso(T0 + 400)}
        st.update(stage="build", status="ready-to-merge")
        self.commit(st, "relay: codex takes a", T0 + 400)
        self.merge("feat/a", T0 + 500)
        w = {x["session"]: x for x in holds.windows([self.work])["windows"]}
        self.assertEqual(w["S1"]["end"], T0 + 300)
        self.assertEqual(w["C1"]["end"], T0 + 500)

    def test_state_history_is_cached_by_commit(self):
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        st.update(stage="spec")
        self.commit(st, "relay: submit idea", T0 + 100)
        first = holds.windows([self.work])
        cache = os.path.join(os.environ["RELAY_HOME"], "state-history.json")
        self.assertEqual(oct(os.stat(cache).st_mode & 0o777), "0o600")
        with open(cache) as f:
            saved = json.load(f)
        self.assertTrue(all(set(v) <= {"stage", "status", "branch", "pr", "owner"} for v in saved["states"].values()))
        self.assertEqual(len(saved["logs"]), 1)
        with mock.patch.object(gitops, "cat_files", wraps=gitops.cat_files) as cat, \
                mock.patch.object(gitops, "git", wraps=gitops.git) as git:
            self.assertEqual(holds.windows([self.work]), first)
        self.assertEqual([c for c in cat.call_args_list if c.args[1]], [])   # no historical state read again
        self.assertNotIn("log", [c.args[1] for c in git.call_args_list])    # nor the history of an unmoved ref
        st.update(stage="plan")
        self.commit(st, "relay: submit spec", T0 + 200)
        (w,) = holds.windows([self.work])["windows"]
        self.assertEqual(w["stages"][-1], [T0 + 200, "plan"])               # a moved ref is read again
        with open(cache) as f:
            self.assertEqual(len(json.load(f)["logs"]), 1)                  # and its old log is dropped

    def test_corrupt_or_unwritable_history_cache(self):
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        os.makedirs(os.environ["RELAY_HOME"])
        helpers.write(os.path.join(os.environ["RELAY_HOME"], "state-history.json"), "{oops")
        self.assertEqual(len(holds.windows([self.work])["windows"]), 1)
        with mock.patch.object(config, "atomic_write", side_effect=OSError("read-only")):
            os.remove(os.path.join(os.environ["RELAY_HOME"], "state-history.json"))
            out = holds.windows([self.work])
        self.assertEqual(len(out["windows"]), 1)
        self.assertIn("state history cache not saved", out["flags"])

    def test_one_unreadable_repo_does_not_hide_a_healthy_one(self):            # F5
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        broken = os.path.join(self.tmp, "broken")
        os.makedirs(os.path.join(broken, ".git"))
        self.assertEqual(len(holds.windows([broken, self.work])["windows"]), 1)

    def test_done_stage_closes_and_worktrees_count_once(self):
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        st.update(stage="done", status="done")
        self.commit(st, "relay: done a", T0 + 400)
        helpers.sh(self.work, "git", "checkout", "-q", "develop")
        tree = os.path.join(self.tmp, "tree")
        helpers.sh(self.work, "git", "worktree", "add", "-q", tree, "feat/a")
        ws = holds.windows([self.work, tree])["windows"]
        self.assertEqual(len(ws), 1)
        self.assertEqual(ws[0]["end"], T0 + 400)

    def test_capitalized_docs_folder_is_found(self):
        os.makedirs(os.path.join(self.work, "Docs", "relay"))      # relay_dir keeps this spelling
        st = self.feature()
        self.commit(st, "relay: new a", T0)
        st.update(stage="spec")
        self.commit(st, "relay: submit idea", T0 + 100)
        self.assertIn("Docs/relay/a/state.md", helpers.sh(self.work, "git", "ls-files"))
        (w,) = holds.windows([self.work])["windows"]
        self.assertEqual((w["slug"], w["start"], w["end"]), ("a", T0, None))
        self.assertEqual(w["stages"], [[T0, "idea"], [T0 + 100, "spec"]])

    def test_owner_written_work_has_windows(self):
        st = self.feature(provider="owner", session="owner@host")
        self.commit(st, "relay: new a", T0)
        (w,) = holds.windows([self.work])["windows"]
        self.assertEqual(w["provider"], "owner")


if __name__ == "__main__":
    unittest.main()
