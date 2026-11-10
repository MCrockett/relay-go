import unittest
from unittest import mock

from relaylib import deploycheck, reviewjobs
from tests import helpers
from tests.test_status import StatusFixture


class DeployCheckTest(StatusFixture):  # merge-and-tidy, deployment without interrupting sessions
    def check(self, ps=()):
        return deploycheck.check(self.projects, ps_lines=list(ps))

    def test_nothing_in_flight_is_safe(self):
        self.repo("alpha", {"one": {"stage": "spec", "status": "drafting"}})
        blocking, notes = self.check(["1 /usr/bin/python3 /opt/relay/bin/relay ui",
                                      "2 python3 /opt/relay/bin/relay hook claude"])
        self.assertEqual(blocking, [])
        self.assertEqual(notes, ["after the pull, restart the dashboard (pid 1): stop it, then run relay ui --background"])

    def test_a_review_in_flight_blocks(self):
        self.repo("alpha", {"one": {"stage": "spec", "status": "in-review"}})
        blocking, _ = self.check()
        self.assertEqual(blocking, ["alpha/one: a review is running; wait for its verdict"])

    def test_a_running_review_job_blocks(self):
        self.repo("alpha", {"one": {"stage": "build", "status": "ready-to-merge"}})
        with mock.patch.object(reviewjobs, "status", return_value={"state": "running"}):
            blocking, _ = self.check()
        self.assertIn("alpha/one: a dashboard or hub review is running; wait for it to end", blocking)

    def test_a_held_owner_action_lock_blocks(self):
        child = helpers.hold_action_lock()
        try:
            blocking, _ = self.check()
        finally:
            helpers.release(child)
        self.assertTrue(any("an owner action" in b for b in blocking))

    def test_a_running_relay_command_blocks(self):
        blocking, _ = self.check(["7 /usr/bin/python3.11 /opt/relay/bin/relay submit"])
        self.assertEqual(blocking, ["relay submit (pid 7): wait for it to finish"])

    def test_the_checker_itself_is_not_counted(self):
        import os
        blocking, _ = self.check([f"{os.getpid()} /usr/bin/python3.11 /opt/relay/bin/relay status"])
        self.assertEqual(blocking, [])

    def test_a_process_scan_that_fails_blocks(self):  # build review R1-3
        import subprocess
        failed = subprocess.CompletedProcess(["ps"], 1, "", "ps: no")
        with mock.patch("relaylib.deploycheck.subprocess.run", return_value=failed):
            self.assertIn("could not list running processes (ps exit 1)", deploycheck._processes()[0][0])
        with mock.patch("relaylib.deploycheck.subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 5)):
            self.assertIn("could not list running processes", deploycheck._processes()[0][0])

    def test_main_exit_codes(self):
        with mock.patch.object(deploycheck, "check", return_value=([], [])), mock.patch("builtins.print"), \
                mock.patch.object(deploycheck, "needs_command", return_value=[]):
            self.assertEqual(deploycheck.main(), 0)
        with mock.patch.object(deploycheck, "check", return_value=(["x"], [])), mock.patch("builtins.print"), \
                mock.patch.object(deploycheck, "needs_command", return_value=[]):
            self.assertEqual(deploycheck.main(), 1)

    def test_main_names_repos_that_need_a_test_command(self):
        with mock.patch.object(deploycheck, "check", return_value=([], [])), \
                mock.patch.object(deploycheck, "needs_command", return_value=["chain-rules", "bottomsup"]), \
                mock.patch("builtins.print") as out:
            self.assertEqual(deploycheck.main(), 0)
        out.assert_any_call('then: after you set ci = "release" globally, these repos need a test_command: '
                            "chain-rules, bottomsup")

    def projects_with_configs(self, configs):
        import os
        root = os.path.join(self.tmp, "needs")
        os.makedirs(root)
        for name, text in configs.items():
            sub = os.path.join(self.tmp, "src-" + name)
            os.makedirs(sub)
            _, work = helpers.make_repo(sub)
            if text is not None:
                helpers.write(os.path.join(work, "docs/relay/config.toml"), text)
                helpers.sh(work, "git", "add", "-A")
                helpers.sh(work, "git", "commit", "-q", "-m", "config")
                helpers.sh(work, "git", "push", "-q")
            os.rename(work, os.path.join(root, name))
        return root

    def test_needs_command_lists_repos_without_repo_config(self):  # R21
        root = self.projects_with_configs({"bare": None, "waived": "[build]\nrequire_ci = false\n",
                                           "has-command": '[build]\ntest_command = "true"\n'})
        self.assertEqual(deploycheck.needs_command(root), ["bare"])

    def test_needs_command_lists_local_or_release_without_a_command(self):  # N12
        root = self.projects_with_configs({"a-local": '[build]\nci = "local"\n',
                                           "b-release": '[build]\nci = "release"\n',
                                           "c-pr": '[build]\nci = "pr"\n',
                                           "d-local-ok": '[build]\nci = "local"\ntest_command = "make"\n',
                                           "e-other": "[review]\n"})
        self.assertEqual(deploycheck.needs_command(root), ["a-local", "b-release", "e-other"])


if __name__ == "__main__":
    unittest.main()
