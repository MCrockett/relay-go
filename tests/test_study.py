import json, os, shutil, tempfile, unittest
from unittest import mock
from relaylib import state, study
from tests import helpers


def review_file(meta, blocking=(), verdict="NO-GO"):
    block = "\n".join(["```text", f"verdict: {verdict}", "prior:", "blocking:"]
                      + [f"  - id: R1-{i} {b}" for i, b in enumerate(blocking, 1)] + ["notes:", "```"])
    return "---\n" + json.dumps(meta) + "\n---\n\nReview.\n\n" + block + "\n"


class StudyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.repo = helpers.make_repo(self.tmp)
        helpers.write(os.path.join(self.repo, "app.py"), "print(1)\n")
        helpers.sh(self.repo, "git", "add", "-A")
        helpers.sh(self.repo, "git", "commit", "-q", "-m", "code")
        self.head = helpers.sh(self.repo, "git", "rev-parse", "HEAD").strip()
        self.base = helpers.sh(self.repo, "git", "rev-parse", "HEAD~1").strip()
        st = state.new_state("demo", "work", {"provider": "claude", "session": "s"}, "feat/demo", small=True)
        st.update(pr=7)
        state.write_state(state.state_path(self.repo, "demo"), st)
        meta = {"reviewer": "codex", "model": "gpt-6-astra", "effort": "high", "round": 1, "head": self.head,
                "base_sha": self.base, "base_ref": "origin/develop", "verdict": "NO-GO", "duration_s": 120.5,
                "tokens": {"input": 1000, "cached": 800, "output": 50}}
        helpers.write(os.path.join(state.feature_dir(self.repo, "demo"), "reviews", "build-1.codex.md"),
                      review_file(meta, ["a.py:1 - first bug", "b.py:2 - second bug"]))

    def test_a_case_comes_from_a_past_review(self):
        case = study.case_from_review(self.repo, "demo", "build", 1)
        self.assertEqual((case["pr"], case["head"], case["base"], case["small"]), (7, self.head, self.base, True))
        self.assertEqual(case["expected_verdict"], "NO-GO")
        self.assertEqual([e["id"] for e in case["expected"]], ["R1-1", "R1-2"])
        self.assertIn("first bug", case["expected"][0]["summary"])

    def test_the_original_review_imports_as_a_result(self):
        r = study.result_from_review(self.repo, "demo", "build", 1, "demo-case")
        self.assertEqual((r["label"], r["verdict"], r["duration_s"]), ("codex:gpt-6-astra@high (original)", "NO-GO", 120.5))
        self.assertEqual(len(r["blocking"]), 2)
        self.assertEqual(r["usage"]["input"], 1000)

    def test_run_reviews_a_frozen_copy_and_cleans_up(self):
        queue = os.path.join(self.tmp, "q")
        os.makedirs(queue)
        helpers.write(os.path.join(queue, "000"), helpers.codex_output(helpers.verdict_block("GO")))
        fake = helpers.fake_bin(self.tmp, "fake", helpers.FAKE_REVIEWER)
        case = study.case_from_review(self.repo, "demo", "build", 1)
        case["id"] = "demo-case"
        out = os.path.join(self.tmp, "out")
        with mock.patch.dict(os.environ, {"RELAY_CODEX_BIN": fake, "FAKE_OUT": queue}):
            results = study.run([case], ["codex:gpt-6-astra@medium"], out, workers=1, timeout_s=60)
        self.assertEqual(results[0]["verdict"], "GO")
        self.assertTrue(os.path.exists(os.path.join(out, "results", "demo-case--codex_gpt-6-astra@medium.json")))
        self.assertNotIn(os.path.join(out, "wt-demo-case"), helpers.sh(self.repo, "git", "worktree", "list"))

    def test_the_report_scores_and_times_each_model(self):
        cases = [{"id": "c1", "expected_verdict": "NO-GO", "expected": [{"id": "H1"}, {"id": "H2"}]},
                 {"id": "c2", "expected_verdict": "GO", "expected": []}]
        def res(case, label, verdict, t):
            return {"case": case, "label": label, "verdict": verdict, "format_ok": True, "duration_s": t,
                    "usage": {"input": 1000, "cached": 500, "output": 10}, "blocking": []}
        results = [res("c1", "m1", "NO-GO", 10), res("c2", "m1", "GO", 5),
                   res("c1", "m2", "GO", 20), res("c2", "m2", "NO-GO", 7)]
        scores = {"c1": {"m1": {"found": ["H1", "H2"]}, "m2": {"found": []}}}
        md = study.report(cases, results, scores)
        self.assertIn("| m1 | 2/2 | 0 | 15s |", md)
        self.assertIn("| m2 | 0/2 | 2 | 27s |", md)
        self.assertIn("| m1 | 10 | 5 |", md)


    def test_a_later_round_is_replayed_with_its_round_and_earlier_findings(self):
        meta = {"reviewer": "codex", "model": "gpt-6-astra", "effort": "medium", "round": 2, "head": self.head,
                "base_sha": self.base, "verdict": "NO-GO", "duration_s": 60, "tokens": {}}
        helpers.write(os.path.join(state.feature_dir(self.repo, "demo"), "reviews", "build-2.codex.md"),
                      review_file(meta, ["a.py:1 - still broken"]).replace("id: R1-1", "id: R1-1"))
        case = study.case_from_review(self.repo, "demo", "build", 2)
        self.assertEqual((case["round"], case["prior_ids"]), (2, ["R1-1", "R1-2"]))
        case["id"] = "demo-r2"
        queue, log = os.path.join(self.tmp, "q2"), os.path.join(self.tmp, "log")
        os.makedirs(queue)
        helpers.write(os.path.join(queue, "000"), helpers.codex_output(
            helpers.verdict_block("NO-GO", ["id: R1-1 still"], [("R1-1", "partial"), ("R1-2", "resolved")])))
        fake = helpers.fake_bin(self.tmp, "fake", helpers.FAKE_REVIEWER)
        with mock.patch.dict(os.environ, {"RELAY_CODEX_BIN": fake, "FAKE_OUT": queue, "FAKE_LOG": log}):
            res = study.run([case], ["codex:gpt-6-astra@medium"], os.path.join(self.tmp, "out2"), 1, 60)[0]
        prompt = json.loads(open(log).read().splitlines()[-1])["argv"][-1]
        self.assertIn("This is review round 2", prompt)
        self.assertIn("R1-1, R1-2", prompt)
        self.assertEqual(res["verdict"], "NO-GO")

if __name__ == "__main__":
    unittest.main()
