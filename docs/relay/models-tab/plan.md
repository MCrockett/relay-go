# models-tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show and edit the owner's reviewer tables from a Models tab in the dashboard, and let a review table be temporary (`--until`), reverting by itself with nothing running.

**Architecture:** A new module `relaylib/reviewtables.py` owns everything about review tables that is not TOML: the timed file `review-until.json`, end-time parsing, the change log `roles-log.jsonl`, the revision hash, and the two writes (`save`, `end`) that the CLI and the dashboard share. `config.py` gains the shared validator, a relay-home write lock, atomic writes, and applies unexpired timed tables at the end of `load`. The CLI, the snapshot and the server are thin callers; the page renders `snapshot.reviewers()` and posts to two new endpoints.

**Tech Stack:** Python 3.11 standard library (`tomllib`, `zoneinfo`, `fcntl`, `hashlib`), `unittest`, one inline HTML/JS page, Node only for the pure-function page tests (skipped when Node is missing).

**Spec:** `docs/relay/models-tab/spec.md` (GO in round 2, `reviews/spec-2.claude.md`). Executors read both.

## Global Constraints

- Python 3.11 standard library only. One module per responsibility in `relaylib/`.
- Plain English in all copy and docs; no em-dashes and no stock AI phrasing.
- Commits: `<type>: short summary` (feat, fix, refactor, test, docs, chore, style), ending with the session's attribution line.
- Tests: `python3.11 -m unittest discover -s tests -t . -v`, files `tests/test_<module>.py`. Never call real Claude or Codex.
- Every test that loads config runs with `RELAY_HOME` set to a temporary folder (R19).
- Files in relay home: `review-until.json` and `roles-log.jsonl`, mode 0600 (D2).
- Timed tables only for `review.claude`, `review.codex`, `review.owner` (D4). A timed table lasts at most 7 days (D12).
- Effort is empty or `[a-z]+`; provider is `claude` or `codex`; model not empty; no `provider:model` twice; at least one entry (D11).
- Lock wait: 1 second, then "busy, try again" (F4).
- Owner decisions confirmed on 2026-10-06: D5 (owner-only `relay roles set` and `relay roles end`) and D12 (7-day cap).

## Spec clarifications

These settle the round-2 review notes without editing the approved spec.

- **R19:** the test for R4 checks that a permanent set leaves a timed entry in place, not that it ends it.
- **D9 / R2a:** `relay roles end` and End now are logged too (`"table": "ended"`), with who.
- **R14:** the Until prefill is the full date and time with offset (`until_iso`, for example `2026-10-07T23:00-04:00`), never bare `HH:MM`, so saving an unchanged prefill keeps the same end.
- **R2a:** if the log append fails after a successful write, the write stands and the message ends with a warning. A missing log or a bad line shows no "last changed" for that author and is never fatal. Only the last 64 KiB of the log are read to find the latest change per author.
- **R10:** the request body of `POST /api/roles` is `{author, table, entries, until, seen}`; `entries` is a list of `provider:model[@effort]` strings.
- **R18:** the README mentions the D11 CLI changes: duplicates are refused, and an effort must be one lowercase word.
- **D11 scope:** the effort and duplicate rules apply to review tables. Writer roles and `reviewer.<provider>` keep today's checks.

## Review Focus

1. A timed table whose end passes while the dashboard is open: the panel shows the permanent table at the next render without a server round trip (Task 7 test pins `liveTimed`).
2. An owner who saves the Until prefill unchanged when the end is days away: the end time stays the same, not the next 23:00 (Task 8 checks the prefill uses `until_iso`; Task 2 test parses that ISO form back to the same epoch).
3. A config edited by hand between page load and save, or a CLI write during a dashboard save: 409 with fresh values, nothing written; the check and write share one lock (Task 6 test with a hand edit; Task 3 test that `save` with a stale `seen` writes nothing).
4. An existing owner config with an effort or duplicate that D11 now refuses: `relay roles` and reviews still load it; only the next write refuses (Task 1 test).
5. A `review-until.json` written by hand with one bad author: the other authors' timed tables still apply, and `relay roles` names the ignored one (Task 3 test).

## File Structure

- Create `relaylib/reviewtables.py`: timed file, `parse_until`, `when`, log, `revision`, `save`, `end`, `Stale`.
- Modify `relaylib/config.py`: `validate_reviewers`, `write_lock`, `atomic_write` (moved from usage), `_set_role` (unlocked, atomic), `set_role` (locked), `load(..., timed=True)`.
- Modify `relaylib/usage.py`: import `atomic_write` from config (no behavior change).
- Modify `relaylib/reviewjobs.py`: `option(spec, limits)` helper shared by `choices` and the snapshot.
- Modify `relaylib/commands.py`: `owner_or_relayed`, `cmd_roles` (set/end/--until/display), parser.
- Modify `relaylib/ui/snapshot.py`: `reviewers()`, added to `build()`.
- Modify `relaylib/ui/server.py`: `POST /api/roles`, `POST /api/roles/end`.
- Modify `relaylib/ui/page.html`: Models tab, Reviewers panel, edit dialog.
- Modify `README.md`.
- Modify `tests/__init__.py`, `tests/test_config.py`, `tests/test_commands.py`, `tests/test_ui_snapshot.py`, `tests/test_ui_server.py`; create `tests/test_reviewtables.py`.

## Coverage

