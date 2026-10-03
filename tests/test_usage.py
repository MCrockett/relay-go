import concurrent.futures
import datetime as dt
import json
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from relaylib import config, usage


def epoch(text):
    return dt.datetime.fromisoformat(text).timestamp()


class UsageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        patch = mock.patch.dict(os.environ, {"RELAY_HOME": self.home})
        patch.start()
        self.addCleanup(patch.stop)
        self.cfg = config.load()
        self.reset = epoch("2026-09-28T00:00:00-04:00")
        self.now = epoch("2026-09-23T18:00:00-04:00")

    def sample(self, used, at, **kwargs):
        return {"provider": "codex", "used_pct": used, "resets_at": self.reset,
                "sampled_at": epoch(at), **kwargs}

    def save(self, *samples):
        for sample in samples:
            usage.append(sample)

    def test_carry_forward_and_thresholds(self):
        self.save(self.sample(9.3, "2026-09-21T23:59:00-04:00"),
                  self.sample(20, "2026-09-22T18:00:00-04:00"))
        result = usage.budget("codex", self.cfg, epoch("2026-09-22T18:00:00-04:00"))
        self.assertAlmostEqual(result["allowance"], 20.7)
        self.assertAlmostEqual(result["meter"], 10.7 / 20.7 * 100)
        for current, expected in ((30, "ok"), (45, "amber"), (51.25, "red")):
            self.save(self.sample(current, "2026-09-23T18:00:00-04:00"))
            result = usage.budget("codex", self.cfg, self.now)
            self.assertEqual(result["allowance"], 25)
            self.assertEqual(result["state"], expected)

    def test_day_one_requires_a_baseline_and_expired_week_resets(self):
        self.save(self.sample(9.3, "2026-09-21T18:00:00-04:00"))
        result = usage.budget("codex", self.cfg, epoch("2026-09-21T18:00:00-04:00"))
        self.assertEqual(result["day_index"], 1)
        self.assertEqual(result["state"], "no-baseline")
        result = usage.budget("codex", self.cfg, self.reset + 60)
        self.assertEqual(result["used_pct"], 0)
        self.assertEqual(result["state"], "no-baseline")

    def test_stale_over_plan_and_week_boundary(self):
        self.save(self.sample(50, "2026-09-22T23:00:00-04:00"),
                  self.sample(55, "2026-09-23T18:00:00-04:00"))
        self.assertEqual(usage.budget("codex", self.cfg, self.now)["state"], "over")
        self.assertEqual(usage.budget("codex", self.cfg, self.now + 7 * 3600)["state"], "stale")
        self.assertEqual(usage.budget("claude", self.cfg, self.now)["state"], "no-baseline")

    def test_concurrent_pruning_preserves_completed_appends(self):
        old = self.sample(1, "2026-01-01T00:00:00Z")
        usage.append(old)
        def work(i):
            if i % 3 == 0:
                usage.prune(self.now)
            usage.append(self.sample(i, "2026-09-23T18:00:00-04:00"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(work, range(80)))
        usage.prune(self.now)
        self.assertEqual(sorted(s["used_pct"] for s in usage.read()), list(range(80)))

    def test_interrupted_line_does_not_lose_next_append(self):
        with open(os.path.join(self.home, "usage.jsonl"), "w") as f:
            f.write('{"provider":')
        usage.append(self.sample(10, "2026-09-23T18:00:00-04:00"))
        self.assertEqual(len(usage.read()), 1)

    def test_codex_dedupe_uses_source_time(self):
        with mock.patch("relaylib.availability.codex_weekly", return_value=(12, self.reset, self.now)):
            usage.record_codex_sample()
            usage.record_codex_sample()
        self.assertEqual(len(usage.read()), 1)
        self.assertEqual(usage.read()[0]["sampled_at"], self.now)
        with mock.patch("relaylib.availability.codex_weekly", return_value=(12, self.reset, self.now + 1)):
            usage.record_codex_sample()
        self.assertEqual(len(usage.read()), 2)

    def capture(self, payload):
        from relaylib import commands
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(payload)), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(commands.main(["usage-snapshot", "claude"]), 0)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def limits(self):
        return json.dumps({"rate_limits": {
            "seven_day": {"used_percentage": 42, "resets_at": self.reset},
            "five_hour": {"used_percentage": 12, "resets_at": self.now + 3600}}})

    def snapshot(self):
        with open(os.path.join(self.home, "claude-usage.json")) as f:
            return json.load(f)

    def test_capture_valid_and_invalid_inputs(self):
        self.capture(self.limits())
        self.assertEqual(self.snapshot()["seven_day"]["used_pct"], 42)
        self.assertEqual(usage.read()[0]["provider"], "claude")
        original = self.snapshot()
        for invalid in ('garbage', '{}', '[]', self.limits().replace('42', 'true'),
                        self.limits().replace('42', '"42"'), self.limits().replace('42', 'NaN'),
                        self.limits().replace(str(self.reset), 'null')):
            self.capture(invalid)
            self.assertEqual(self.snapshot(), original)
            self.assertEqual(len(usage.read()), 1)

    def test_capture_writes_are_independent(self):
        self.capture(self.limits())
        old = self.snapshot()
        with mock.patch("relaylib.usage.os.replace", side_effect=OSError("rename failed")):
            self.capture(self.limits())
        self.assertEqual(self.snapshot(), old)
        self.assertEqual(len(usage.read()), 2)
        self.assertFalse(any(p.startswith(".relay-") for p in os.listdir(self.home)))
        with mock.patch("relaylib.usage.append", side_effect=OSError("append failed")):
            self.capture(self.limits().replace('42', '43'))
        self.assertEqual(self.snapshot()["seven_day"]["used_pct"], 43)
        self.assertEqual(len(usage.read()), 2)

    def test_capture_unwritable_storage_is_quiet(self):
        with mock.patch("relaylib.usage.os.makedirs", side_effect=PermissionError("unwritable")):
            self.capture(self.limits())
        self.assertEqual(os.listdir(self.home), [])

    def test_capture_runtime_and_lock_contention(self):
        script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "relay")
        def run():
            started = time.monotonic()
            p = subprocess.run([sys.executable, script, "usage-snapshot", "claude"],
                               input=self.limits(), text=True, capture_output=True)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
            # R10's 150 ms target is measured on the owner's Mac, not a shared Linux CI runner.
            self.assertLess(time.monotonic() - started, .150 if sys.platform == "darwin" else 1.0)
        for _ in range(5):
            run()
        with usage.locked():
            for _ in range(5):
                run()
        self.assertEqual(len(usage.read()), 5)
        self.assertEqual(self.snapshot()["seven_day"]["used_pct"], 42)
