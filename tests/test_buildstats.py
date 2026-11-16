import os
import tempfile
import threading
import unittest
from unittest import mock

from relaylib import buildstats, gitops, sessions
from tests import helpers


class BuildStatsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        _, self.work = helpers.make_repo(temp.name)

    def test_counts_processes_by_kind(self):
        with buildstats.collect() as stats:
            gitops.git(self.work, "rev-parse", "HEAD")
            gitops.cat_files(self.work, ["HEAD:README.md"])
            sessions._ps("pid=", 5)
            result = stats.finish()
        self.assertEqual(result["processes"], {"git": 2, "gh": 0, "other": 1})
        self.assertIsInstance(result["cpu_seconds"], float)
        self.assertGreaterEqual(result["cpu_seconds"], 0)
        self.assertGreaterEqual(result["seconds"], 0)
        self.assertEqual(result["fetched"], [])
        self.assertEqual(result["cache"], {"hits": 0, "misses": 0})

    def test_gh_by_name_or_configured_path(self):
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": "/opt/fake/gh-wrapper"}), buildstats.collect() as stats:
            buildstats.count(["gh", "pr", "view"])
            buildstats.count(["/opt/fake/gh-wrapper", "pr", "view"])
            buildstats.count(["/usr/bin/git", "status"])
            buildstats.count(["ps", "-A"])
            result = stats.finish()
        self.assertEqual(result["processes"], {"git": 1, "gh": 2, "other": 1})

    def test_network_env_counts_its_config_read(self):
        with buildstats.collect() as stats:
            gitops.network_env(self.work)
            self.assertEqual(stats.finish()["processes"]["git"], 1)

    def test_nothing_counted_outside_a_collector(self):
        buildstats.count(["git", "status"])
        buildstats.note_fetch("x")
        buildstats.hit()
        buildstats.miss()
        with buildstats.collect() as stats:
            result = stats.finish()
        self.assertEqual(sum(result["processes"].values()), 0)

    def test_fetches_hits_and_threads(self):
        with buildstats.collect() as stats:
            buildstats.note_fetch("/r/a")
            buildstats.hit()
            buildstats.hit()
            buildstats.miss()
            import contextvars
            ctx = contextvars.copy_context()
            workers = [threading.Thread(target=ctx.copy().run, args=(buildstats.count, ["git", "x"]))
                       for _ in range(20)]
            for w in workers:
                w.start()
            for w in workers:
                w.join()
            result = stats.finish()
        self.assertEqual(result["fetched"], ["/r/a"])
        self.assertEqual(result["cache"], {"hits": 2, "misses": 1})
        self.assertEqual(result["processes"]["git"], 20)


if __name__ == "__main__":
    unittest.main()
