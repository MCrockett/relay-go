import json
import os
import unittest
from unittest import mock

from relaylib import hub, hubpage
from tests.test_hub import HubHome

UUID = "958dee3a-031e-4b49-be5f-09efa15292b1"


class RedactTest(unittest.TestCase):  # phone-artifact D5, R7, R8
    def test_the_spec_table(self):
        for text, want in (
                (f"claude --resume {UUID}", "claude --resume [id]"),
                ("[app/google-services.json](/Users/x/p/app/google-services.json)",
                 "[app/google-services.json](google-services.json)"),
                ("export GH_TOKEN=ghp_0123456789abcdefghijABCDEFGHIJ012345", "export GH_TOKEN=[hidden]"),
                ("Authorization: Bearer abc.def.ghi", "Authorization: Bearer [hidden]"),
                ("password: hunter22", "password: [hidden]"),
                ("mail me at someone@example.com", "mail me at [email]"),
                ("<script>alert(1)</script>", "<script>alert(1)</script>")):  # escaping is the renderer's job
            self.assertEqual(hubpage.redact(text), want, text)

    def test_known_ids_and_the_token(self):
        self.assertEqual(hubpage.redact("resume S-123 or S-1234 now", ids=["S-123"]), "resume [id] or [id]4 now")
        self.assertEqual(hubpage.redact("t=Zq9", ids=["Zq"]), "t=Zq9")             # too short to be an id
        self.assertEqual(hubpage.redact("open ?t=TOKENVALUE1 please", token="TOKENVALUE1"), "open ?t=[hidden] please")

    def test_secret_shapes(self):
        for secret in ("gho_" + "a1" * 12, "github_pat_" + "B2_" * 10, "sk-ant-api03-" + "x9" * 12,
                       "sk-" + "Q7" * 12, "xoxb-1234-5678-abcdefghij", "AKIAABCDEFGHIJKLMNOP", "ASIAABCDEFGHIJKLMNOP",
                       "AIza" + "c" * 35, "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl", "0123456789abcdef" * 2 + "01234567"):
            self.assertEqual(hubpage.redact(f"see {secret} here"), "see [hidden] here", secret)
        self.assertEqual(hubpage.redact("api_key=abc123 and auth: 'x y'"), "api_key=[hidden] and auth: [hidden]")
        self.assertEqual(hubpage.redact("built at 011f0bc"), "built at 011f0bc")    # a short SHA stays

    def test_paths(self):
        self.assertEqual(hubpage.redact("read ~/x/y.txt and /private/tmp/a/b."), "read y.txt and b.")
        self.assertEqual(hubpage.redact("see src/app.py and /api/snapshot"), "see src/app.py and /api/snapshot")
        self.assertEqual(hubpage.redact("in /Volumes/Disk 2/x"), "in Disk 2/x")      # a space ends the path

    def test_values_that_are_not_text(self):
        for value in (None, 5, 1.5, ["x"], {"a": 1}):
            self.assertEqual(hubpage.redact(value), "")

    def test_known_ids_come_from_the_registry_records_and_items(self):
        found = [{"kind": "session", "session_id": "SESSION-A"},
                 {"kind": "feature", "session": {"provider": "claude", "session_id": "SESSION-B"}},
                 {"kind": "feature", "session": None}]
        with mock.patch.object(hub, "registered", return_value={("claude", "HUB-1")}):
            ids = hubpage.known_ids(found, [{"session_id": "REC-1"}, {"session_id": None}, "junk"])
        self.assertEqual(set(ids), {"SESSION-A", "SESSION-B", "HUB-1", "REC-1"})


class TokenTest(HubHome):
    def test_dashboard_token(self):
        self.assertIsNone(hubpage.dashboard_token())
        os.makedirs(os.environ["RELAY_HOME"], exist_ok=True)
        with open(os.path.join(os.environ["RELAY_HOME"], "ui.json"), "w") as f:
            json.dump({"token": "abc", "pid": 1, "port": 9}, f)
        self.assertEqual(hubpage.dashboard_token(), "abc")
        with open(os.path.join(os.environ["RELAY_HOME"], "ui.json"), "w") as f:
            f.write("{")
        self.assertIsNone(hubpage.dashboard_token())


if __name__ == "__main__":
    unittest.main()
