"""Cached builds give the same snapshot as uncached ones (dashboard-scan Task 13: R15, F7, R18). The F2 error rows
are checked in tests/test_ui_snapshot.py SharedStoreTest."""
import json, os, tempfile, unittest
from unittest import mock
from relaylib import gitops, merged, owneractions, scancache, state
from relaylib.ui import snapshot
from tests import helpers

VOLATILE = {"build_stats", "age_seconds", "built_at"}


def strip(value):
    """The snapshot without its cost line and without fields that move with the clock."""
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


class EquivalenceTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = temp.name
        self.root = os.path.join(self.tmp, "projects")
        os.makedirs(self.root)
        self.origin, self.work = helpers.make_repo(self.root)
        self.feature(self.work, "feat/live", "live", stage="build", status="drafting", pr=7)
        self.feature(self.work, "feat/old", "old", stage="build", status="ready-to-merge", pr=8, back=True)
        helper = helpers.clone(self.origin, os.path.join(self.tmp, "helper"))
        helpers._identify(helper)
        self.feature(helper, "feat/remote", "remote", stage="spec", status="waiting-owner")  # origin only
        helpers.sh(helper, "git", "fetch", "-q", "origin")
        helpers.sh(helper, "git", "switch", "-qc", "feat/stack", "origin/feat/live")  # inherits live's copy
        self.feature(helper, None, "stack", stage="plan", status="drafting")
        helpers.sh(helper, "git", "switch", "-qc", "feat/broken", "origin/develop")
        helpers.write(os.path.join(state.feature_dir(helper, "broken"), "state.md"), "not a state file\n")
        self.publish(helper)
        helpers.sh(self.work, "git", "fetch", "-q", "origin")
        self.tree = os.path.join(self.tmp, "linked")
        helpers.sh(self.work, "git", "worktree", "add", "-q", self.tree, "-b", "feat/linked", "origin/feat/stack")
        os.makedirs(os.path.join(self.tmp, "b"))
        _, self.second = helpers.make_repo(os.path.join(self.tmp, "b"))
        os.rename(self.second, os.path.join(self.root, "second"))
        self.second = os.path.join(self.root, "second")
        self.feature(self.second, "feat/b", "bee", stage="spec", status="waiting-owner")
        helpers.name_test_repos(self)
        api = os.path.join(self.tmp, "api.json")
        open_pr = {"state": "OPEN", "baseRefName": "develop", "headRefOid": "0" * 40, "isDraft": False,
                   "statusCheckRollup": [], "mergedAt": None}
        helpers.write(api, json.dumps({"pr view 7 ": {**open_pr, "number": 7}, "pr view 8 ": {**open_pr, "number": 8}}))
        self.gh_log = os.path.join(self.tmp, "gh.log")
        patch = mock.patch.dict(os.environ, {
            "RELAY_HOME": os.path.join(self.tmp, "home"), "RELAY_ROOT": self.root, "HOME": self.tmp,
            "CODEX_HOME": os.path.join(self.tmp, "codex"), "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude"),
            "RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH), "FAKE_GH_API": api,
            "FAKE_GH_LOG": self.gh_log})
        patch.start()
        self.addCleanup(patch.stop)
        helpers.sh(self.work, "git", "switch", "-q", "feat/live")
        merged.mark_merged(self.work, 8)  # F7: GitHub still says OPEN for 8

    def feature(self, repo, branch, slug, back=False, **fields):
        if branch:
            helpers.sh(repo, "git", "switch", "-qc", branch, "develop")
        st = state.new_state(slug, os.path.basename(repo), {"provider": "codex", "session": "s-" + slug},
                             branch or gitops.current_branch(repo))
        st.update(fields)
        state.write_state(state.state_path(repo, slug), st)
        self.publish(repo)
        if back:
            helpers.sh(repo, "git", "switch", "-q", "develop")

    @staticmethod
    def publish(repo):
        helpers.sh(repo, "git", "add", ".")
        helpers.sh(repo, "git", "commit", "-qm", "fixture")
        helpers.sh(repo, "git", "push", "-qu", "origin", "HEAD")

    def done_heads(self, data):
        """Each done feature's seen.pr_head, then blanked: a build does not ask GitHub about it (Task 6)."""
        heads, done = [], {r["feature"] for r in data["rows"] if r.get("stage") == "done"}
        for item in data["rows"] + data["features"]:
            seen = item.get("seen")
            if item.get("feature") in done and isinstance(seen, dict):
                heads.append(seen.get("pr_head"))
                seen["pr_head"] = "(done)"
        return heads

    def test_disabled_cold_and_warm_builds_agree(self):
        with mock.patch("time.time", return_value=1_794_500_000.0):  # ages and wait times do not move
            plain = strip(snapshot.build(store=scancache.Store(enabled=False)))
            store = scancache.Store()
            cold = strip(snapshot.build(store=store))
            warm = strip(snapshot.build(store=store))
        self.assertTrue(all(h for h in self.done_heads(plain)))
        for got in (cold, warm):
            self.assertEqual(set(self.done_heads(got)), {None})
        names = sorted((r["repo"] or "", r["feature"]) for r in plain["rows"])
        for slug in ("live", "old", "remote", "stack", "bee"):
            self.assertIn(slug, [n[1] for n in names])
        self.assertNotIn("broken", [n[1] for n in names])  # an unreadable origin state.md is skipped, as before
        old = next(r for r in plain["rows"] if r["feature"] == "old")
        self.assertEqual(old["stage"], "done")  # F7: the merged record wins over GitHub's OPEN
        for name, got in (("cold", cold), ("warm", warm)):
            with self.subTest(build=name):
                self.assertEqual(got.keys(), plain.keys())
                for key in plain:
                    self.assertEqual(got[key], plain[key], key)

    def test_a_merge_action_on_a_recorded_merge_still_asks_github(self):
        data = snapshot.build(store=scancache.Store())
        seen = next(r for r in data["rows"] if r["feature"] == "old")["seen"]
        open(self.gh_log, "w").close()
        try:
            owneractions.merge(self.work, "old", seen)
        except Exception:
            pass  # refused or failed: only the question matters here
        with open(self.gh_log) as f:
            self.assertIn("pr view 8", f.read())


if __name__ == "__main__":
    unittest.main()
