import os, tempfile, unittest
from unittest import mock
from relaylib import ledger
from relaylib.errors import RelayError


class LedgerTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"RELAY_HOME": tempfile.mkdtemp()})
        p.start()
        self.addCleanup(p.stop)

    def test_append_read_summarize(self):
        ledger.append({"repo": "r", "role": "review", "provider": "codex", "model": "m",
                       "input": 100, "cached": 80, "output": 5, "duration_s": 2.0})
        ledger.append({"repo": "r", "role": "review", "provider": "codex", "model": "m",
                       "input": 50, "cached": 0, "output": 5, "duration_s": 1.0})
        rows = ledger.read(ledger.parse_since("7d"))
        self.assertEqual(len(rows), 2)
        g = ledger.summarize(rows)[("r", "review", "codex:m")]
        self.assertEqual((g["runs"], g["input"], g["cached"], g["output"]), (2, 150, 80, 10))

    def test_parse_since(self):
        self.assertEqual(ledger.parse_since("12h").total_seconds(), 43200)
        for bad in ("7w", "x", ""):
            with self.assertRaises(RelayError):
                ledger.parse_since(bad)

    def test_read_missing_is_empty(self):
        self.assertEqual(ledger.read(), [])


if __name__ == "__main__":
    unittest.main()
