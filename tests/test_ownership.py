import os, shutil, tempfile, unittest
from unittest import mock
from relaylib import gitops, ownership, state
from relaylib.errors import RelayError
from tests import helpers


def owner(session):
    return {"provider": "claude", "session": session, "worktree": "/w", "since": "t"}


class OwnershipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.work = helpers.make_repo(self.tmp)
        self.gh = os.path.join(self.tmp, "pr.json")
        helpers.write(self.gh, '{"state": "OPEN"}')
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "home"), "FAKE_GH_JSON": self.gh,
                                         "RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH)})
        p.start()
        self.addCleanup(p.stop)

    def publish(self, repo, slug, session, status="drafting", handoff=False):
        helpers.sh(repo, "git", "switch", "-q", "-c", f"feat/{slug}")
        st = state.new_state(slug, "r", owner(session), f"feat/{slug}")
        st["status"], st["pr"] = status, 3
        state.write_state(state.state_path(repo, slug), st)
        if handoff:
            helpers.write(os.path.join(state.feature_dir(repo, slug), "handoff.md"), "notes\n")
        gitops.commit_paths_under(repo, f"docs/relay/{slug}", "relay: test")
        gitops.push(repo, f"feat/{slug}")
        helpers.sh(repo, "git", "switch", "-q", "develop")

    def test_same_session_may_write(self):
        self.publish(self.work, "a", "s1")
        ownership.check_can_write(self.work, "s1")

    def test_other_machine_sees_remote_claim(self):
        self.publish(self.work, "a", "s1")
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        with self.assertRaisesRegex(RelayError, "another session owns"):
            ownership.check_can_write(other, "s2")

    def test_handoff_releases(self):
        self.publish(self.work, "a", "s1", handoff=True)
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        ownership.check_can_write(other, "s2")

    def test_ready_to_merge_holds_until_merged(self):
        self.publish(self.work, "a", "s1", status="ready-to-merge")
        with self.assertRaises(RelayError):
            ownership.check_can_write(self.work, "s2")
        helpers.write(self.gh, '{"state": "MERGED"}')
        ownership.check_can_write(self.work, "s2")

    def test_local_worktree_claim(self):
        wt = os.path.join(self.tmp, "wt")
        helpers.sh(self.work, "git", "worktree", "add", "-q", "-b", "feat/local", wt)
        state.write_state(state.state_path(wt, "local"), state.new_state("local", "r", owner("s9"), "feat/local"))
        with self.assertRaisesRegex(RelayError, "s9"):
            ownership.check_can_write(self.work, "s1")

    def test_uncommitted_local_handoff_does_not_release(self):
        wt = os.path.join(self.tmp, "wt")
        helpers.sh(self.work, "git", "worktree", "add", "-q", "-b", "feat/local", wt)
        state.write_state(state.state_path(wt, "local"), state.new_state("local", "r", owner("s9"), "feat/local"))
        helpers.write(os.path.join(state.feature_dir(wt, "local"), "handoff.md"), "skeleton, not committed\n")
        with self.assertRaisesRegex(RelayError, "s9"):
            ownership.check_can_write(self.work, "s1")

    def test_offline_refuses(self):
        helpers.sh(self.work, "git", "remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        with self.assertRaisesRegex(RelayError, "cannot fetch"):
            ownership.check_can_write(self.work, "s1")


    def test_a_handoff_releases_every_feature_of_that_session(self):
        self.publish(self.work, "one", "s1")
        self.publish(self.work, "two", "s1", handoff=True)
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        ownership.check_can_write(other, "s2")

    def test_another_sessions_claim_still_blocks(self):
        self.publish(self.work, "one", "s1", handoff=True)
        self.publish(self.work, "two", "s9")
        other = helpers.clone(self.origin, os.path.join(self.tmp, "other"))
        with self.assertRaisesRegex(RelayError, "s9"):
            ownership.check_can_write(other, "s2")

    def test_an_unpushed_handoff_does_not_release_the_session(self):
        self.publish(self.work, "one", "s1")
        self.publish(self.work, "two", "s1")
        wt = os.path.join(self.tmp, "wt")
        helpers.sh(self.work, "git", "worktree", "add", "-q", wt, "feat/two")
        helpers.write(os.path.join(state.feature_dir(wt, "two"), "handoff.md"), "committed, never pushed\n")
        helpers.sh(wt, "git", "add", "-A")
        helpers.sh(wt, "git", "commit", "-q", "-m", "relay: handoff two")
        with self.assertRaisesRegex(RelayError, "another session owns"):
            ownership.check_can_write(self.work, "s2")

    def test_inherited_copies_on_stacked_branches_are_ignored(self):
        self.publish(self.work, "one", "s1")
        helpers.sh(self.work, "git", "switch", "-q", "feat/one")          # stack: two starts from one
        helpers.sh(self.work, "git", "switch", "-q", "-c", "feat/two")
        st = state.new_state("two", "r", owner("s2"), "feat/two")
        state.write_state(state.state_path(self.work, "two"), st)
        gitops.commit_paths_under(self.work, "docs/relay/two", "relay: two")
        gitops.push(self.work, "feat/two")
        claims = ownership.collect(self.work)
        self.assertEqual(sorted((c.slug, c.where) for c in claims if c.where.startswith("origin/")),
                         [("one", "origin/feat/one"), ("two", "origin/feat/two")])

if __name__ == "__main__":
    unittest.main()
