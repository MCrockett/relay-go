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
