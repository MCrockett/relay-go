import os, tempfile, unittest
from relaylib import state
from relaylib.errors import RelayError

OWNER = {"provider": "claude", "session": "s1", "worktree": "/w", "since": "t"}


class StateTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def put(self, slug, **over):
        st = state.new_state(slug, "repo", OWNER, over.pop("branch", f"feat/{slug}"), small=over.pop("small", False))
        st.update(over)
        state.write_state(state.state_path(self.root, slug), st)
        return st

    def test_round_trip_and_body(self):
        self.put("alpha")
        path = state.state_path(self.root, "alpha")
        text = open(path).read()
        self.assertTrue(text.startswith("---\n{"))
        self.assertIn("Do not edit by hand", text)
        st = state.read_state(path)
        self.assertEqual((st["stage"], st["status"]), ("idea", "drafting"))

    def test_review_error_reason_lives_only_while_the_review_has_failed(self):
        self.put("alpha", status="review-error", review_error="boom")
        path = state.state_path(self.root, "alpha")
        self.assertEqual(state.read_state(path)["review_error"], "boom")
        st = state.read_state(path)
        st["status"] = "in-review"
        state.write_state(path, st)
        self.assertNotIn("review_error", state.read_state(path))

    def test_small_starts_at_build_and_skips(self):
        st = self.put("tiny", small=True)
        self.assertEqual(st["stage"], "build")
        self.assertEqual(st["skipped"], ["spec", "plan"])

    def test_bad_frontmatter(self):
        with self.assertRaises(RelayError):
            state.parse_state("no frontmatter", "x")
        with self.assertRaises(RelayError):
            state.parse_state("---\n{not json\n---\n", "x")

    def test_select_by_branch(self):
        self.put("alpha")
        self.put("beta")
        self.assertEqual(state.select_feature(self.root, "feat/beta"), "beta")

    def test_select_only_active(self):
        self.put("alpha", status="ready-to-merge")
        self.put("beta")
        merged = lambda st: st["status"] == "ready-to-merge"
        self.assertEqual(state.select_feature(self.root, "develop", is_done=merged), "beta")

    def test_unmerged_ready_to_merge_is_still_active(self):
        self.put("alpha", status="ready-to-merge")
        self.put("beta")
        with self.assertRaises(RelayError):
            state.select_feature(self.root, "develop")

    def test_select_ambiguous_fails_and_lists(self):
        self.put("alpha")
        self.put("beta")
        with self.assertRaisesRegex(RelayError, "alpha, beta"):
            state.select_feature(self.root, "develop")

    def test_select_explicit_unknown(self):
        with self.assertRaises(RelayError):
            state.select_feature(self.root, "develop", "nope")


    def test_existing_docs_spelling_is_used(self):
        os.makedirs(os.path.join(self.root, "Docs", "relay", "alpha"))
        st = state.new_state("alpha", "repo", OWNER, "feat/alpha")
        state.write_state(state.state_path(self.root, "alpha"), st)
        self.assertEqual(state.state_path(self.root, "alpha"),
                         os.path.join(self.root, "Docs", "relay", "alpha", "state.md"))
        self.assertEqual([slug for slug, _ in state.list_features(self.root)], ["alpha"])

if __name__ == "__main__":
    unittest.main()