| Requirement | Tasks |
|---|---|
| D2, D3, D10, R2, R3, R4, R5, F3, F6 | 3 |
| R1, D12 | 2, 4 |
| R2a, D9 | 3, 4, 5 |
| R6, R7, D5, F7 | 4 |
| R8, D7, D11, F4 | 1, 3 |
| R9, D6, R16 (data) | 5 |
| R10, R11, D8, F1, F2 | 6 |
| R12, R13, R16 (page) | 7 |
| R14, R15, R17, F8 | 8 |
| R18 | 9 |
| R19 | 1 to 8 (each task's tests), 1 (isolation) |
| F5 | no code: a review reads the table once in `review_current`; Task 3 adds a test that a loaded cfg is not changed by a later save |

---

### Task 1: Validation, lock and atomic writes in config

**Files:**
- Modify: `tests/__init__.py`
- Modify: `relaylib/config.py`
- Modify: `relaylib/usage.py:30-41` (move `atomic_write`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.validate_reviewers(entries: list[str]) -> list[str]` (stripped, empty items dropped, raises `RelayError`); `config.write_lock(timeout=None)` context manager (default `LOCK_TIMEOUT_S = 1.0`, raises `RelayError("busy, try again")`); `config.atomic_write(path, text, mode=0o600)`; `config._set_role(path, key, value)` (no lock); `config.set_role(path, key, value)` (locks, then `_set_role`).

- [ ] **Step 1: Isolate relay home for the whole suite.** In `tests/__init__.py` add:

```python
import os
import tempfile

# A live timed reviewer table on the owner's machine must never change test results (models-tab R19).
if not os.environ.get("RELAY_HOME", "").startswith(tempfile.gettempdir()):
    os.environ["RELAY_HOME"] = tempfile.mkdtemp(prefix="relay-home-")
```

In `tests/test_config.py` `setUp`, patch `RELAY_HOME` to `self.tmp` as well as `RELAY_CONFIG`.

- [ ] **Step 2: Write the failing tests** in `tests/test_config.py`:

```python
    def test_validate_reviewers_one_rule_set(self):
        self.assertEqual(config.validate_reviewers([" codex:gpt-6-astra@xhigh", "", "claude:claude-fable-5-1"]),
                         ["codex:gpt-6-astra@xhigh", "claude:claude-fable-5-1"])
        for bad, why in ((["gemini:pro"], "provider"), (["codex:"], "empty"), ([], "at least one"),
                         (["codex:a@High"], "lowercase"), (["codex:a@very high"], "lowercase"),
                         (["codex:a@low", "codex:a@high"], "twice")):
            with self.subTest(bad=bad), self.assertRaisesRegex(RelayError, why):
                config.validate_reviewers(bad)

    def test_set_role_refuses_duplicates_but_load_stays_lenient(self):
        with open(self.hub, "a") as f:
            f.write('\n[review.prefer]\nclaude = ["codex:a@High", "codex:a"]\n')
        self.assertEqual([p.model for p in config.review_preferences(config.load(), "claude")], ["a", "a"])
        with self.assertRaisesRegex(RelayError, "twice"):
            config.set_role(self.hub, "review.claude", "codex:a, codex:a@high")

    def test_set_role_is_atomic(self):
        before = open(self.hub).read()
        with mock.patch("os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            config.set_role(self.hub, "build", "claude:claude-sonnet-5")
        self.assertEqual(open(self.hub).read(), before)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith(".relay-")], [])

    def test_set_role_waits_for_the_lock_then_refuses(self):
        import fcntl
        with mock.patch.object(config, "LOCK_TIMEOUT_S", 0.05), \
                open(os.path.join(self.tmp, "roles.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(RelayError, "busy, try again"):
                config.set_role(self.hub, "build", "claude:claude-sonnet-5")
        self.assertNotIn("claude-sonnet-5", open(self.hub).read())
```

- [ ] **Step 3: Run them to see them fail.** `python3.11 -m unittest tests.test_config -v`. Expected: `AttributeError: module 'relaylib.config' has no attribute 'validate_reviewers'` and friends.

- [ ] **Step 4: Implement.** In `config.py` (imports: add `contextlib`, `fcntl`, `tempfile`, `time`):

```python
LOCK_TIMEOUT_S = 1.0
EFFORT = re.compile(r"[a-z]+")


def validate_reviewers(entries):
    """models-tab D11: the one rule set for every write of a review table. Reading stays lenient."""
    entries = [e.strip() for e in entries if isinstance(e, str) and e.strip()]
    if not entries:
        raise RelayError("give at least one reviewer, e.g. codex:gpt-6-astra@high")
    seen = set()
    for e in entries:
        spec = parse_model_spec(e)
        if spec.effort is not None and not EFFORT.fullmatch(spec.effort):
            raise RelayError(f"effort in {e!r} must be one lowercase word, like high")
        if (spec.provider, spec.model) in seen:
            raise RelayError(f"{spec.provider}:{spec.model} appears twice; list each reviewer once")
        seen.add((spec.provider, spec.model))
    return entries


def atomic_write(path, text, mode=0o600):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".relay-", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), mode)
            f.write(text)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextlib.contextmanager
def write_lock(timeout=None):
    """One lock in relay home for every reviewer-table write (models-tab D7). Not reentrant: take it once."""
    timeout = LOCK_TIMEOUT_S if timeout is None else timeout
    os.makedirs(relay_home(), exist_ok=True)
    with open(os.path.join(relay_home(), "roles.lock"), "a") as f:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RelayError("busy, try again")
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
```

Rename today's `set_role` body to `_set_role(path, key, value)`, with two changes: the `review.` branch becomes `entries = validate_reviewers(value.split(","))` (the existing author check stays first), and the final `open(path, "w")` write becomes `atomic_write(path, "\n".join(lines) + "\n", mode)` where `mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644` (the owner's file keeps its mode). Then:

```python
def set_role(path, key, value):
    """Rewrite one role line in config.toml, leaving every other line as written."""
    with write_lock():
        _set_role(path, key, value)
```

In `usage.py` delete `atomic_write` and import it: `from .config import atomic_write, relay_home` (`ui/server.py` keeps importing it from `usage`).

- [ ] **Step 5: Run the config and usage tests.** `python3.11 -m unittest tests.test_config tests.test_usage -v`. Expected: PASS.

- [ ] **Step 6: Commit.** `git add tests/__init__.py tests/test_config.py relaylib/config.py relaylib/usage.py && git commit -m "feat: one validator, a lock and atomic writes for reviewer tables"`

---

### Task 2: End-time parsing

**Files:**
- Create: `relaylib/reviewtables.py`
- Test: `tests/test_reviewtables.py`

**Interfaces:**
- Produces: `reviewtables.parse_until(text: str, tz_name: str, now: float | None = None) -> int` (epoch seconds, raises `RelayError`); `reviewtables.when(epoch, tz_name) -> str` (`"Tue 23:00"`); `reviewtables.iso(epoch, tz_name) -> str` (`"2026-10-07T23:00-04:00"`); constants `AUTHORS = ("claude", "codex", "owner")`, `MAX_S = 7 * 86400`.

- [ ] **Step 1: Write the failing tests:**

```python
import datetime as dt, os, tempfile, unittest
from unittest import mock
from zoneinfo import ZoneInfo
from relaylib import reviewtables
from relaylib.errors import RelayError

TZ = "America/Detroit"
at = lambda *a: dt.datetime(*a, tzinfo=ZoneInfo(TZ)).timestamp()
NOW = at(2026, 10, 6, 15, 0)


class ParseUntilTest(unittest.TestCase):
    def test_hh_mm_today_or_tomorrow(self):
        self.assertEqual(reviewtables.parse_until("23:00", TZ, NOW), at(2026, 10, 6, 23, 0))
        self.assertEqual(reviewtables.parse_until("9:30", TZ, NOW), at(2026, 10, 7, 9, 30))
        self.assertEqual(reviewtables.parse_until("15:00", TZ, NOW), at(2026, 10, 7, 15, 0))  # now is not ahead

    def test_iso_with_zone_round_trips_the_prefill(self):
        end = at(2026, 10, 9, 23, 0)
        self.assertEqual(reviewtables.parse_until(reviewtables.iso(end, TZ), TZ, NOW), end)
        self.assertEqual(reviewtables.parse_until("2026-10-07T03:00Z", TZ, NOW), at(2026, 10, 6, 23, 0))

    def test_refusals(self):
        for text, why in (("2026-10-06T14:00-04:00", "past"), ("2026-10-14T16:00-04:00", "7 days"),
                          ("2026-10-07T10:00", "time zone"), ("25:00", "time of day"), ("soon", "HH:MM"),
                          ("", "HH:MM")):
            with self.subTest(text=text), self.assertRaisesRegex(RelayError, why):
                reviewtables.parse_until(text, TZ, NOW)

    def test_daylight_saving(self):
        with self.assertRaisesRegex(RelayError, "daylight-saving"):           # 02:30 is skipped
            reviewtables.parse_until("02:30", TZ, at(2027, 3, 14, 1, 0))
        first = reviewtables.parse_until("01:30", TZ, at(2026, 11, 1, 0, 30))  # 01:30 occurs twice
        self.assertEqual(first, at(2026, 11, 1, 0, 30) + 3600)

    def test_uses_the_zone_given(self):
        self.assertEqual(reviewtables.parse_until("23:00", "UTC", NOW),
                         dt.datetime(2026, 10, 6, 23, 0, tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(reviewtables.when(at(2026, 10, 6, 23, 0), TZ), "Tue 23:00")
```

- [ ] **Step 2: Run them to see them fail.** `python3.11 -m unittest tests.test_reviewtables -v`. Expected: `ModuleNotFoundError: No module named 'relaylib.reviewtables'`.

- [ ] **Step 3: Implement** the start of `relaylib/reviewtables.py`:

```python
"""Review tables beyond config.toml: temporary tables in ~/.relay/review-until.json, their end times, the change
log in ~/.relay/roles-log.jsonl, and the writes the CLI and the dashboard share (models-tab)."""
import datetime as dt
import re
import time
from zoneinfo import ZoneInfo

from . import config
from .errors import RelayError

AUTHORS = config.PROVIDERS + ("owner",)
MAX_S = 7 * 86400


def when(epoch, tz_name):
    return dt.datetime.fromtimestamp(epoch, ZoneInfo(tz_name)).strftime("%a %H:%M")


def iso(epoch, tz_name):
    return dt.datetime.fromtimestamp(epoch, ZoneInfo(tz_name)).isoformat(timespec="minutes")


def parse_until(text, tz_name, now=None):
    """End time as epoch seconds: HH:MM (next occurrence in tz_name) or ISO 8601 with a zone (R1, D12)."""
    now = time.time() if now is None else now
    zone, text = ZoneInfo(tz_name), (text or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if match:
        hour, minute = int(match[1]), int(match[2])
        if hour > 23 or minute > 59:
            raise RelayError(f"{text} is not a time of day")
        today = dt.datetime.fromtimestamp(now, zone).date()
        for day in (today, today + dt.timedelta(days=1)):
            wall = dt.datetime.combine(day, dt.time(hour, minute))
            local = wall.replace(tzinfo=zone, fold=0)  # fold=0: a time that occurs twice means the first
            if local.timestamp() > now:
                break
        if local.astimezone(dt.timezone.utc).astimezone(zone).replace(tzinfo=None) != wall:
            raise RelayError(f"{text} does not occur on {day} in {tz_name}: a daylight-saving change skips it")
        end = local.timestamp()
    else:
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            raise RelayError(f"until must be HH:MM or an ISO date-time with a time zone, not {text!r}")
        if parsed.tzinfo is None:
            raise RelayError(f"{text} needs a time zone, e.g. 2026-10-07T23:00-04:00")
        end = parsed.timestamp()
    if end <= now:
        raise RelayError(f"until {text} is in the past")
    if end - now > MAX_S:
        raise RelayError(f"until {text} is more than 7 days away; a temporary table lasts at most 7 days")
    return int(end)
```

- [ ] **Step 4: Run them to see them pass.** `python3.11 -m unittest tests.test_reviewtables -v`. Expected: PASS.

- [ ] **Step 5: Commit.** `git add relaylib/reviewtables.py tests/test_reviewtables.py && git commit -m "feat: parse the end time of a temporary reviewer table"`

---

### Task 3: Timed file, log, revision, save and end; load applies timed tables

**Files:**
- Modify: `relaylib/reviewtables.py`
- Modify: `relaylib/config.py` (`load`)
- Test: `tests/test_reviewtables.py`

**Interfaces:**
- Consumes: Task 1 `validate_reviewers`, `write_lock`, `atomic_write`, `_set_role`; Task 2 `parse_until`, `when`.
- Produces: `class Stale(RelayError)`; `path() -> str`; `read(now=None) -> (dict[author, {"entries", "until", "set_at", "by"}], list[str])` (active tables, problems; never raises); `latest() -> dict[author, row]`; `revision() -> str`; `save(author, table, entries, until, by, seen=None, now=None) -> str`; `end(author_or_all, by, seen=None, now=None) -> str`; `config.load(repo_root=None, timed=True)`.

- [ ] **Step 1: Write the failing tests** (add to `tests/test_reviewtables.py`):

```python
class TablesTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.cfg = os.path.join(self.home, "config.toml")
        with open(self.cfg, "w") as f:
            f.write('[review.prefer]\nclaude = ["codex:gpt-6-astra"]\ncodex = ["claude:claude-sonnet-5"]\n'
                    'owner = ["codex:gpt-6-astra"]\n')
        p = mock.patch.dict(os.environ, {"RELAY_HOME": self.home, "RELAY_CONFIG": self.cfg})
        p.start()
        self.addCleanup(p.stop)
        self.clock = mock.patch("time.time", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def prefs(self, author="claude", repo=None):
        from relaylib import config
        return [f"{p.provider}:{p.model}" for p in config.review_preferences(config.load(repo), author)]

    def test_timed_table_wins_then_expires_with_nothing_running(self):
        msg = reviewtables.save("claude", "timed", ["claude:claude-fable-5-1", "codex:gpt-6-astra"], "23:00", "owner")
        self.assertIn("until Tue 23:00", msg)
        self.assertEqual(self.prefs(), ["claude:claude-fable-5-1", "codex:gpt-6-astra"])
        self.assertEqual(oct(os.stat(reviewtables.path()).st_mode & 0o777), "0o600")
        self.clock.stop()
        with mock.patch("time.time", return_value=at(2026, 10, 6, 23, 0)):
            self.assertEqual(self.prefs(), ["codex:gpt-6-astra"])            # F6: expired at the boundary
        self.clock.start()

    def test_timed_wins_over_a_repo_overlay(self):
        repo = os.path.join(self.home, "repo")
        os.makedirs(os.path.join(repo, "docs", "relay"))
        with open(os.path.join(repo, "docs", "relay", "config.toml"), "w") as f:
            f.write('[review.prefer]\nclaude = ["claude:claude-sonnet-5"]\n')
        self.assertEqual(self.prefs(repo=repo), ["claude:claude-sonnet-5"])
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        self.assertEqual(self.prefs(repo=repo), ["claude:claude-fable-5-1"])

    def test_permanent_set_leaves_a_timed_table_in_place(self):          # R4, D10
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        msg = reviewtables.save("claude", "permanent", ["claude:claude-sonnet-5"], None, "owner")
        self.assertIn("temporary table still applies until Tue 23:00", msg)
        self.assertEqual(self.prefs(), ["claude:claude-fable-5-1"])
        from relaylib import config
        self.assertEqual(config.load(timed=False)["review"]["prefer"]["claude"], ["claude:claude-sonnet-5"])

    def test_end_one_all_and_nothing(self):                               # R5
        self.assertEqual(reviewtables.end("codex", "owner"), "no temporary table for codex")
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        reviewtables.save("owner", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        self.assertIn("claude", reviewtables.end("claude", "owner"))
        self.assertEqual(self.prefs(), ["codex:gpt-6-astra"])
        reviewtables.end("all", "owner")
        self.assertEqual(reviewtables.read()[0], {})

    def test_every_change_is_logged_with_who(self):                     # R2a, D9
        reviewtables.save("claude", "permanent", ["claude:claude-sonnet-5"], None, "owner")
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "claude session s1")
        reviewtables.end("claude", "dashboard")
        latest = reviewtables.latest()["claude"]
        self.assertEqual((latest["table"], latest["by"]), ("ended", "dashboard"))
        with open(os.path.join(self.home, "roles-log.jsonl"), "a") as f:
            f.write("{not json\n")
        self.assertEqual(reviewtables.latest()["claude"]["by"], "dashboard")  # a bad line is skipped

    def test_log_failure_keeps_the_write_with_a_warning(self):
        with mock.patch.object(reviewtables, "_append_log", side_effect=OSError("read-only")):
            msg = reviewtables.save("claude", "permanent", ["claude:claude-sonnet-5"], None, "owner")
        self.assertIn("warning", msg)
        self.assertEqual(self.prefs(), ["claude:claude-sonnet-5"])

    def test_malformed_file_and_bad_author_are_ignored_and_reported(self):   # R3, F3
        with open(reviewtables.path(), "w") as f:
            f.write("{oops")
        self.assertEqual(self.prefs(), ["codex:gpt-6-astra"])
        self.assertIn("ignored", reviewtables.read()[1][0])
        good = {"entries": ["claude:claude-fable-5-1"], "until": NOW + 3600, "set_at": NOW, "by": "owner"}
        with open(reviewtables.path(), "w") as f:
            json.dump({"claude": {**good, "entries": ["gemini:pro"]}, "owner": good}, f)
        tables, problems = reviewtables.read()
        self.assertEqual(list(tables), ["owner"])
        self.assertIn("claude", problems[0])
        self.assertEqual(self.prefs(), ["codex:gpt-6-astra"])
        reviewtables.save("codex", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")  # next write replaces it
        self.assertEqual(sorted(json.load(open(reviewtables.path()))), ["codex", "owner"])

    def test_stale_seen_writes_nothing(self):                            # D8, F1
        seen = reviewtables.revision()
        with open(self.cfg, "a") as f:
            f.write("# edited by hand\n")
        with self.assertRaises(reviewtables.Stale):
            reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "dashboard", seen)
        self.assertFalse(os.path.exists(reviewtables.path()))
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "dashboard",
                          reviewtables.revision())

    def test_revision_with_missing_files(self):
        os.unlink(self.cfg)
        self.assertEqual(reviewtables.revision(), reviewtables.revision())   # missing counts as empty

    def test_table_and_until_must_agree(self):                           # R11
        with self.assertRaisesRegex(RelayError, "needs an end time"):
            reviewtables.save("claude", "timed", ["codex:a"], None, "owner")
        with self.assertRaisesRegex(RelayError, "no end time"):
            reviewtables.save("claude", "permanent", ["codex:a"], "23:00", "owner")
        with self.assertRaisesRegex(RelayError, "author"):
            reviewtables.save("nobody", "permanent", ["codex:a"], None, "owner")

    def test_a_loaded_config_is_not_changed_by_a_later_save(self):       # F5
        from relaylib import config
        cfg = config.load()
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        self.assertEqual(cfg["review"]["prefer"]["claude"], ["codex:gpt-6-astra"])
```

(Add `import json` to the test file's imports.)

- [ ] **Step 2: Run them to see them fail.** `python3.11 -m unittest tests.test_reviewtables -v`. Expected: `AttributeError: module 'relaylib.reviewtables' has no attribute 'save'`.

- [ ] **Step 3: Implement** in `relaylib/reviewtables.py` (add imports `hashlib`, `json`, `os`):

```python
FILE, LOG = "review-until.json", "roles-log.jsonl"
LOG_TAIL = 64 * 1024


class Stale(RelayError):
    """The owner config or the timed file changed since the caller looked (D8)."""


def path():
    return os.path.join(config.relay_home(), FILE)


def read(now=None):
    """(active timed tables by author, problems). Never raises: a bad file or entry is reported and ignored (R3)."""
    now = time.time() if now is None else now
    try:
        with open(path()) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        return {}, []
    except (OSError, ValueError) as e:
        return {}, [f"{FILE} ignored: {e}"]
    tables, problems = {}, []
    for author, table in data.items():
        try:
            if author not in AUTHORS:
                raise ValueError("unknown author")
            until, entries = table["until"], table["entries"]
            if isinstance(until, bool) or not isinstance(until, (int, float)):
                raise ValueError("bad end time")
            if not isinstance(entries, list) or not entries:
                raise ValueError("no reviewers")
            for entry in entries:
                config.parse_model_spec(entry)
        except (ValueError, KeyError, TypeError, AttributeError, RelayError) as e:
            problems.append(f"{FILE}: the temporary table for {author} was ignored ({e})")
            continue
        if until > now:
            tables[author] = table
    return tables, problems


def _bytes(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


def revision():
    """What the owner saw: the owner config and the timed file; a missing file counts as empty (R9)."""
    digest = hashlib.sha256(_bytes(config.config_path()))
    digest.update(b"\0" + _bytes(path()))
    return digest.hexdigest()[:16]


def _append_log(rows):
    fd = os.open(os.path.join(config.relay_home(), LOG), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _log(rows):
    """Append after the write succeeded; a failure leaves the write standing with a warning (R2a)."""
    try:
        _append_log(rows)
        return ""
    except OSError as e:
        return f"\nrelay: warning: the change was saved but not logged ({e})"


def latest():
    """The newest log row per author, from the tail of the log. Missing or bad lines are skipped."""
    try:
        with open(os.path.join(config.relay_home(), LOG), "rb") as f:
            f.seek(max(0, os.path.getsize(f.name) - LOG_TAIL))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        try:
            row = json.loads(line)
            if row["author"] in AUTHORS and isinstance(row["at"], (int, float)):
                out[row["author"]] = row
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _check(seen):
    if seen is not None and seen != revision():
        raise Stale("changed since you looked; review the fresh table before saving again")


def _write_timed(tables):
    config.atomic_write(path(), json.dumps(tables, indent=2, sort_keys=True) + "\n", 0o600)


def _permanent_text(author):
    prefs = config.review_preferences(config.load(timed=False), author)
    return " -> ".join(f"{p.provider}:{p.model}" + (f"@{p.effort}" if p.effort else "") for p in prefs)


def save(author, table, entries, until, by, seen=None, now=None):
    """Write a permanent (config.toml) or timed (review-until.json) review table. CLI and dashboard (R1, R4, R10)."""
    if author not in AUTHORS:
        raise RelayError(f"review.{author}: the author must be claude, codex or owner")
    if table not in ("permanent", "timed"):
        raise RelayError("table must be permanent or timed")
    if table == "timed" and not until:
        raise RelayError("a temporary table needs an end time (until)")
    if table == "permanent" and until:
        raise RelayError("a permanent table has no end time; use a temporary table for that")
    if not isinstance(entries, list):
        raise RelayError("entries must be a list of provider:model[@effort]")
    entries = config.validate_reviewers(entries)
    now = time.time() if now is None else now
    with config.write_lock():
        _check(seen)
        tz = config.load(timed=False)["limits"]["timezone"]
        if table == "timed":
            end = parse_until(until, tz, now)
            tables = read(now)[0]
            tables[author] = {"entries": entries, "until": end, "set_at": int(now), "by": by}
            _write_timed(tables)
            message = (f"review.{author} = {', '.join(entries)} until {when(end, tz)} (temporary; "
                       f"then {_permanent_text(author)})")
        else:
            end = None
            config._set_role(config.config_path(), f"review.{author}", ", ".join(entries))
            message = f"review.{author} = {', '.join(entries)} in {config.config_path()} (applies to every repo)"
            active = read(now)[0].get(author)
            if active:
                message += f"; the temporary table still applies until {when(active['until'], tz)}"
        return message + _log([{"at": int(now), "author": author, "table": table, "entries": entries,
                                "until": end, "by": by}])


def end(author, by, seen=None, now=None):
    """End timed tables now: one author or all (R5, R11). Nothing to end is not an error."""
    if author != "all" and author not in AUTHORS:
        raise RelayError(f"review.{author}: the author must be claude, codex, owner or all")
    now = time.time() if now is None else now
    with config.write_lock():
        _check(seen)
        tables = read(now)[0]
        ended = [a for a in (AUTHORS if author == "all" else (author,)) if a in tables]
        if not ended:
            return "no temporary tables" if author == "all" else f"no temporary table for {author}"
        for a in ended:
            del tables[a]
        _write_timed(tables)
        message = "; ".join(f"review.{a} is back to {_permanent_text(a)}" for a in ended)
        return message + _log([{"at": int(now), "author": a, "table": "ended", "entries": None, "until": None,
                                "by": by} for a in ended])
```

In `config.load`, add the `timed=True` parameter and, after the paths loop:

```python
    if timed:  # a temporary table wins over the owner's and the repo's tables until it ends (models-tab D3)
        from . import reviewtables  # reviewtables imports config at module level
        for author, table in reviewtables.read()[0].items():
            cfg.setdefault("review", {}).setdefault("prefer", {})[author] = list(table["entries"])
    return cfg
```

- [ ] **Step 4: Run the new and existing tests.** `python3.11 -m unittest tests.test_reviewtables tests.test_config -v`. Expected: PASS.

- [ ] **Step 5: Commit.** `git add relaylib/reviewtables.py relaylib/config.py tests/test_reviewtables.py && git commit -m "feat: temporary reviewer tables that end by themselves, logged and revisioned"`

---

### Task 4: CLI: `relay roles set --until`, `relay roles end`, owner-only, display

**Files:**
- Modify: `relaylib/commands.py` (`cmd_roles` ~724, parser ~858, new `owner_or_relayed` next to `check_same_provider`)
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: Task 3 `reviewtables.save`, `end`, `read`, `latest`, `when`; `config.load(timed=False)`.
- Produces: `commands.owner_or_relayed(args, what) -> str | None` (None for the owner; `"<provider> session <id>"` for a relayed agent).

- [ ] **Step 1: Write the failing tests** in `CommandsTest` (its environment is a Claude session with `RELAY_HOME` in the temp folder):

```python
    def owner_env(self):
        return mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                            if k not in ("CLAUDECODE", "RELAY_PROVIDER", "RELAY_SESSION")},
                               clear=True)

    def test_roles_set_and_end_are_owner_only(self):                     # R7, D5, F7
        for argv in (("roles", "set", "review.claude", "codex:gpt-6-astra"),
                     ("roles", "set", "build", "claude:claude-sonnet-5"), ("roles", "end", "all")):
            self.assertEqual(self.relay(*argv), 1)
            self.assertIn("owner-only", self.last_err)
        self.assertNotIn("review.prefer", open(self.cfg).read())
        self.assertEqual(self.relay("roles"), 0, self.last_err)               # showing stays open
        self.assertEqual(self.relay("roles", "set", "review.claude", "codex:gpt-6-astra", "--relayed"), 0,
                         self.last_err)
        self.assertEqual(self.relay("roles"), 0, self.last_err)
        self.assertRegex(self.last_out, r"last changed \w{3} \d\d:\d\d by claude session s1")
        with self.owner_env():
            self.assertEqual(self.relay("roles", "set", "review.codex", "claude:claude-sonnet-5"), 0, self.last_err)
            self.assertEqual(self.relay("roles", "set", "review.codex", "claude:a", "--relayed"), 1)

    def test_roles_until_shows_temporary_then_permanent_and_ends(self):  # R1, R5, R6
        with self.owner_env():
            self.assertEqual(self.relay("roles", "set", "review.claude", "codex:gpt-6-astra"), 0, self.last_err)
            self.assertEqual(self.relay("roles", "set", "review.claude", "claude:claude-fable-5-1",
                                        "--until", "23:59"), 0, self.last_err)
            self.assertEqual(self.relay("roles"), 0, self.last_err)
            self.assertRegex(self.last_out, r"review\.claude\s+claude:claude-fable-5-1\s+temporary until \w{3} 23:59")
            self.assertRegex(self.last_out, r"then codex:gpt-6-astra")
            self.assertEqual(self.relay("roles", "set", "build", "claude:x", "--until", "23:59"), 1)
            self.assertIn("only review tables", self.last_err)
            self.assertEqual(self.relay("roles", "end", "review.claude"), 0, self.last_err)
            self.assertEqual(self.relay("roles", "end", "review.claude"), 0, self.last_err)
            self.assertIn("no temporary table for claude", self.last_out)

    def test_roles_reports_an_ignored_timed_file(self):                  # F3
        os.makedirs(os.environ["RELAY_HOME"], exist_ok=True)
        helpers.write(os.path.join(os.environ["RELAY_HOME"], "review-until.json"), "{oops")
        self.assertEqual(self.relay("roles"), 0, self.last_err)
        self.assertIn("review-until.json ignored", self.last_out)
```

- [ ] **Step 2: Run them to see them fail.** `python3.11 -m unittest tests.test_commands -k roles -v`. Expected: FAIL (`set` succeeds without `--relayed`; `end` is not a valid choice).

- [ ] **Step 3: Implement.** Add `reviewtables` to the `from . import ...` line. Add:

```python
def owner_or_relayed(args, what):
    """None for the owner in their own terminal; '<provider> session <id>' for an agent passing on the owner's
    words with --relayed. Anything else is refused."""
    env = dict(os.environ)
    if not args.relayed:
        identity.require_owner_terminal(env, what)
        return None
    if not identity.in_agent_session(env):
        raise RelayError("--relayed is for an agent passing on the owner's decision; in your own terminal, "
                         "run it without --relayed")
    me = identity.detect(env, args.by)
    if me.provider not in config.PROVIDERS:
        raise RelayError("--relayed records which agent passed the decision on: --by must be claude or codex")
    return f"{me.provider} session {me.session}"


def _review_author(key):
    author = key.split(".", 1)[1] if key.startswith("review.") else ""
    if author not in reviewtables.AUTHORS:
        raise RelayError(f"{key}: expected review.claude, review.codex or review.owner")
    return author
```

Replace the `set` branch of `cmd_roles` with:

```python
    if args.action in ("set", "end"):
        by = owner_or_relayed(args, f"relay roles {args.action}") or "owner"
        if args.action == "end":
            if not args.key:
                raise RelayError("usage: relay roles end review.<author> | all")
            print("relay: " + reviewtables.end("all" if args.key == "all" else _review_author(args.key), by))
            return 0
        if not (args.key and args.value):
            raise RelayError("usage: relay roles set <role> <provider:model[@effort]> [--until HH:MM]")
        if args.key.startswith("review."):
            print("relay: " + reviewtables.save(_review_author(args.key), "timed" if args.until else "permanent",
                                                args.value.split(","), args.until, by))
            return 0
        if args.until:
            raise RelayError("only review tables can be temporary: review.claude, review.codex, review.owner")
        config.set_role(config.config_path(), args.key, args.value)
        print(f"relay: {args.key} = {args.value} in {config.config_path()} (applies to every repo)")
        return 0
```

Replace the reviewer loop of the display with:

```python
    base, (timed, problems), changes = config.load(timed=False), reviewtables.read(), reviewtables.latest()
    tz = cfg["limits"]["timezone"]
    chain = lambda prefs: " -> ".join(reviewjobs.spec_id(p) for p in prefs)
    for author in reviewtables.AUTHORS:
        line = f"  review.{author:6} " + chain(config.review_preferences(cfg, author))
        if author in timed:
            line += f"   temporary until {reviewtables.when(timed[author]['until'], tz)}"
            line += f"\n  {'':13} then {chain(config.review_preferences(base, author))}"
        if author in changes:
            line += f"\n  {'':13} last changed {reviewtables.when(changes[author]['at'], tz)} by {changes[author]['by']}"
        print(line)
    for problem in problems:
        print(f"  note: {problem}")
```

Parser: `ro.add_argument("action", nargs="?", choices=["set", "end"])`, plus `--until` ("review tables only: HH:MM or an ISO date-time with a zone; at most 7 days"), `--relayed` and `--by` with the same help text as `relay rule`. Update the subcommand help to `"show roles, or roles set <role> <provider:model[@effort]> [--until HH:MM], or roles end review.<author>|all (owner only)"`.

- [ ] **Step 4: Run the commands tests.** `python3.11 -m unittest tests.test_commands -v`. Expected: PASS, including `test_roles_shows_the_table_and_who_is_out`.

- [ ] **Step 5: Commit.** `git add relaylib/commands.py tests/test_commands.py && git commit -m "feat: relay roles set --until, relay roles end, owner-only table changes"`

---

### Task 5: Snapshot `reviewers` object

**Files:**
- Modify: `relaylib/reviewjobs.py:26-39` (`option` helper)
- Modify: `relaylib/ui/snapshot.py` (`reviewers`, `build`)
- Test: `tests/test_ui_snapshot.py`

**Interfaces:**
- Consumes: Task 3 `reviewtables.read`, `latest`, `revision`, `when`, `iso`.
- Produces: `reviewjobs.option(spec, limits) -> {"id", "provider", "model", "effort", "available", "reason"}`; `snapshot.reviewers(now=None) -> dict` with keys `authors` (per author: `entries`, `permanent`, `until`, `until_label`, `until_iso`, `changed` = `{"label", "by"}` or None), `writers` (role -> spec string), `effort` (`effort`, `final_effort`, `release_effort`), `known` (list of `{id, provider, available, reason}` with `id` = `provider:model`), `overriding` (list of `{repo, path, authors}`), `problems` (list of str), `timezone`, `revision`. `build()` adds `"reviewers"`: that dict, or `{"error": str}` when building it fails.

- [ ] **Step 1: Write the failing test** in `tests/test_ui_snapshot.py` (new class; patch `RELAY_HOME`, `RELAY_CONFIG`, `RELAY_ROOT` to temp paths in `setUp`; write the owner config with `[review.prefer] claude = ["codex:gpt-6-astra@high"]`; create `root/proj/.git` and `root/proj/docs/relay/config.toml` with `[review.prefer] codex = ["claude:x"]`; append one ledger row `{"provider": "claude", "model": "claude-haiku-4-5"}` through `ledger`'s own writer or by writing the ledger file):

```python
    def test_reviewers_object(self):
        reviewtables.save("claude", "timed", ["claude:claude-fable-5-1"], "23:00", "owner")
        r = snapshot.reviewers()
        claude = r["authors"]["claude"]
        self.assertEqual([e["id"] for e in claude["entries"]], ["claude:claude-fable-5-1"])
        self.assertEqual([e["id"] for e in claude["permanent"]], ["codex:gpt-6-astra@high"])
        self.assertTrue(claude["until"] and claude["until_label"] and claude["until_iso"].endswith(("-04:00", "-05:00")))
        self.assertEqual(claude["changed"]["by"], "owner")
        self.assertIsNone(r["authors"]["codex"]["until"])
        self.assertIn("claude:claude-haiku-4-5", [k["id"] for k in r["known"]])     # from the ledger
        self.assertIn("codex:gpt-6-astra", [k["id"] for k in r["known"]])           # from the config, no effort
        self.assertEqual([(o["repo"], o["authors"]) for o in r["overriding"]], [("proj", ["codex"])])
        self.assertEqual(set(r["writers"]), {"spec", "plan", "build", "audit"})
        self.assertEqual(r["revision"], reviewtables.revision())

    def test_reviewers_with_no_files_still_has_a_revision(self):
        os.unlink(os.environ["RELAY_CONFIG"])
        self.assertTrue(snapshot.reviewers()["revision"])
```

- [ ] **Step 2: Run it to see it fail.** `python3.11 -m unittest tests.test_ui_snapshot -v`. Expected: `AttributeError: ... 'reviewers'`.

- [ ] **Step 3: Implement.** In `reviewjobs.py`:

```python
def option(spec, limits):
    blocked, why = availability.blocked(spec.provider, limits)
    return {"id": spec_id(spec), "provider": spec.provider, "model": spec.model, "effort": spec.effort,
            "available": not blocked, "reason": why if blocked else ""}
```

and make `choices` append `option(spec, cfg["limits"])`. In `snapshot.py` (imports: add `reviewtables`, `tomllib`):

```python
def _overriding(root):
    """Projects whose own docs/relay/config.toml sets [review.prefer] (models-tab D6)."""
    out, top = [], os.path.realpath(root)
    for repo in status.checkouts(root):
        if os.path.dirname(os.path.realpath(repo)) != top:
            continue  # worktrees repeat their repository
        path = os.path.join(state.relay_dir(repo), "config.toml")
        try:
            with open(path, "rb") as f:
                prefer = (tomllib.load(f).get("review") or {}).get("prefer")
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(prefer, dict) and prefer:
            out.append({"repo": os.path.basename(repo), "path": path, "authors": sorted(prefer)})
    return out


def reviewers(now=None):
    """The owner's review tables with the timed file applied, for the Models tab (models-tab R9)."""
    now = time.time() if now is None else now
    revision = reviewtables.revision()  # first: a save that lands while this builds is caught as stale
    cfg, base = config.load(), config.load(timed=False)
    tz, limits = base["limits"]["timezone"], cfg["limits"]
    timed, problems = reviewtables.read(now)
    changes = reviewtables.latest()
    authors = {}
    for author in reviewtables.AUTHORS:
        t, change = timed.get(author), changes.get(author)
        authors[author] = {
            "entries": [reviewjobs.option(s, limits) for s in config.review_preferences(cfg, author)],
            "permanent": [reviewjobs.option(s, limits) for s in config.review_preferences(base, author)],
            "until": t["until"] if t else None,
            "until_label": reviewtables.when(t["until"], tz) if t else None,
            "until_iso": reviewtables.iso(t["until"], tz) if t else None,
            "changed": {"label": reviewtables.when(change["at"], tz), "by": change["by"]} if change else None}
    known = {f"{p['provider']}:{p['model']}" for a in authors.values() for p in a["entries"] + a["permanent"]}
    for provider, model in base["roles"]["reviewer_models"].items():
        known.add(f"{provider}:{config.parse_model_spec(model, provider).model}")
    for row in ledger.read():
        if row.get("provider") in config.PROVIDERS and row.get("model"):
            known.add(f"{row['provider']}:{row['model']}")
    known_list = []
    for item in sorted(known):
        option = reviewjobs.option(config.parse_model_spec(item), limits)
        known_list.append({"id": item, "provider": option["provider"], "available": option["available"],
                           "reason": option["reason"]})
    review = base.get("review") or {}
    return {"authors": authors, "writers": {r: base["roles"][r] for r in config.ROLE_KEYS},
            "effort": {k: review.get(k) for k in ("effort", "final_effort", "release_effort")},
            "known": known_list, "overriding": _overriding(status.projects_root()), "problems": problems,
            "timezone": tz, "revision": revision}


def _reviewers_or_error():
    try:
        return reviewers()
    except (RelayError, OSError, ValueError, KeyError, TypeError) as e:
        return {"error": str(e)}
```

In `build()`, add `"reviewers": _reviewers_or_error()` to the returned dict.

- [ ] **Step 4: Run the snapshot tests.** `python3.11 -m unittest tests.test_ui_snapshot -v`. Expected: PASS.

- [ ] **Step 5: Commit.** `git add relaylib/reviewjobs.py relaylib/ui/snapshot.py tests/test_ui_snapshot.py && git commit -m "feat: the dashboard snapshot carries the owner's reviewer tables"`

---

### Task 6: Server endpoints `/api/roles` and `/api/roles/end`

**Files:**
- Modify: `relaylib/ui/server.py` (`do_POST`)
- Test: `tests/test_ui_server.py`

**Interfaces:**
- Consumes: Task 3 `reviewtables.save`, `end`, `Stale`, `revision`; Task 5 `snapshot.reviewers`.
- Produces: `POST /api/roles` with `{author, table, entries, until, seen}`, `POST /api/roles/end` with `{author, seen}`. 200 `{"message", "reviewers"}`; 400 `{"error"}`; 409 `{"error", "fresh": reviewers}`. Both call `cache.refresh()`.

- [ ] **Step 1: Write the failing test** in `ServerTest` (`setUp` already sets `RELAY_HOME` to `self.tmp`; patch `RELAY_CONFIG` to a file in `self.tmp` and `RELAY_ROOT` to an empty temp folder inside the test):

```python
    def test_roles_endpoints(self):
        cfg = os.path.join(self.tmp, "config.toml")
        with open(cfg, "w") as f:
            f.write('[review.prefer]\nclaude = ["codex:gpt-6-astra"]\n')
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with mock.patch.dict(os.environ, {"RELAY_CONFIG": cfg, "RELAY_ROOT": root}), \
                mock.patch.object(self.cache, "refresh") as refresh:
            seen = snapshot.reviewers()["revision"]
            body = {"author": "claude", "table": "timed", "entries": ["claude:claude-fable-5-1@high"],
                    "until": "23:59", "seen": seen}
            code, text = self.request("/api/roles", "POST", body)
            self.assertEqual(code, 200, text)
            result = json.loads(text)
            self.assertIn("until", result["message"])
            self.assertEqual(result["reviewers"]["authors"]["claude"]["entries"][0]["id"],
                             "claude:claude-fable-5-1@high")
            self.assertGreaterEqual(refresh.call_count, 1)
            code, text = self.request("/api/roles", "POST", body)                       # seen is now stale
            self.assertEqual(code, 409, text)
            self.assertIn("fresh", json.loads(text))
            fresh = json.loads(text)["fresh"]["revision"]
            for bad in ({"entries": ["gemini:x"]}, {"entries": []}, {"until": "2020-01-01T00:00Z"},
                        {"table": "permanent"}, {"entries": "codex:a"}, {"seen": 5}):
                with self.subTest(bad=bad):
                    code, text = self.request("/api/roles", "POST", {**body, "seen": fresh, **bad})
                    self.assertEqual(code, 400, text)
            self.assertEqual(snapshot.reviewers()["revision"], fresh)                    # nothing written
            code, text = self.request("/api/roles/end", "POST", {"author": "claude", "seen": fresh})
            self.assertEqual(code, 200, text)
            code, text = self.request("/api/roles/end", "POST",
                                      {"author": "claude", "seen": snapshot.reviewers()["revision"]})
            self.assertEqual((code, json.loads(text)["message"]), (200, "no temporary table for claude"))
            self.assertEqual(self.request("/api/roles", "POST", body, token="wrong")[0], 403)
```

- [ ] **Step 2: Run it to see it fail.** `python3.11 -m unittest tests.test_ui_server -k roles -v`. Expected: `404 != 200`.

- [ ] **Step 3: Implement.** Import `reviewtables` in `server.py`. In `do_POST`, before the final `else`:

```python
            elif path in ("/api/roles", "/api/roles/end"):
                try:
                    if not isinstance(body.get("seen"), str):
                        raise RelayError("seen must be the revision the page loaded")
                    if path == "/api/roles":
                        message = reviewtables.save(body.get("author"), body.get("table"), body.get("entries"),
                                                    body.get("until") or None, "dashboard", body["seen"])
                    else:
                        message = reviewtables.end(body.get("author"), "dashboard", body["seen"])
                    self.reply(200, {"message": message, "reviewers": snapshot.reviewers()})
                except reviewtables.Stale as e:
                    self.reply(409, {"error": str(e), "fresh": snapshot.reviewers()})
                finally:
                    self.server.cache.refresh()
```

`RelayError` from `save`/`end` (validation, past or distant end, busy lock) falls through to the existing 400 handler.

- [ ] **Step 4: Run the server tests.** `python3.11 -m unittest tests.test_ui_server -v`. Expected: PASS.

- [ ] **Step 5: Commit.** `git add relaylib/ui/server.py tests/test_ui_server.py && git commit -m "feat: dashboard endpoints to save and end reviewer tables"`

---

### Task 7: Models tab and the Reviewers panel (read side)

**Files:**
- Modify: `relaylib/ui/page.html`
- Test: `tests/test_ui_server.py`

**Interfaces:**
- Consumes: `data.reviewers` from Task 5.
- Produces: page functions `liveTimed(author, nowMs) -> bool`, `shownEntries(author, nowMs) -> list` (pure, tested in Node), `renderReviewers()`; buttons carrying `data-edit="<author>:permanent|timed|new"` and `data-end="<author>"` for Task 8.

- [ ] **Step 1: Write the failing tests:**

```python
    def test_models_tab_and_reviewers_panel(self):
        page = self.request("/?t=test-token")[1]
        for piece in ('id="tab-models"', ">Models</a>", "#view=models", "view==='usage'", 'id="reviewers"',
                      'id="reviewer-rows"', 'id="writer-roles"', "informational", "temporary until",
                      "End now", "Edit permanent", "Set temporary", "Edit temporary", "renderReviewers()",
                      "overrides your table for that project"):
            self.assertIn(piece, page)
        self.assertNotIn(">Usage</a>", page)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_an_expired_timed_table_stops_showing_without_the_server(self):
        page = self.request("/?t=test-token")[1]
        funcs = re.search(r"function liveTimed\(.*?(?=function renderReviewers\()", page).group(0)
        script = funcs + """
const a={until:1000,entries:[{id:'t'}],permanent:[{id:'p'}]};
console.log(JSON.stringify({before:shownEntries(a,999000).map(e=>e.id), after:shownEntries(a,1000000).map(e=>e.id),
  live:liveTimed(a,999000), none:liveTimed({until:null},0)}));"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, {"before": ["t"], "after": ["p"], "live": True, "none": False})
```

Also update `test_page_sections_and_local_assets` if it asserts the tab text (it asserts `id="usage"`, which stays).

- [ ] **Step 2: Run them to see them fail.** `python3.11 -m unittest tests.test_ui_server -k "models or expired" -v`. Expected: FAIL on `id="tab-models"`.

- [ ] **Step 3: Implement** in `page.html`:
  - Nav: `<a id="tab-models" href="#view=models">Models</a>` replaces the Usage link. Rename `#tab-usage` handlers to `#tab-models`, writing `#view=models`. Keep `usage-view` as the container id (tests and CSS use it). `showView(initial.get('view')==='models'||initial.get('view')==='usage'?'usage':'projects')` so the old link still opens the tab (R12).
  - At the top of `#usage-view`, before `<section id="usage">`:

```html
<section id="reviewers" class="section"><div class="section-head"><h2>Reviewers</h2></div><p class="reason">Who reviews each author's work. The first available reviewer wins.</p><div id="reviewer-notes" class="reason"></div><div id="reviewer-rows"></div><h3>Writers</h3><p class="reason">Who writes each stage. Shown for information until relay launches writers.</p><div id="writer-roles"></div></section>
```

  - Script, placed before `renderUsage`:

```js
function liveTimed(a,nowMs){return !!a.until&&a.until*1000>nowMs;}
function shownEntries(a,nowMs){return liveTimed(a,nowMs)?a.entries:a.permanent;}
function chain(list){return list.map(e=>e.id+(e.available?'':' (out: '+e.reason+')')).join(' → ');}
function renderReviewers(){const r=data.reviewers||{},rows=$('reviewer-rows'),notes=$('reviewer-notes');if(r.error){rows.replaceChildren(node('p','Could not read the reviewer tables: '+r.error,'reason'));notes.replaceChildren();return;}const now=Date.now();notes.replaceChildren(...(r.problems||[]).map(p=>node('p',p)),...(r.overriding||[]).map(o=>node('p',o.repo+' sets its own reviewers for '+o.authors.join(', ')+' in '+o.path+'; that overrides your table for that project (a temporary table still wins).')));rows.replaceChildren(...['claude','codex','owner'].map(author=>{const a=r.authors?.[author];if(!a)return node('div');const box=node('div',null,'reviewer-row'),live=liveTimed(a,now);box.append(node('h3',author==='owner'?'Work by you':'Work by '+author));box.append(node('p',chain(shownEntries(a,now))+(live?' · temporary until '+a.until_label:'')));if(live)box.append(node('p','Then back to: '+chain(a.permanent),'reason'));if(a.changed)box.append(node('p','Last changed '+a.changed.label+' by '+a.changed.by,'reason'));const acts=node('div',null,'actions'),btn=(label,edit)=>{const b=node('button',label);b.dataset.edit=author+':'+edit;b.onclick=()=>openRolesEditor(author,edit,b);return b;};acts.append(btn('Edit permanent','permanent'));if(live){acts.append(btn('Edit temporary','timed'));const end=node('button','End now');end.dataset.end=author;end.onclick=()=>confirmEnd(author,a);acts.append(end);}else acts.append(btn('Set temporary','new'));box.append(acts);return box;}));$('writer-roles').replaceChildren(...Object.entries(r.writers||{}).map(([role,spec])=>node('p',role+': '+spec)));}
```

  `openRolesEditor` and `confirmEnd` are added in Task 8; until then define them as no-ops so this task's page works: `function openRolesEditor(){}function confirmEnd(){}` (Task 8 replaces both).
  - In `load()`, call `renderReviewers();` right after `renderUsage();`.

- [ ] **Step 4: Run the server tests.** `python3.11 -m unittest tests.test_ui_server -v`. Expected: PASS.

- [ ] **Step 5: Commit.** `git add relaylib/ui/page.html tests/test_ui_server.py && git commit -m "feat: the Usage tab becomes Models, with the reviewer tables on top"`

---

### Task 8: Edit dialog, save with confirmation, End now, focus

**Files:**
- Modify: `relaylib/ui/page.html`
- Test: `tests/test_ui_server.py`

**Interfaces:**
- Consumes: Task 7 `data-edit`/`data-end` buttons, `renderReviewers`; Task 6 endpoints; existing `api`, `notice`, `node`, `$`, `#confirm`, `#do-action`, `pending`.
- Produces: pure `editList(list, op, i, value) -> {list, error}` and `effortChoices(list) -> string[]` (Node-tested), `openRolesEditor(author, mode, button)`, `confirmEnd(author, a)`.

- [ ] **Step 1: Write the failing tests:**

```python
    def test_roles_editor_on_the_page(self):
        page = self.request("/?t=test-token")[1]
        for piece in ('<dialog id="roles-edit"', 'id="re-list"', '<label for="re-add">', 'list="re-known"',
                      '<label for="re-until">', 'id="re-save"', "'/api/roles'", "'/api/roles/end'",
                      "until_iso", "relay checks only its shape", "Changed since you looked",
                      "rolesReturn", "kind==='roles'"):
            self.assertIn(piece, page)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_editor_list_rules_run_in_node(self):
        page = self.request("/?t=test-token")[1]
        funcs = re.search(r"function editList\(.*?(?=function openRolesEditor\()", page).group(0)
        script = funcs + """
const L=[{provider:'codex',model:'a',effort:null},{provider:'claude',model:'b',effort:'max'}];
const ids=r=>r.list.map(e=>e.provider+':'+e.model+(e.effort?'@'+e.effort:'')).join(',');
console.log(JSON.stringify({up:ids(editList(L,'up',1)), topUp:ids(editList(L,'up',0)), down:ids(editList(L,'down',0)),
  remove:ids(editList(L,'remove',0)), effort:ids(editList(L,'effort',0,'high')), clear:ids(editList(L,'effort',1,'')),
  add:ids(editList(L,'add',0,' claude:c ')), dup:editList(L,'add',0,'codex:a').error, bad:editList(L,'add',0,'gpt').error,
  untouched:ids({list:L}), efforts:effortChoices(L)}));"""
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["up"], "claude:b@max,codex:a")
        self.assertEqual(out["topUp"], "codex:a,claude:b@max")
        self.assertEqual(out["down"], "claude:b@max,codex:a")
        self.assertEqual(out["remove"], "claude:b@max")
        self.assertEqual(out["effort"], "codex:a@high,claude:b@max")
        self.assertEqual(out["clear"], "codex:a,claude:b")
        self.assertEqual(out["add"], "codex:a,claude:b@max,claude:c")
        self.assertIn("already", out["dup"])
        self.assertIn("provider:model", out["bad"])
        self.assertEqual(out["untouched"], "codex:a,claude:b@max")
        self.assertEqual(out["efforts"], ["low", "medium", "high", "xhigh", "max"])
```

- [ ] **Step 2: Run them to see them fail.** `python3.11 -m unittest tests.test_ui_server -k "editor" -v`. Expected: FAIL on `<dialog id="roles-edit"`.

- [ ] **Step 3: Implement** in `page.html`:
  - Dialog after `#review-request`:

```html
<dialog id="roles-edit"><h2 id="re-title">Edit reviewers</h2><ol id="re-list"></ol><label for="re-add">Add reviewer</label><input id="re-add" list="re-known" placeholder="provider:model"><datalist id="re-known"></datalist><button id="re-add-go">Add</button><p id="re-note" class="reason"></p><div id="re-until-box"><label for="re-until">Until (HH:MM, or a date and time like 2026-10-07T23:00-04:00; at most 7 days)</label><input id="re-until"></div><div class="actions"><button id="re-cancel">Cancel</button><button id="re-save" class="primary">Save</button></div></dialog>
```

  - Script (pure helpers first, then `openRolesEditor`):

```js
function editList(list,op,i,value){const out=list.map(e=>({...e}));if(op==='up'&&i>0)[out[i-1],out[i]]=[out[i],out[i-1]];else if(op==='down'&&i<out.length-1)[out[i],out[i+1]]=[out[i+1],out[i]];else if(op==='remove')out.splice(i,1);else if(op==='effort')out[i].effort=value||null;else if(op==='add'){const m=/^(claude|codex):([^@\s]+)$/.exec((value||'').trim());if(!m)return {list,error:'Use provider:model, for example codex:gpt-6-astra'};if(out.some(e=>e.provider===m[1]&&e.model===m[2]))return {list,error:m[1]+':'+m[2]+' is already in the list'};out.push({provider:m[1],model:m[2],effort:null});}return {list:out,error:''};}
function effortChoices(list){const base=['low','medium','high','xhigh'];return base.concat(list.map(e=>e.effort).filter((v,i,a)=>v&&!base.includes(v)&&a.indexOf(v)===i));}
const specOf=e=>e.provider+':'+e.model+(e.effort?'@'+e.effort:'');
let rolesEdit=null,rolesReturn=null;
function openRolesEditor(author,mode,button){const a=data.reviewers.authors[author],timed=mode!=='permanent';rolesReturn=button.dataset.edit;rolesEdit={author,mode,timed,seen:data.reviewers.revision,old:(mode==='timed'?a.entries:a.permanent).map(e=>({provider:e.provider,model:e.model,effort:e.effort})),list:null};rolesEdit.list=rolesEdit.old.map(e=>({...e}));$('re-title').textContent=(timed?'Temporary':'Permanent')+' reviewers for work by '+author;$('re-until-box').hidden=!timed;$('re-until').value=mode==='timed'?a.until_iso:'';$('re-known').replaceChildren(...data.reviewers.known.map(k=>{const o=document.createElement('option');o.value=k.id;o.label=k.available?k.id:k.id+' (out: '+k.reason+')';return o;}));$('re-note').textContent='';drawRolesList();$('roles-edit').showModal();}
function drawRolesList(){const efforts=effortChoices(rolesEdit.list),known=new Map(data.reviewers.known.map(k=>[k.id,k]));$('re-list').replaceChildren(...rolesEdit.list.map((e,i)=>{const li=node('li'),id=e.provider+':'+e.model,k=known.get(id);li.append(node('span',id+(k&&!k.available?' (out: '+k.reason+')':'')));const sel=document.createElement('select');sel.id='re-effort-'+i;const lab=node('label','Effort');lab.htmlFor=sel.id;for(const v of ['',...efforts]){const o=document.createElement('option');o.value=v;o.textContent=v||'default';sel.append(o);}sel.value=e.effort||'';sel.onchange=()=>apply('effort',i,sel.value);li.append(lab,sel);for(const [op,label] of [['up','Move up'],['down','Move down'],['remove','Remove']]){const b=node('button',label);b.onclick=()=>apply(op,i);li.append(b);}return li;}));$('re-save').disabled=!rolesEdit.list.length;}
function apply(op,i,value){const r=editList(rolesEdit.list,op,i,value);rolesEdit.list=r.list;$('re-note').textContent=r.error;if(op==='add'&&!r.error){const id=value.trim();if(!data.reviewers.known.some(k=>k.id===id))$('re-note').textContent=id+' is not a model relay has used: relay checks only its shape, and a mistyped model placed first makes reviews fail.';$('re-add').value='';}drawRolesList();}
$('re-add-go').onclick=()=>apply('add',0,$('re-add').value);
$('re-cancel').onclick=()=>$('roles-edit').close();
$('roles-edit').addEventListener('close',()=>{if(!pending||pending.kind!=='roles')returnRolesFocus();});
function returnRolesFocus(){const b=rolesReturn&&document.querySelector('[data-edit="'+rolesReturn+'"]');if(b)b.focus({preventScroll:true});}
$('re-save').onclick=()=>{const e=rolesEdit,until=$('re-until').value.trim();if(e.timed&&!until){$('re-note').textContent='A temporary table needs an end time.';return;}const which=e.timed?'temporary table until '+until:'permanent table';pending={kind:'roles',path:'/api/roles',body:{author:e.author,table:e.timed?'timed':'permanent',entries:e.list.map(specOf),until:e.timed?until:null,seen:e.seen}};$('roles-edit').close();$('confirm-title').textContent='Save the '+which+' for work by '+e.author+'?';$('confirm-effect').textContent='Before: '+(e.mode==='new'?'none':e.old.map(specOf).join(' → '))+'. After: '+e.list.map(specOf).join(' → ')+'.';$('confirm-revision').textContent='revision '+e.seen;$('confirm').showModal();};
function confirmEnd(author,a){rolesReturn=author+':permanent';pending={kind:'roles',path:'/api/roles/end',body:{author,seen:data.reviewers.revision}};$('confirm-title').textContent='End the temporary table for work by '+author+' now?';$('confirm-effect').textContent='Before: '+a.entries.map(e=>e.id).join(' → ')+' until '+a.until_label+'. After: '+a.permanent.map(e=>e.id).join(' → ')+'.';$('confirm-revision').textContent='revision '+data.reviewers.revision;$('confirm').showModal();}
```

  - In the existing `$('do-action').onclick`, handle the new kind first:

```js
if(action.kind==='roles'){try{const r=await api(action.path,action.body);data.reviewers=r.reviewers;notice(r.message);}catch(e){if(e.fresh)data.reviewers=e.fresh;notice(e.status===409?'Changed since you looked: '+e.message:e.message,true,e.fresh);}finally{pending=null;renderReviewers();returnRolesFocus();}return;}
```

  - On Cancel of `#confirm` for a roles action, clear `pending` and call `returnRolesFocus()` (add to the existing `#cancel-action` handler: `if(pending?.kind==='roles'){pending=null;returnRolesFocus();}`).

- [ ] **Step 4: Run the server tests and check the page by hand.** `python3.11 -m unittest tests.test_ui_server -v`. Expected: PASS. Then `RELAY_HOME=$(mktemp -d) relay ui` from an owner terminal (the owner runs this; an agent asks the owner or uses the run skill with a temp `RELAY_HOME` and `RELAY_CONFIG`): open Models, Set temporary on claude with `23:59`, confirm, see "temporary until", End now, see the permanent table, tab through every control.

- [ ] **Step 5: Commit.** `git add relaylib/ui/page.html tests/test_ui_server.py && git commit -m "feat: edit reviewer tables from the Models tab"`

---

### Task 9: README and the full suite

**Files:**
- Modify: `README.md` (reviewer paragraph ~73, command list ~77)

- [ ] **Step 1: Update README.** In the `[review.prefer]` paragraph, after the `relay roles set review.claude ...` sentence, add:

```markdown
Changing a table is your decision: `relay roles set` and `relay roles end` run in your own terminal, and an agent passes them on only when you ask, with `--relayed` (logged with the session). A list may not name the same model twice, and an effort is one lowercase word (`low`, `high`, `xhigh`). Add `--until 23:00` (or an ISO date-time with a zone, at most 7 days away) to make a change temporary: relay keeps it in `~/.relay/review-until.json` and returns to your normal table by itself when the time passes, even if nothing is running then. A temporary table wins over a project's own `docs/relay/config.toml`. `relay roles end review.claude` (or `all`) ends it early. Every change is logged in `~/.relay/roles-log.jsonl`, and `relay roles` shows the latest one per author.
```

In the dashboard section, rename Usage to Models and add one sentence: "The Models tab shows the reviewer tables at the top; you can reorder, add and remove reviewers, set their effort, and make a change temporary. Writer roles are shown for information." Add to the command list:

```
    relay roles set review.claude "claude:claude-fable-5-1, codex:gpt-6-astra" --until 23:00
    relay roles end review.claude            back to the normal table now (or: all)
```

- [ ] **Step 2: Check for em-dashes and run the full suite.** `grep -n $'—' README.md relaylib/reviewtables.py relaylib/ui/page.html docs/relay/models-tab/plan.md` (expected: no output), then `python3.11 -m unittest discover -s tests -t . -v`. Expected: all tests pass (380 before this feature, plus the new ones).

- [ ] **Step 3: Commit.** `git add README.md && git commit -m "docs: the Models tab, temporary reviewer tables and owner-only roles changes"`
