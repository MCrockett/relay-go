import os, shutil, tempfile, unittest
from unittest import mock
from relaylib import basebranch
from relaylib.errors import RelayError
from tests import helpers

DAY = 86400
T0 = 1_790_000_000


def commit(repo, name, when):
    helpers.write(os.path.join(repo, name), name + "\n")
    helpers.sh(repo, "git", "add", name)
    stamp = f"@{when} +0000"
    with mock.patch.dict(os.environ, {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}):
        helpers.sh(repo, "git", "commit", "-q", "-m", name)


class BaseBranchTest(unittest.TestCase):
    """A seed repo with main and develop, pushed to a bare origin whose HEAD is `head`; then a fresh clone, which
    has origin/HEAD as a real clone does."""

    def make(self, head, develop_days_old, main_ahead=1, develop_ahead=0):
        tmp = tempfile.mkdtemp(prefix="relaytest-")
        self.addCleanup(shutil.rmtree, tmp, True)
        origin, seed = os.path.join(tmp, "origin.git"), os.path.join(tmp, "seed")
        helpers.sh(tmp, "git", "init", "-q", "--bare", "-b", head, origin)
        helpers.sh(tmp, "git", "init", "-q", "-b", "main", seed)
        helpers._identify(seed)
        commit(seed, "a", T0)
        helpers.sh(seed, "git", "branch", "develop")
        helpers.sh(seed, "git", "switch", "-q", "develop")
        for i in range(develop_ahead):
            commit(seed, f"d{i}", T0 + i)
        helpers.sh(seed, "git", "switch", "-q", "main")
        for i in range(main_ahead):
            commit(seed, f"m{i}", T0 + develop_days_old * DAY + i)
        helpers.sh(seed, "git", "remote", "add", "origin", origin)
        helpers.sh(seed, "git", "push", "-q", "origin", "main", "develop")
        return helpers.clone(origin, os.path.join(tmp, "work"))

    def config(self, work, text):
        helpers.write(os.path.join(work, "docs", "relay", "config.toml"), text)

    def test_stale_develop_with_main_default_starts_from_main_and_warns_nothing(self):
        work = self.make("main", develop_days_old=60, main_ahead=3)
        base, why, warning = basebranch.choose(work)
        self.assertEqual(base, "main")
        self.assertIn("origin/develop is stale", why)
        self.assertIsNone(warning)

    def test_develop_only_behind_main_starts_from_main_even_when_recent(self):
        work = self.make("main", develop_days_old=2, main_ahead=3, develop_ahead=0)
        self.assertEqual(basebranch.choose(work)[0], "main")

    def test_develop_with_own_work_but_stale_starts_from_main(self):
        work = self.make("main", develop_days_old=60, main_ahead=3, develop_ahead=2)
        self.assertEqual(basebranch.choose(work)[0], "main")

    def test_active_develop_with_main_default_keeps_develop(self):
        work = self.make("main", develop_days_old=2, main_ahead=1, develop_ahead=5)
        self.assertEqual(basebranch.choose(work)[0], "develop")

    def test_develop_default_starts_from_develop(self):
        work = self.make("develop", develop_days_old=60, main_ahead=3)
        base, _, warning = basebranch.choose(work)
        self.assertEqual(base, "develop")
        self.assertIsNone(warning)

    def test_config_wins_over_origin_head(self):
        work = self.make("develop", develop_days_old=0)
        self.config(work, '[relay]\nbase_branch = "main"\n')
        self.assertEqual(basebranch.choose(work)[:2], ("main", "[relay] base_branch"))

    def test_config_naming_a_stale_branch_warns(self):
        work = self.make("main", develop_days_old=60, main_ahead=3)
        self.config(work, '[relay]\nbase_branch = "develop"\n')
        base, _, warning = basebranch.choose(work)
        self.assertEqual(base, "develop")
        self.assertIn("60 days behind origin/main", warning)
        self.assertIn("lacks 3", warning)

    def test_explicit_base_wins_over_config(self):
        work = self.make("main", develop_days_old=0)
        self.config(work, '[relay]\nbase_branch = "main"\n')
        self.assertEqual(basebranch.choose(work, "develop")[:2], ("develop", "--base"))

    def test_bad_config_value_refuses(self):
        work = self.make("main", develop_days_old=0)
        for bad in ('base_branch = 3', 'base_branch = "a b"', 'base_branch = "x..y"', 'base_branch = ""'):
            self.config(work, f"[relay]\n{bad}\n")
            with self.assertRaises(RelayError):
                basebranch.choose(work)

    def test_missing_configured_branch_refuses(self):
        work = self.make("main", develop_days_old=0)
        self.config(work, '[relay]\nbase_branch = "trunk"\n')
        with self.assertRaisesRegex(RelayError, "no origin/trunk"):
            basebranch.choose(work)

    def test_no_develop_and_no_origin_head_refuses(self):
        work = self.make("main", develop_days_old=0)
        helpers.sh(work, "git", "remote", "set-head", "origin", "-d")
        helpers.sh(work, "git", "update-ref", "-d", "refs/remotes/origin/develop")
        with self.assertRaisesRegex(RelayError, "Pass --base"):
            basebranch.choose(work)

    def test_no_origin_head_falls_back_to_develop(self):
        work = self.make("main", develop_days_old=60, main_ahead=3)
        helpers.sh(work, "git", "remote", "set-head", "origin", "-d")
        self.assertEqual(basebranch.choose(work)[0], "develop")


if __name__ == "__main__":
    unittest.main()
