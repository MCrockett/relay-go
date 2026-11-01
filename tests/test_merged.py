import json, os, shutil, tempfile, unittest
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

    def test_merged_without_a_time_is_asked_once(self):
        helpers.write(self.data, '{"state": "MERGED"}')
        self.assertIsNone(merged.merged_at(self.tmp, {"status": "ready-to-merge", "pr": 7}))
        os.remove(self.data)  # gh would now fail loudly; it is not asked
        with mock.patch.object(merged.gitops, "gh_json", side_effect=AssertionError("asked again")):
            self.assertIsNone(merged.merged_at(self.tmp, {"status": "ready-to-merge", "pr": 7}))


NOT_FOUND_GH = '''#!/usr/bin/env python3
import sys
sys.stderr.write("GraphQL: Could not resolve to a PullRequest with the number of 57. (repository.pullRequest)\\n")
sys.exit(1)
'''


def _resolve_in_child(args):
    repo, st, home = args
    os.environ["RELAY_HOME"] = home
    from relaylib import events, merged as m
    with events.collect() as batch:
        outcome = m.resolve(repo, st)[0]
        batch.flush()
    return outcome


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, "pr.json")
        self.home = os.path.join(self.tmp, "home")
        p = mock.patch.dict(os.environ, {"RELAY_HOME": self.home, "FAKE_GH_JSON": self.data,
                                         "RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh", helpers.FAKE_GH)})
        p.start()
        self.addCleanup(p.stop)
        self.repo = os.path.join(self.tmp, "upstream-studio")
        os.makedirs(self.repo)
        helpers.sh(self.repo, "git", "init", "-q")
        helpers.sh(self.repo, "git", "remote", "add", "origin", "https://github.com/MCrockett/upstream-studio.git")
        self.st = {"repo": "upstream-studio", "feature": "clip-studio", "status": "ready-to-merge", "pr": 57}

    def hub_events(self):
        path = os.path.join(self.home, "hub", "log.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(l) for l in f if '"event"' in l]

    def test_outcomes(self):
        for gh_state, outcome in (("OPEN", "open"), ("CLOSED", "closed"), ("MERGED", "merged")):
            helpers.write(self.data, json.dumps({"state": gh_state, "baseRefName": "develop"}))
            self.assertEqual(merged.resolve(self.repo, dict(self.st, pr=hash(gh_state) % 1000))[0], outcome)

    def test_network_error_is_unknown_and_not_cached(self):
        outcome, reason = merged.resolve(self.repo, self.st)  # no data file: gh fails
        self.assertEqual(outcome, "unknown")
        self.assertFalse(os.path.exists(os.path.join(self.home, "prgone.json")))

    def test_gone_with_copied_reason_cached_in_its_own_file(self):
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh404", NOT_FOUND_GH)}):
            outcome, reason = merged.resolve(self.repo, dict(self.st, repo="UpstreamPartners"))
        self.assertEqual(outcome, "gone")
        self.assertEqual(reason, "PR #57 is not in MCrockett/upstream-studio; this feature was copied from "
                                 "UpstreamPartners")
        self.assertEqual(merged._load(), {})  # older relay reads any merged.json value as merged
        self.assertEqual(merged.resolve(self.repo, self.st), ("gone", reason))  # cached; gh not asked
        self.assertFalse(merged.is_done(self.repo, self.st))

    def test_gone_without_copy(self):
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh404", NOT_FOUND_GH)}):
            self.assertEqual(merged.resolve(self.repo, self.st)[1], "PR #57 is not in MCrockett/upstream-studio")

    def test_closed_is_asked_again(self):
        helpers.write(self.data, '{"state": "CLOSED"}')
        self.assertEqual(merged.resolve(self.repo, self.st), ("closed", "PR #57 was closed without merging"))
        helpers.write(self.data, '{"state": "OPEN"}')
        self.assertEqual(merged.resolve(self.repo, self.st)[0], "open")

    def test_any_status_with_a_pr(self):
        helpers.write(self.data, '{"state": "MERGED", "baseRefName": "develop"}')
        self.assertTrue(merged.is_done(self.repo, dict(self.st, status="in-review")))

    def test_discovery_through_is_done_announces_once(self):
        helpers.write(self.data, '{"state": "MERGED", "baseRefName": "develop"}')
        self.assertTrue(merged.is_done(self.repo, self.st))
        self.assertTrue(merged.is_done(self.repo, self.st))
        (ev,) = self.hub_events()
        self.assertEqual((ev["event"], ev["slug"], ev["pr"]), ("merged", "clip-studio", 57))

    def test_release_label_from_the_same_answer(self):
        helpers.write(self.data, '{"state": "MERGED", "baseRefName": "main"}')
        merged.resolve(self.repo, self.st)
        self.assertEqual(self.hub_events()[0]["event"], "released")

    def test_already_cached_merge_announces_nothing(self):
        os.makedirs(self.home)
        helpers.write(os.path.join(self.home, "merged.json"),
                      json.dumps({"https://github.com/MCrockett/upstream-studio.git#57": True}))
        self.assertTrue(merged.is_done(self.repo, self.st))
        self.assertEqual(self.hub_events(), [])

    def test_two_processes_deliver_one_event(self):
        import multiprocessing
        helpers.write(self.data, '{"state": "MERGED", "baseRefName": "develop"}')
        with multiprocessing.get_context("spawn").Pool(4) as pool:
            got = pool.map(_resolve_in_child, [(self.repo, self.st, self.home)] * 4)
        self.assertEqual(got, ["merged"] * 4)
        self.assertEqual(len(self.hub_events()), 1)

    def test_mark_merged_claims_once_and_warns_when_unwritable(self):
        self.assertEqual(merged.mark_merged(self.repo, 57), (True, None))
        self.assertEqual(merged.mark_merged(self.repo, 57), (False, None))
        with mock.patch.object(merged, "_store", side_effect=OSError("read-only")):
            claimed, warning = merged.mark_merged(self.repo, 58)
        self.assertFalse(claimed)
        self.assertIn("PR #58 is merged, but relay could not record it locally: read-only", warning)


class GoneRecoveryTest(ResolveTest):  # merge-and-tidy F7: undoing a wrong gone
    def test_removing_the_prgone_entry_asks_github_again(self):
        with mock.patch.dict(os.environ, {"RELAY_GH_BIN": helpers.fake_bin(self.tmp, "gh404", NOT_FOUND_GH)}):
            self.assertEqual(merged.resolve(self.repo, self.st)[0], "gone")
        helpers.write(self.data, '{"state": "OPEN", "baseRefName": "develop"}')
        self.assertEqual(merged.resolve(self.repo, self.st)[0], "gone")     # cached: GitHub is not asked
        os.remove(os.path.join(self.home, "prgone.json"))                   # the README's recovery step
        self.assertEqual(merged.resolve(self.repo, self.st)[0], "open")


for _name in dir(ResolveTest):  # run only the recovery test here
    if _name.startswith("test_") and _name not in GoneRecoveryTest.__dict__:
        setattr(GoneRecoveryTest, _name, None)
