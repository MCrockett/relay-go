import os, shutil, tempfile, unittest
from unittest import mock
from relaylib import merged
from tests import helpers


class MergedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, "pr.json")
        p = mock.patch.dict(os.environ, {"RELAY_HOME": os.path.join(self.tmp, "home"), "FAKE_GH_JSON": self.data,
                                         "RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH)})
        p.start()
        self.addCleanup(p.stop)
        self.st = {"repo": "r", "status": "ready-to-merge", "pr": 4}

    def test_open_pr_is_not_done(self):
        helpers.write(self.data, '{"state": "OPEN"}')
        self.assertFalse(merged.is_done(self.tmp, self.st))

    def test_merged_is_done_and_cached(self):
        helpers.write(self.data, '{"state": "MERGED"}')
        self.assertTrue(merged.is_done(self.tmp, self.st))
        os.remove(self.data)  # gh would now fail; the cache answers
        self.assertTrue(merged.is_done(self.tmp, self.st))

    def test_gh_failure_is_not_done(self):
        self.assertFalse(merged.is_done(self.tmp, self.st))

    def test_other_statuses(self):
        self.assertFalse(merged.is_done(self.tmp, {"status": "in-review", "pr": 4}))
        self.assertTrue(merged.is_done(self.tmp, {"status": "done"}))

    def test_cache_is_keyed_by_origin_not_repo_name(self):
        repo = os.path.join(self.tmp, "api")
        os.makedirs(repo)
        helpers.sh(repo, "git", "init", "-q")
        helpers.sh(repo, "git", "remote", "add", "origin", "https://github.com/org-b/api.git")
        os.makedirs(os.environ["RELAY_HOME"], exist_ok=True)
        helpers.write(os.path.join(os.environ["RELAY_HOME"], "merged.json"),
                      '{"https://github.com/org-a/api.git#4": true, "api#4": true}')
        helpers.write(self.data, '{"state": "OPEN"}')
        self.assertFalse(merged.is_done(repo, {"repo": "api", "status": "ready-to-merge", "pr": 4}))

    def test_merged_at_from_gh_then_cached(self):
        helpers.write(self.data, '{"state": "MERGED", "mergedAt": "2026-10-06T20:00:00Z"}')
        st = {"status": "ready-to-merge", "pr": 7}
        self.assertEqual(merged.merged_at(self.tmp, st), 1791316800.0)
        os.remove(self.data)  # gh would now fail; the cache answers
        self.assertEqual(merged.merged_at(self.tmp, st), 1791316800.0)
        self.assertFalse(merged.is_done(self.tmp, {"status": "ready-to-merge", "pr": 8}))

    def test_merged_at_falls_back_to_the_merge_commit(self):
        origin, work = helpers.make_repo(self.tmp)
        helpers.sh(work, "git", "checkout", "-q", "-b", "feat/a")
        helpers.write(os.path.join(work, "a.txt"), "a\n")
        helpers.sh(work, "git", "add", "a.txt")
        helpers.sh(work, "git", "commit", "-q", "-m", "a")
        x = helpers.sh(work, "git", "rev-parse", "HEAD").strip()
        helpers.sh(work, "git", "checkout", "-q", "develop")
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_DATE": "@1791316800 +0000"}):
            helpers.sh(work, "git", "merge", "-q", "--no-ff", "-m", "merge", "feat/a")
        helpers.sh(work, "git", "push", "-q", "origin", "develop")
        helpers.sh(work, "git", "fetch", "-q", "origin")
        self.assertEqual(merged.merged_at(work, {"status": "ready-to-merge", "pr": 7}, branch_head=x), 1791316800.0)
        self.assertIsNone(merged.merged_at(work, {"status": "drafting"}, branch_head="0" * 40))

