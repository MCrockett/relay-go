import unittest
from unittest import mock

from relaylib import redact


class PublicLineTest(unittest.TestCase):
    def clean(self, text, user="mcrockett"):
        with mock.patch("getpass.getuser", return_value=user):
            return redact.public_line(text)

    def test_paths_keep_only_their_last_part(self):
        self.assertEqual(self.clean("spawn /Users/mcrockett/.local/bin/codex ENOENT"), "spawn codex ENOENT")
        self.assertEqual(self.clean("cannot open ~/StudioProjects/relay-go/x.md: denied"), "cannot open x.md: denied")
        self.assertEqual(self.clean("in /tmp/ and /"), "in tmp and /")
        self.assertEqual(self.clean("ratio 3/4 and and/or"), "ratio 3/4 and and/or")    # not paths

    def test_usernames_emails_urls_and_tokens(self):
        self.assertEqual(self.clean("not logged in as mcrockett (MCrockett@Example.com)"),
                         "not logged in as <user> (<email>)")
        self.assertEqual(self.clean("GET https://bob:pw@api.example.com/v1/x?key=abc failed"),
                         "GET https://api.example.com failed")
        self.assertEqual(self.clean("bad key sk-ant-api03-AbCdEf0123456789xyzXYZ"), "bad key <redacted>")
        self.assertEqual(self.clean("request req_011CXyz9abcdefghijklmnop failed"), "request <redacted> failed")
        self.assertEqual(self.clean("codex timed out twice"), "codex timed out twice")   # plain words stay

    def test_first_non_empty_line_and_limit(self):
        self.assertEqual(self.clean("\n  \nfirst\nsecond"), "first")
        self.assertEqual(len(self.clean("y " * 300)), 200)
        self.assertEqual(self.clean(""), "")
        self.assertEqual(self.clean(None), "")

    def test_short_usernames_are_left_alone(self):
        self.assertEqual(self.clean("go to it", user="it"), "go to it")


if __name__ == "__main__":
    unittest.main()
