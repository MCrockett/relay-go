# writer-usage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Count the tokens of the Claude Code and Codex sessions that held relay features, by feature, stage and model, from the CLIs' own local logs, and show them on the Models tab and in `relay cost`.

**Architecture:** Three new modules, one responsibility each. `relaylib/transcripts.py` finds and incrementally parses the two log formats into turns (timestamps, model, token numbers, Claude message id and git branch only) with D6 diagnostics, cached in `~/.relay/writer-usage.json`. `relaylib/holds.py` reads each feature's published `state.md` history and builds hold windows (D4, D9). `relaylib/writerusage.py` joins them: one turn to one feature (D10), active minutes (D7), and period totals (R9). The snapshot, page and `relay cost` are thin callers.

**Tech Stack:** Python 3.11 standard library, `unittest`, temp git repos from `tests/helpers.py`, Node for the page's pure functions (skipped without Node).

**Spec:** `docs/relay/writer-usage/spec.md` (GO in round 3, `reviews/spec-3.codex.md`). Executors read both.

## Global Constraints

- Python 3.11 standard library only; one module per responsibility in `relaylib/`.
- Plain English in copy and docs; no em-dashes.
- Commits: `<type>: short summary`, ending with the session's attribution line.
- Tests: `python3.11 -m unittest discover -s tests -t . -v`. Every test sets `RELAY_HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR` to temporary folders (R13). Never call real Claude or Codex.
- Read only timestamps, model names, usage numbers, Claude message ids and `gitBranch`, session ids and file paths; never store message text (R4).
- Cache file `~/.relay/writer-usage.json`, mode 0600; lock `writer-usage.lock` in relay home, 1 second wait (D5).
- Token fields match the ledger (D3): input includes cache reads and writes; cached is cache reads.
- Minutes: gaps between consecutive turns of one session, each capped at 300 seconds, credited to the later turn (D7).

## Spec clarifications

- **D6 threshold:** the spec says 200 lines is "far above" the longest healthy run. Measured on this machine (300 newest files of each kind, 2026-10-06): the longest run of lines between usage records is 94 for Claude and 16 for Codex. 200 is about twice the Claude worst case. The threshold stays 200.
- **Owner-written work:** a state whose `owner.provider` is `owner` has no CLI log. Its windows are built (so they end other sessions' overlap correctly) but no log is looked up and nothing is reported unreadable for it (spec non-goal).
- **Claude projects folder:** `$CLAUDE_CONFIG_DIR/projects` when `CLAUDE_CONFIG_DIR` is set (Claude Code's own override), otherwise `~/.claude/projects`.
- **Handoff commit:** recognized by its subject, `relay: handoff <slug>`, which `cmd_handoff` writes.
- **Feature rows' name:** `<repo> · <feature> · <stage> (<provider>)`, so the existing sort-by-name and tie-break rules apply unchanged.

## Review Focus

1. A Claude transcript that repeats one message id with growing usage across two refreshes: the turn is replaced, not added (Task 2 test).
2. A Codex rollout resumed or restarted so the running total drops: the count restarts from zero and nothing goes negative (Task 2 test).
3. A session holding two features in one repo that hands off one: both windows close at the handoff (Task 4 test).
4. A merged feature whose state still says ready-to-merge, with the session working on afterwards: later turns are unattributed (Task 4 and Task 5 tests).
5. The dashboard and `relay cost` refreshing at the same time: one waits up to a second, then uses its in-memory result and says "usage cache not saved" (Task 3 test).

## File Structure

- Modify `relaylib/config.py`: `write_lock(timeout=None, name="roles.lock")`.
- Modify `relaylib/merged.py`: `merged_at(repo_dir, st, branch_head=None) -> float | None`.
- Create `relaylib/transcripts.py`, `relaylib/holds.py`, `relaylib/writerusage.py`.
- Modify `relaylib/ui/snapshot.py` (`build` adds `writing`), `relaylib/ui/page.html` (shared table renderer, two writing tables), `relaylib/commands.py` (`cmd_cost`), `README.md`.
- Tests: `tests/test_transcripts.py`, `tests/test_holds.py`, `tests/test_writerusage.py`, plus `tests/test_merged.py`, `tests/test_config.py`, `tests/test_ui_snapshot.py`, `tests/test_ui_server.py`, `tests/test_commands.py`.

## Coverage

| Requirement | Tasks |
|---|---|
| D1, R1 | 2 |
| D3, R2, R3, R4 | 2 |
| D5, R5, F3, F6 | 3 |
| D6, F1, F2, F4 | 2, 3, 5 |
| D4, D9, R6, R7, R8a, F5, F7 | 1, 4 |
| D10, D7, D2, R8, R9 | 5 |
| D8, R10 | 6 |
| R11 | 7 |
| R12 | 8 |
| R13 | 1 to 7 |

---

### Task 1: Named locks and merge time

**Files:**
- Modify: `relaylib/config.py` (`write_lock`)
- Modify: `relaylib/merged.py`
- Test: `tests/test_config.py`, `tests/test_merged.py`

**Interfaces:**
- Produces: `config.write_lock(timeout=None, name="roles.lock")`; `merged.merged_at(repo_dir, st, branch_head=None) -> float | None` (epoch seconds).

- [ ] **Step 1: Write the failing tests.** In `tests/test_config.py`, extend the lock test: holding `writer-usage.lock` does not block `write_lock()` (roles) and does block `write_lock(name="writer-usage.lock")` with "busy, try again". In `tests/test_merged.py` (follow its existing fake-gh setup):

```python
    def test_merged_at_from_gh_then_cached(self):
        # fake gh answers {"state": "MERGED", "mergedAt": "2026-10-06T20:00:00Z"} once
        st = {"status": "ready-to-merge", "pr": 7}
        self.assertEqual(merged.merged_at(self.repo, st), 1791403200.0)
        # second call: gh now fails, cached value still returned
        self.assertEqual(merged.merged_at(self.repo, st), 1791403200.0)

    def test_merged_at_falls_back_to_the_merge_commit(self):
        # temp repo: branch commit X, merged into origin/develop with a merge commit at a known GIT_COMMITTER_DATE;
        # gh unavailable (RELAY_GH_BIN points at a failing script)
        self.assertEqual(merged.merged_at(self.repo, {"status": "ready-to-merge", "pr": 7}, branch_head=x), merge_time)
        self.assertIsNone(merged.merged_at(self.repo, {"status": "drafting"}, branch_head="0" * 40))
```

- [ ] **Step 2: Run them to see them fail.** `python3.11 -m unittest tests.test_config tests.test_merged -v`.
- [ ] **Step 3: Implement.** `write_lock` takes `name` and opens `os.path.join(relay_home(), name)`; every existing caller keeps the default. In `merged.py`:

```python
def merged_at(repo_dir, st, branch_head=None):
    """When the feature's PR merged (writer-usage D9): gh mergedAt, cached; else the first merge commit on
    origin/develop or origin/main that contains branch_head; else None."""
    key = f"{gitops.origin_url(repo_dir)}#{st.get('pr')}@at"
    if st.get("pr"):
        cached = _load().get(key)
        if isinstance(cached, (int, float)):
            return float(cached)
        try:
            info = gitops.gh_json(repo_dir, ["pr", "view", str(st["pr"]), "--json", "state,mergedAt"])
            if info.get("state") == "MERGED" and info.get("mergedAt"):
                at = datetime.datetime.fromisoformat(info["mergedAt"].replace("Z", "+00:00")).timestamp()
                with _WRITE:
                    cache = _load()
                    cache[key] = at
                    os.makedirs(relay_home(), exist_ok=True)
                    with open(_cache_path(), "w") as f:
                        json.dump(cache, f)
                return at
        except (RelayError, OSError, ValueError):
            pass
    if branch_head:
        for base in ("origin/develop", "origin/main"):
            out = gitops.git(repo_dir, "log", "--merges", "--ancestry-path", "--reverse", "--format=%ct",
                             f"{branch_head}..{base}", check=False).stdout.split()
            if out:
                return float(out[0])
    return None
```

`is_done` keeps reading `cache.get(key)` for its own key; the `@at` keys never collide with it.

- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat: named relay-home locks and the time a feature's PR merged"`

---

### Task 2: Transcript parsing and discovery

**Files:**
- Create: `relaylib/transcripts.py`
- Test: `tests/test_transcripts.py`

**Interfaces:**
- Produces:
  - `claude_projects() -> str`, `codex_sessions() -> str` (D1 roots, honoring `CLAUDE_CONFIG_DIR` and `CODEX_HOME`).
  - `files_for(provider, session) -> list[str]` (Claude: `projects/*/<session>.jsonl` plus `projects/*/<session>/subagents/*.jsonl`; Codex: `sessions/*/*/*/rollout-*-<session>.jsonl`; sorted).
  - `class FileState` with `parse(lines: list[str]) -> None` that updates `turns: dict[str, dict]` (key: Claude message id, or `codex:<n>` per counted event), `candidates`, `bad`, `lines_since_record`, `had_valid`, and for Codex `model`, `last_total` (the running baseline). Each turn is `{"at": float, "model": str, "input": int, "cached": int, "output": int, "branch": str | None}`.
  - `FileState.problem() -> str | None` per D6 and F1: `"no usage records"` when lines were read but none valid; otherwise the non-empty items joined with "; ": `"partial: N of M usage records unreadable"` when `bad` (usage candidates or context records that fail validation), `"N lines are not JSON"` when `malformed` (complete lines that do not parse; a partial last line is never handed to `parse`, Task 3), and `"format changed: 200 lines without usage records"` when `lines_since_record >= 200` after a valid record.
  - Claude usage requires all four of `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens` as non-negative integers; a missing field makes the record bad, never zero. Codex requires `input_tokens`, `cached_input_tokens`, `output_tokens` in `total_token_usage`.
  - `FileState.to_json() / FileState.from_json(d)` for the cache (Task 3).

- [ ] **Step 1: Write the failing tests.** Build lines in the test with `json.dumps`; no fixture files needed beyond what the test writes:

```python
def claude(mid, at, model="claude-opus-5-5", inp=10, cr=0, cw=0, out=5, branch="feat/x", text="SECRET TEXT"):
    return json.dumps({"type": "assistant", "timestamp": at, "gitBranch": branch, "message": {
        "id": mid, "model": model, "role": "assistant", "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": inp, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw,
                  "output_tokens": out}}})

def codex_ctx(model):
    return json.dumps({"timestamp": "2026-10-06T16:00:00Z", "type": "turn_context", "payload": {"model": model}})

def codex_count(at, inp, cached, out):
    return json.dumps({"timestamp": at, "type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out}}}})


class ParseTest(unittest.TestCase):
    def test_claude_dedupes_by_message_id_last_record_wins(self):
        fs = transcripts.FileState("claude")
        fs.parse([claude("m1", "2026-10-06T16:00:00Z", out=1), claude("m1", "2026-10-06T16:00:01Z", out=7),
                  claude("m2", "2026-10-06T16:01:00Z", inp=2, cr=100, cw=20)])
        self.assertEqual(len(fs.turns), 2)
        self.assertEqual(fs.turns["m1"]["output"], 7)
        self.assertEqual((fs.turns["m2"]["input"], fs.turns["m2"]["cached"]), (122, 100))
        fs.parse([claude("m1", "2026-10-06T16:00:02Z", out=9)])          # appended on a later refresh
        self.assertEqual(fs.turns["m1"]["output"], 9)
        self.assertIsNone(fs.problem())

    def test_claude_skips_on_purpose_without_warning(self):
        fs = transcripts.FileState("claude")
        synthetic = json.loads(claude("m3", "2026-10-06T16:02:00Z", model="<synthetic>"))
        no_id = json.loads(claude("x", "2026-10-06T16:02:00Z")); del no_id["message"]["id"]
        fs.parse([claude("m1", "2026-10-06T16:00:00Z"), json.dumps(synthetic), json.dumps(no_id),
                  json.dumps({"type": "user", "message": {"content": "hi"}})])
        self.assertEqual(list(fs.turns), ["m1"])
        self.assertIsNone(fs.problem())

    def test_malformed_lines_and_missing_fields_are_reported_not_zero(self):
        missing = json.loads(claude("m2", "2026-10-06T16:00:00Z"))
        del missing["message"]["usage"]["cache_read_input_tokens"]
        fs = transcripts.FileState("claude")
        fs.parse([claude("m1", "2026-10-06T16:00:00Z"), "not json", json.dumps(missing)])
        self.assertEqual(list(fs.turns), ["m1"])
        self.assertEqual(fs.problem(), "partial: 1 of 2 usage records unreadable; 1 lines are not JSON")

    def test_claude_bad_usage_is_partial(self):
        bad = json.loads(claude("m2", "2026-10-06T16:00:00Z")); bad["message"]["usage"]["output_tokens"] = "lots"
        fs = transcripts.FileState("claude")
        fs.parse([claude("m1", "2026-10-06T16:00:00Z"), json.dumps(bad)])
        self.assertEqual(fs.problem(), "partial: 1 of 2 usage records unreadable")

    def test_codex_counts_increases_and_restarts_after_a_drop(self):
        fs = transcripts.FileState("codex")
        fs.parse([codex_ctx("gpt-6-astra"), codex_count("2026-10-06T16:00:00Z", 100, 80, 5),
                  codex_count("2026-10-06T16:00:01Z", 100, 80, 5),           # repeated: adds nothing
                  codex_count("2026-10-06T16:01:00Z", 250, 200, 9),
                  codex_count("2026-10-06T16:02:00Z", 40, 0, 2)])             # dropped: new count from zero
        got = sorted((t["input"], t["cached"], t["output"]) for t in fs.turns.values())
        self.assertEqual(got, [(40, 0, 2), (100, 80, 5), (150, 120, 4)])
        self.assertTrue(all(t["model"] == "gpt-6-astra" for t in fs.turns.values()))
        fs.parse([json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": None}})])
        self.assertIsNone(fs.problem())                                       # info: null is ignored

    def test_codex_context_without_model_is_partial(self):
        fs = transcripts.FileState("codex")
        fs.parse([json.dumps({"type": "turn_context", "payload": {}}), codex_count("2026-10-06T16:00:00Z", 1, 0, 1)])
        self.assertIn("partial", fs.problem())

    def test_no_records_and_format_change(self):
        fs = transcripts.FileState("claude")
        fs.parse([json.dumps({"type": "user"})] * 3)
        self.assertEqual(fs.problem(), "no usage records")
        fs = transcripts.FileState("claude")
        fs.parse([claude("m1", "2026-10-06T16:00:00Z")] + [json.dumps({"type": "assistant2"})] * 200)
        self.assertEqual(fs.problem(), "format changed: 200 lines without usage records")

    def test_files_for_finds_both_layouts(self):
        # CLAUDE_CONFIG_DIR and CODEX_HOME patched to temp folders; create
        #   projects/-a/S1.jsonl, projects/-b/S1/subagents/agent-1.jsonl, projects/-a/OTHER.jsonl,
        #   sessions/2026/10/06/rollout-2026-10-06T12-00-00-C1.jsonl
        self.assertEqual(len(transcripts.files_for("claude", "S1")), 2)
        self.assertEqual(len(transcripts.files_for("codex", "C1")), 1)
        self.assertEqual(transcripts.files_for("codex", "missing"), [])
```

- [ ] **Step 2: Run them to see them fail.** `python3.11 -m unittest tests.test_transcripts -v`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `relaylib/transcripts.py` (the parsing half):

```python
"""Token usage from the CLIs' own session logs (writer-usage D1, D3, D6). Reads timestamps, models, usage
numbers, Claude message ids and git branches; never message text."""
import glob
import json
import os

from .usage import timestamp

FORMAT_LINES = 200


def claude_projects():
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")


def codex_sessions():
    return os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions")


def files_for(provider, session):
    if provider == "claude":
        root = claude_projects()
        found = glob.glob(os.path.join(root, "*", glob.escape(session) + ".jsonl"))
        found += glob.glob(os.path.join(root, "*", glob.escape(session), "subagents", "*.jsonl"))
    elif provider == "codex":
        found = glob.glob(os.path.join(codex_sessions(), "*", "*", "*", f"rollout-*-{glob.escape(session)}.jsonl"))
    else:
        found = []
    return sorted(found)


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class FileState:
    def __init__(self, provider):
        self.provider, self.turns = provider, {}
        self.candidates = self.bad = self.malformed = self.lines = self.lines_since_record = self.events = 0
        self.had_valid, self.model, self.last_total = False, None, None

    def parse(self, lines):
        for line in lines:
            self.lines += 1
            self.lines_since_record += 1
            try:
                entry = json.loads(line)
            except ValueError:
                self.malformed += 1
                continue
            if isinstance(entry, dict):
                (self._claude if self.provider == "claude" else self._codex)(entry)

    def _ok(self):
        self.had_valid, self.lines_since_record = True, 0

    def _claude(self, e):
        m = e.get("message")
        if e.get("type") != "assistant" or not isinstance(m, dict) or "usage" not in m:
            return
        if not m.get("id") or m.get("model") == "<synthetic>":
            return
        self.candidates += 1
        u = m["usage"]
        try:
            fields = [u[k] for k in ("input_tokens", "cache_read_input_tokens",
                                     "cache_creation_input_tokens", "output_tokens")]
            if not all(_count(v) for v in fields) or not m.get("model"):
                raise ValueError("bad usage")
            at = timestamp(e["timestamp"])
        except (ValueError, KeyError, TypeError, AttributeError):
            self.bad += 1
            return
        inp, cread, cwrite, out = fields
        self.turns[m["id"]] = {"at": at, "model": m["model"], "input": inp + cread + cwrite, "cached": cread,
                               "output": out, "branch": e.get("gitBranch") or None}
        self._ok()

    def _codex(self, e):
        p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        if e.get("type") == "turn_context":
            self.candidates += 1
            if isinstance(p.get("model"), str) and p["model"]:
                self.model = p["model"]
                self._ok()
            else:
                self.bad += 1
            return
        if p.get("type") != "token_count" or p.get("info") is None:
            return
        self.candidates += 1
        try:
            t = p["info"]["total_token_usage"]
            total = [t["input_tokens"], t["cached_input_tokens"], t["output_tokens"]]
            if not all(_count(v) for v in total):
                raise ValueError("bad usage")
            at = timestamp(e["timestamp"])
        except (ValueError, KeyError, TypeError):
            self.bad += 1
            return
        self._ok()
        base = self.last_total if self.last_total and all(a >= b for a, b in zip(total, self.last_total)) \
            else [0, 0, 0]
        delta = [a - b for a, b in zip(total, base)]
        self.last_total = total
        if not any(delta):
            return
        self.events += 1
        self.turns[f"codex:{self.events}"] = {"at": at, "model": self.model or "unknown", "input": delta[0],
                                              "cached": delta[1], "output": delta[2], "branch": None}

    def problem(self):
        if self.lines and not self.had_valid:
            return "no usage records"
        found = []
        if self.bad:
            found.append(f"partial: {self.bad} of {self.candidates} usage records unreadable")
        if self.malformed:
            found.append(f"{self.malformed} lines are not JSON")
        if self.lines_since_record >= FORMAT_LINES:
            found.append(f"format changed: {FORMAT_LINES} lines without usage records")
        return "; ".join(found) or None

    def to_json(self):
        return {k: getattr(self, k) for k in ("provider", "turns", "candidates", "bad", "malformed", "lines",
                                               "lines_since_record", "events", "had_valid", "model", "last_total")}

    @classmethod
    def from_json(cls, d):
        """Raises KeyError or TypeError for a state that is not one this class wrote (F3)."""
        fs = cls(d["provider"])
        for k in fs.to_json():
            setattr(fs, k, d[k])
        if not isinstance(fs.turns, dict):
            raise TypeError("turns must be a dict")
        return fs
```

- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat: read token usage from Claude Code and Codex session logs"`

---

### Task 3: Incremental reads, the cache and its lock

**Files:**
- Modify: `relaylib/transcripts.py`
- Test: `tests/test_transcripts.py`

**Interfaces:**
- Consumes: Task 1 `config.write_lock(name=...)`, `config.atomic_write`; Task 2 `FileState`, `files_for`.
- Produces: `transcripts.read_sessions(sessions: set[tuple[str, str]]) -> dict` with keys `turns` (`{(provider, session): list[turn]}`, all files of a session merged, sorted by `at`), `unreadable` (`{(provider, session): reason}`), `notes` (list of str). `CACHE = "writer-usage.json"`, `LOCK = "writer-usage.lock"`, `CHUNK = 1 << 20`.
- Cache shape (`VERSION = 1`): `{"version", "files": {path: {"inode", "size", "mtime_ns", "offset", "state"}}, "sessions": {"<provider>:<session>": [path, ...]}}`. `sessions` remembers every file ever seen for a session, so a file that disappears keeps its turns and stays reported. Each file entry is validated when used (`inode`, `size`, `mtime_ns`, `offset` integers; `state` loads through `FileState.from_json`); an invalid entry is dropped and its file read again from the start (F3).

- [ ] **Step 1: Write the failing tests:**

```python
class IncrementalTest(unittest.TestCase):
    # setUp: temp RELAY_HOME, CLAUDE_CONFIG_DIR, CODEX_HOME; self.path = projects/-a/S1.jsonl with two turns

    def test_second_refresh_parses_nothing_new_and_appends_once(self):
        first = transcripts.read_sessions({("claude", "S1")})
        self.assertEqual(len(first["turns"][("claude", "S1")]), 2)
        with mock.patch.object(transcripts.FileState, "parse", wraps=None) as parse:
            transcripts.read_sessions({("claude", "S1")})
        parse.assert_not_called()                                        # unchanged file: no parsing
        with open(self.path, "a") as f:
            f.write(claude("m3", "2026-10-06T16:05:00Z") + "\n" + claude("m4", "2026-10-06T16:06:00Z")[:30])
        got = transcripts.read_sessions({("claude", "S1")})["turns"][("claude", "S1")]
        self.assertEqual(len(got), 3)                                    # the partial last line waits
        with open(self.path, "a") as f:
            f.write(claude("m4", "2026-10-06T16:06:00Z")[30:] + "\n")
        self.assertEqual(len(transcripts.read_sessions({("claude", "S1")})["turns"][("claude", "S1")]), 4)

    def test_replaced_or_shrunk_file_is_read_again(self):
        transcripts.read_sessions({("claude", "S1")})
        with open(self.path, "w") as f:
            f.write(claude("z1", "2026-10-06T17:00:00Z") + "\n")
        got = transcripts.read_sessions({("claude", "S1")})["turns"][("claude", "S1")]
        self.assertEqual([t["at"] for t in got], [timestamp("2026-10-06T17:00:00Z")])

    def test_cache_holds_no_message_text_and_is_private(self):
        transcripts.read_sessions({("claude", "S1")})
        cache = os.path.join(os.environ["RELAY_HOME"], "writer-usage.json")
        self.assertNotIn("SECRET TEXT", open(cache).read())
        self.assertEqual(oct(os.stat(cache).st_mode & 0o777), "0o600")

    def test_corrupt_cache_is_rebuilt(self):
        cache = os.path.join(os.environ["RELAY_HOME"], "writer-usage.json")
        for text in ("{oops", json.dumps({"version": 1, "files": {self.path: {"inode": "x"}}, "sessions": {}}),
                     json.dumps({"version": 1, "files": {self.path: {"inode": 1, "size": 1, "mtime_ns": 1,
                                 "offset": 0, "state": {"provider": "claude", "turns": []}}}, "sessions": {}})):
            with self.subTest(text=text[:20]):
                helpers.write(cache, text)
                self.assertEqual(len(transcripts.read_sessions({("claude", "S1")})["turns"][("claude", "S1")]), 2)

    def test_large_file_is_read_in_bounded_chunks(self):
        with open(self.path, "a") as f:
            for n in range(3000):
                f.write(claude(f"big{n}", "2026-10-06T16:10:00Z", text="x" * 1000) + "\n")
        real = transcripts.FileState.parse
        with mock.patch.object(transcripts, "CHUNK", 64 * 1024), \
                mock.patch.object(transcripts.FileState, "parse", autospec=True, side_effect=real) as parse:
            got = transcripts.read_sessions({("claude", "S1")})["turns"][("claude", "S1")]
        self.assertEqual(len(got), 3002)
        self.assertGreater(parse.call_count, 10)                                  # many bounded reads
        self.assertTrue(all(len(c.args[1]) < 200 for c in parse.call_args_list))  # never the whole file at once

    def test_inode_replacement_is_read_again(self):
        transcripts.read_sessions({("claude", "S1")})
        os.rename(self.path, self.path + ".old")
        helpers.write(self.path, "".join(claude(f"n{i}", f"2026-10-06T18:0{i}:00Z") + "\n" for i in range(3)))
        got = transcripts.read_sessions({("claude", "S1")})["turns"][("claude", "S1")]
        self.assertEqual([t["at"] for t in got][0], timestamp("2026-10-06T18:00:00Z"))
        self.assertEqual(len(got), 3)                                             # the old file's turns are gone

    def test_a_vanished_subagent_file_keeps_its_turns_and_is_reported(self):
        sub = os.path.join(os.path.dirname(self.path), "S1", "subagents", "agent-1.jsonl")
        helpers.write(sub, claude("s1", "2026-10-06T16:30:00Z") + "\n")
        self.assertEqual(len(transcripts.read_sessions({("claude", "S1")})["turns"][("claude", "S1")]), 3)
        os.unlink(sub)
        for _ in range(2):                                                      # stays reported on later refreshes
            got = transcripts.read_sessions({("claude", "S1")})
            self.assertEqual(len(got["turns"][("claude", "S1")]), 3)
            self.assertIn("log not found", got["unreadable"][("claude", "S1")])

    def test_missing_log_and_partial_subagent_are_reported(self):
        # subagent file for S1 with one bad usage record
        got = transcripts.read_sessions({("claude", "S1"), ("codex", "gone")})
        self.assertEqual(got["unreadable"][("codex", "gone")], "log not found")
        self.assertIn("partial", got["unreadable"][("claude", "S1")])
        self.assertEqual(len(got["turns"][("claude", "S1")]), 3)            # what was read is still counted

    def test_unwritable_lock_or_cache_uses_memory_and_says_so(self):
        for target in ("write_lock", "atomic_write"):
            with self.subTest(target=target), mock.patch.object(config, target, side_effect=OSError("read-only")):
                got = transcripts.read_sessions({("claude", "S1")})
            self.assertEqual(len(got["turns"][("claude", "S1")]), 2)
            self.assertIn("usage cache not saved", got["notes"])

    def test_busy_lock_uses_memory_and_says_so(self):
        import fcntl
        with mock.patch.object(config, "LOCK_TIMEOUT_S", 0.05), \
                open(os.path.join(os.environ["RELAY_HOME"], "writer-usage.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            got = transcripts.read_sessions({("claude", "S1")})
        self.assertEqual(len(got["turns"][("claude", "S1")]), 2)
        self.assertIn("usage cache not saved", got["notes"])
```

- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** in `transcripts.py` (add imports `from . import config`, `from .errors import RelayError`):

```python
CACHE, LOCK = "writer-usage.json", "writer-usage.lock"
VERSION, CHUNK = 1, 1 << 20


def _valid_entry(entry, provider):
    if not isinstance(entry, dict) or not all(isinstance(entry.get(k), int) for k in ("inode", "size", "mtime_ns", "offset")):
        raise ValueError("bad cache entry")
    if FileState.from_json(entry["state"]).provider != provider:
        raise ValueError("bad cache entry")
    return entry


def _load_cache():
    try:
        with open(os.path.join(config.relay_home(), CACHE)) as f:
            data = json.load(f)
        if (data.get("version") == VERSION and isinstance(data.get("files"), dict)
                and isinstance(data.get("sessions"), dict)):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"version": VERSION, "files": {}, "sessions": {}}


def _usable(entry, provider):
    try:
        return _valid_entry(entry, provider) if entry else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None                                   # F3: a broken entry is read again from the start


def _advance(path, entry, provider):
    """Parse the bytes after entry's offset in CHUNK-sized reads (F6); a shrunk or replaced file starts over."""
    st = os.stat(path)
    entry = _usable(entry, provider)
    if not entry or entry["inode"] != st.st_ino or st.st_size < entry["offset"]:
        entry = {"inode": st.st_ino, "size": 0, "mtime_ns": 0, "offset": 0, "state": FileState(provider).to_json()}
    fs, offset = FileState.from_json(entry["state"]), entry["offset"]
    if st.st_size > offset:
        with open(path, "rb") as f:
            f.seek(offset)
            carry, left = b"", st.st_size - offset    # read only up to the size seen by stat
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                data = carry + chunk
                end = data.rfind(b"\n") + 1          # a partial last line waits for the next chunk or refresh
                if end:
                    fs.parse(data[:end].decode("utf-8", "replace").splitlines())
                    offset += end
                carry = data[end:]
    return {"inode": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "offset": offset,
            "state": fs.to_json()}


def read_sessions(sessions):
    out = {"turns": {}, "unreadable": {}, "notes": []}
    lock = None
    try:
        lock = config.write_lock(name=LOCK)
        lock.__enter__()
    except (RelayError, OSError):                     # busy, or relay home not writable: use memory (D5)
        lock = None
        out["notes"].append("usage cache not saved")
    try:
        cache = _load_cache()
        for provider, session in sorted(sessions):
            key = f"{provider}:{session}"
            known = sorted(set(cache["sessions"].get(key) or []) | set(files_for(provider, session)))
            cache["sessions"][key] = known
            if not known:
                out["unreadable"][(provider, session)] = "log not found"
                continue
            turns, problems = [], []
            for path in known:
                try:
                    cache["files"][path] = _advance(path, cache["files"].get(path), provider)
                except OSError:
                    problems.append("log not found")          # gone now: its cached turns stay counted
                entry = _usable(cache["files"].get(path), provider)
                if entry:
                    fs = FileState.from_json(entry["state"])
                    turns += fs.turns.values()
                    if fs.problem():
                        problems.append(fs.problem())
            out["turns"][(provider, session)] = sorted(turns, key=lambda t: t["at"])
            if problems:
                out["unreadable"][(provider, session)] = "; ".join(sorted(set(problems)))
        if lock:
            try:
                config.atomic_write(os.path.join(config.relay_home(), CACHE), json.dumps(cache), 0o600)
            except OSError:
                out["notes"].append("usage cache not saved")
    finally:
        if lock:
            lock.__exit__(None, None, None)
    return out
```

(In the busy-lock test, `_load_cache` still reads the last saved cache, so only the unsaved part is re-parsed. A `mock.patch` of `config.write_lock` raising `OSError` reaches the same fallback.)

- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat: read session logs incrementally with a private cache"`

---

### Task 4: Hold windows from state history

**Files:**
- Create: `relaylib/holds.py`
- Test: `tests/test_holds.py`

**Interfaces:**
- Consumes: Task 1 `merged.merged_at`; `status.checkouts`, `gitops.remote_branches`, `gitops.ls_files`, `gitops.show`, `gitops.git`, `gitops.origin_url`, `state.parse_state`, `state.RELAY_DIR`.
- Produces: `holds.windows(checkouts: list[str]) -> dict` (with a per-commit state cache in `~/.relay/state-history.json`, mode 0600, keyed `<commit sha>:<path>` holding only `stage`, `status`, `branch`, `pr` and `owner`; commits never change, so a cached state is never read from git again, D5) with `windows` (list of `{"repo", "url", "slug", "branch", "provider", "session", "start", "end", "stages": [[time, stage], ...], "last_commit"}`, `end` None while open) and `flags` (list of str: "merge time unknown for <repo> <slug>").

- [ ] **Step 1: Write the failing tests.** Use `helpers.make_repo`; write states with `state.new_state` / `state.write_state`, commit with a fixed `GIT_COMMITTER_DATE` and `GIT_AUTHOR_DATE` through `mock.patch.dict(os.environ, ...)`, and push. A helper `commit(st, subject, at)` keeps each test short.

```python
    def test_take_moves_the_window_and_stage_follows_commits(self):
        st = state.new_state("a", "repo", {"provider": "claude", "session": "S1", "since": iso(T0)}, "feat/a")
        commit(st, "relay: new a", T0)
        st.update(stage="spec"); commit(st, "relay: submit idea", T0 + 100)
        st["owner"] = {"provider": "codex", "session": "C1", "since": iso(T0 + 200)}
        commit(st, "relay: codex takes a", T0 + 250)
        w = {x["session"]: x for x in holds.windows([self.work])["windows"]}
        self.assertEqual((w["S1"]["start"], w["S1"]["end"]), (T0, T0 + 250))
        self.assertEqual(w["S1"]["stages"], [[T0, "idea"], [T0 + 100, "spec"]])
        self.assertEqual((w["C1"]["start"], w["C1"]["end"]), (T0 + 200, None))   # since, not commit time

    def test_handoff_on_one_feature_closes_the_sessions_other_windows(self):
        # S1 holds a (feat/a) and b (feat/b); commit "relay: handoff a" on feat/a at T0 + 300
        ends = {x["slug"]: x["end"] for x in holds.windows([self.work])["windows"] if x["session"] == "S1"}
        self.assertEqual(ends, {"a": T0 + 300, "b": T0 + 300})

    def test_merge_ends_the_window_even_though_state_says_ready(self):
        # state build / ready-to-merge, pr 7; merge feat/a into develop with a merge commit at T0 + 500, push,
        # delete origin/feat/a; gh fails, so the merge-commit fallback is used
        (w,) = holds.windows([self.work])["windows"]
        self.assertEqual(w["end"], T0 + 500)

    def test_unknown_merge_time_closes_at_the_last_state_commit_and_flags(self):
        # PR merged per merged.is_done (cache says done) but no merge commit and gh fails
        out = holds.windows([self.work])
        self.assertEqual(out["windows"][0]["end"], out["windows"][0]["last_commit"])
        self.assertIn("merge time unknown", out["flags"][0])

    def test_merged_without_a_pr_number_or_gh_uses_origin_history(self):
        # state still build / ready-to-merge with no pr; feat/a merged into develop at T0 + 500 and deleted
        (w,) = holds.windows([self.work])["windows"]
        self.assertEqual(w["end"], T0 + 500)        # found only on origin/develop, so merged; fallback reachable

    def test_merge_never_extends_an_earlier_end(self):
        # S1's window ended by a handoff at T0 + 300; the PR merged at T0 + 500
        (w,) = [x for x in holds.windows([self.work])["windows"] if x["session"] == "S1"]
        self.assertEqual(w["end"], T0 + 300)

    def test_state_history_is_cached_by_commit(self):
        holds.windows([self.work])
        with mock.patch.object(gitops, "show", wraps=gitops.show) as show:
            holds.windows([self.work])
        history_reads = [c for c in show.call_args_list if not c.args[1].startswith("origin/")]
        self.assertEqual(history_reads, [])           # discovery reads refs; no historical commit is read again

    def test_one_unreadable_repo_does_not_hide_a_healthy_one(self):            # F5
        broken = os.path.join(self.tmp, "broken")
        os.makedirs(os.path.join(broken, ".git"))
        self.assertEqual(len(holds.windows([broken, self.work])["windows"]), 1)

    def test_done_stage_closes_and_worktrees_count_once(self):
        # a second worktree of the same repo is passed too; stage set to done at T0 + 400
        ws = holds.windows([self.work, self.worktree])["windows"]
        self.assertEqual(len(ws), 1)
        self.assertEqual(ws[0]["end"], T0 + 400)
```

- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** `relaylib/holds.py`:

```python
"""Who held which feature when (writer-usage D4, D9): windows from each feature's published state.md history."""
import datetime
import os

from . import gitops, merged, state
from .errors import RelayError


def _since(owner, fallback):
    try:
        return datetime.datetime.fromisoformat(owner.get("since") or "").timestamp()
    except (TypeError, ValueError):
        return fallback


def _features(repo):
    """{slug: ref} with the feature's own origin branch when it exists, else origin/develop, else origin/main."""
    found = {}
    for ref in gitops.remote_branches(repo):
        for path in gitops.ls_files(repo, ref, state.RELAY_DIR):
            parts = path.split("/")
            if len(parts) == 4 and parts[3].lower() == "state.md":
                found.setdefault(parts[2], set()).add(ref)
    out = {}
    for slug, refs in found.items():
        try:
            st = state.parse_state(gitops.show(repo, sorted(refs)[0], f"{state.RELAY_DIR}/{slug}/state.md") or "", slug)
        except RelayError:
            continue
        own = f"origin/{st.get('branch')}"
        out[slug] = own if own in refs else next((r for r in ("origin/develop", "origin/main") if r in refs), None)
    return {s: r for s, r in out.items() if r}


def _history(repo, ref, slug):
    """[(commit time, subject, state)] oldest first."""
    path = f"{state.RELAY_DIR}/{slug}/state.md"
    log = gitops.git(repo, "log", "--reverse", "--format=%H%x09%ct%x09%s", ref, "--", path).stdout.splitlines()
    rows = []
    for line in log:
        sha, at, subject = line.split("\t", 2)
        try:
            rows.append((float(at), subject, state.parse_state(gitops.show(repo, sha, path) or "", path)))
        except RelayError:
            continue
    return rows, (log[-1].split("\t", 1)[0] if log else None)
```

and `windows(checkouts)`: group checkouts by `gitops.origin_url` (first checkout per URL); for each feature, walk `_history`; open a window when `owner.session` changes to a new non-empty session (`start = _since(owner, commit_time)`), append `[commit_time, stage]` to its `stages` with the real commit time (never moved to `start`: `_stage` in Task 5 uses the first entry's stage for a turn before the first commit, and the D10 tie-break compares real commit times), close it at the next owner change (`end = commit_time`) or when `stage == "done"` or `status == "done"`. Record each `relay: handoff <slug>` commit as `(repo url, session, time)`; after all features of a repo are walked, close every window of that session in that repo that is open at that time (`end = min(end or inf, handoff_time)` for windows with `start < handoff_time`). A feature counts as merged when `merged.is_done(repo, st)` is true, or when its own branch `origin/<branch>` no longer exists and its state is found on origin/develop or origin/main (merging deletes the branch, so this works without a PR number or gh). For a merged feature, `merge = merged.merged_at(repo, st, branch_head=<head of origin/<branch> when it exists, else the last state commit sha>)`; when that is None, `merge = last_commit` and add the flag `f"merge time unknown for {repo_name} {slug}"`. Every window of the feature then ends at `min(its end, merge)` (an open window gets `merge`), so a merge never extends an earlier handoff or owner change. `_history` reads each `(sha, path)` state from the cache before calling `gitops.show`, and the new ones are written with `config.atomic_write(..., 0o600)` at the end of `windows`; a corrupt cache file is ignored and rebuilt. Errors from git for one repo (RelayError, OSError) skip that repo (F5).

- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat: hold windows from each feature's published state history"`

---

### Task 5: Attribution, minutes and period totals

**Files:**
- Create: `relaylib/writerusage.py`
- Test: `tests/test_writerusage.py`

**Interfaces:**
- Consumes: Task 3 `transcripts.read_sessions`; Task 4 `holds.windows`.
- Produces: `writerusage.attribute(windows, turns_by_session) -> list[dict]` (each turn plus `provider`, `session`, `repo`, `slug`, `stage` or `None` for unattributed, and `minutes`); `writerusage.totals(attributed, since_s, now) -> dict` with `features`, `models` and `feature_models` (one row per feature, stage and model, named `<feature name> · <provider:model>`, for `relay cost`, R11) (rows `{"name", "sessions", "turns", "input", "cached", "output", "cached_share", "minutes"}` sorted by name), `unattributed` (`{provider: {turns, input, cached, output, minutes}}`); `writerusage.summary(checkouts, now=None) -> dict` with `"7"` and `"30"` totals, `unreadable` (list of `{provider, session, reason}`), `notes`.

- [ ] **Step 1: Write the failing tests** (pure functions on hand-built windows and turns, plus one `summary` test with patched `holds.windows` and `transcripts.read_sessions`):

```python
W = lambda slug, start, end, stages, branch="feat/" : {"repo": "r", "url": "u", "slug": slug, "branch": branch + slug,
     "provider": "claude", "session": "S1", "start": start, "end": end, "stages": stages, "last_commit": start}
turn = lambda at, branch=None, model="claude-opus-5-5": {"at": at, "model": model, "input": 10, "cached": 4,
                                                         "output": 2, "branch": branch}

    def test_one_turn_one_feature(self):
        ws = [W("a", 0, None, [[0, "spec"], [50, "plan"]]), W("b", 10, None, [[10, "build"]])]
        got = writerusage.attribute(ws, {("claude", "S1"): [turn(5), turn(60, "feat/b"), turn(60), turn(70, "feat/x")]})
        self.assertEqual([(g["slug"], g["stage"]) for g in got],
                         [("a", "spec"), ("b", "build"), ("a", "plan"), ("a", "plan")])
        # turn(60) without a branch: a's latest state commit (50) is newer than b's (10)

    def test_equal_commit_times_break_ties_by_repo_and_slug(self):
        ws = [W("b", 0, None, [[0, "spec"]]), W("a", 0, None, [[0, "build"]])]
        (got,) = writerusage.attribute(ws, {("claude", "S1"): [turn(5)]})
        self.assertEqual(got["slug"], "a")

    def test_outside_windows_is_unattributed_and_minutes_are_capped(self):
        ws = [W("a", 0, 100, [[0, "spec"]])]
        got = writerusage.attribute(ws, {("claude", "S1"): [turn(10), turn(70), turn(1000), turn(1010)]})
        self.assertEqual([g["slug"] for g in got], ["a", "a", None, None])
        self.assertEqual([g["minutes"] for g in got], [0, 1.0, 5.0, 10 / 60])     # 60 s, capped 300 s, 10 s

    def test_totals_by_feature_and_model_within_the_period(self):
        attributed = [dict(turn(t), provider="claude", session=s, repo="r", slug="a", stage="spec", minutes=1)
                      for t, s in ((100, "S1"), (200, "S2"), (-10 * 86400, "S1"))]
        got = writerusage.totals(attributed, 7 * 86400, now=300)
        (row,) = got["features"]
        self.assertEqual((row["name"], row["sessions"], row["turns"], row["input"], row["cached_share"]),
                         ("r · a · spec (claude)", 2, 2, 20, 40.0))
        self.assertEqual(got["models"][0]["name"], "claude:claude-opus-5-5")

    def test_two_models_on_one_feature_and_stage_split_in_feature_models(self):
        attributed = [dict(turn(100, model=m), provider="claude", session="S1", repo="r", slug="a", stage="build",
                           minutes=1) for m in ("claude-opus-5-5", "claude-fable-5-1")]
        got = writerusage.totals(attributed, 86400, now=300)
        self.assertEqual(len(got["features"]), 1)
        self.assertEqual([r["name"] for r in got["feature_models"]],
                         ["r · a · build (claude) · claude:claude-fable-5-1",
                          "r · a · build (claude) · claude:claude-opus-5-5"])

    def test_summary_reports_unreadable_and_skips_owner_sessions(self):
        # patched holds.windows: one claude window S1, one owner window owner@host
        # patched transcripts.read_sessions returns unreadable {("claude","S1"): "log not found"}
        got = writerusage.summary(["/repo"], now=1000)
        self.assertEqual(got["unreadable"], [{"provider": "claude", "session": "S1", "reason": "log not found"}])
        read.assert_called_once_with({("claude", "S1")})                  # owner sessions are never looked up
```

- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** `relaylib/writerusage.py`:

```python
"""Writing-session usage per feature, stage and model (writer-usage D2, D7, D10, R9)."""
import time

from . import holds, transcripts

GAP_CAP_S = 300
PERIODS = (7, 30)


def _stage(window, at):
    stage = window["stages"][0][1]
    for when, name in window["stages"]:
        if when <= at:
            stage = name
    return stage


def _latest_commit(window, at):
    return max((when for when, _ in window["stages"] if when <= at), default=float("-inf"))


def attribute(windows, turns_by_session):
    out = []
    for (provider, session), turns in turns_by_session.items():
        mine = [w for w in windows if w["provider"] == provider and w["session"] == session]
        previous = None
        for t in sorted(turns, key=lambda t: t["at"]):
            gap = 0 if previous is None else min(GAP_CAP_S, max(0, t["at"] - previous))
            previous = t["at"]
            open_ = [w for w in mine if w["start"] <= t["at"] and (w["end"] is None or t["at"] < w["end"])]
            match = [w for w in open_ if t.get("branch") and w["branch"] == t["branch"]]
            pick = (match or sorted(open_, key=lambda w: (-_latest_commit(w, t["at"]), w["repo"], w["slug"])))[:1]
            w = pick[0] if pick else None
            out.append({**t, "provider": provider, "session": session, "minutes": gap / 60,
                        "repo": w and w["repo"], "slug": w and w["slug"], "stage": w and _stage(w, t["at"])})
    return out


def _add(group, t):
    group["turns"] += 1
    group["sessions"].add((t["provider"], t["session"]))
    for k in ("input", "cached", "output", "minutes"):
        group[k] += t[k]


def _rows(groups):
    rows = []
    for name, g in sorted(groups.items()):
        rows.append({"name": name, "sessions": len(g["sessions"]), "turns": g["turns"], "input": g["input"],
                     "cached": g["cached"], "output": g["output"], "minutes": g["minutes"],
                     "cached_share": g["cached"] / g["input"] * 100 if g["input"] else 0})
    return rows


def totals(attributed, since_s, now):
    new = lambda: {"sessions": set(), "turns": 0, "input": 0, "cached": 0, "output": 0, "minutes": 0}
    features, models, both, loose = {}, {}, {}, {}
    for t in attributed:
        if t["at"] < now - since_s:
            continue
        if t["slug"] is None:
            _add(loose.setdefault(t["provider"], new()), t)
            continue
        feature, model = f"{t['repo']} · {t['slug']} · {t['stage']} ({t['provider']})", f"{t['provider']}:{t['model']}"
        _add(features.setdefault(feature, new()), t)
        _add(models.setdefault(model, new()), t)
        _add(both.setdefault(f"{feature} · {model}", new()), t)
    unattributed = {p: {k: v for k, v in g.items() if k != "sessions"} for p, g in loose.items()}
    return {"features": _rows(features), "models": _rows(models), "feature_models": _rows(both),
            "unattributed": unattributed}


def summary(checkouts, now=None, periods=PERIODS):
    now = time.time() if now is None else now
    held = holds.windows(checkouts)
    sessions = {(w["provider"], w["session"]) for w in held["windows"] if w["provider"] in ("claude", "codex")}
    read = transcripts.read_sessions(sessions)
    attributed = attribute(held["windows"], read["turns"])
    out = {str(days): totals(attributed, days * 86400, now) for days in periods}
    out["unreadable"] = [{"provider": p, "session": s, "reason": r} for (p, s), r in sorted(read["unreadable"].items())]
    out["notes"] = held["flags"] + read["notes"]
    return out
```

- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat: attribute writing-session turns to one feature and stage"`

---

### Task 6: Snapshot and the Models tab tables

**Files:**
- Modify: `relaylib/ui/snapshot.py` (`build`)
- Modify: `relaylib/ui/page.html`
- Test: `tests/test_ui_snapshot.py`, `tests/test_ui_server.py`

**Interfaces:**
- Consumes: Task 5 `writerusage.summary`.
- Produces: snapshot key `writing` (the summary, or `{"error": str}`); page functions `activityTable(kind, title, rows, cols, empty)` and `renderWriting()`.

- [ ] **Step 1: Write the failing tests.** Snapshot: with `writerusage.summary` patched to return a small dict, `build()["writing"]` equals it; with it raising `OSError("x")`, `build()["writing"] == {"error": "x"}` and the rest of the snapshot is intact. Page strings: `id="writing-tables"`, `'Writing sessions by feature'`, `'Writing sessions by model'`, `'No writing sessions in this period.'`, `"Unattributed"`, `"Unreadable"`, `"renderWriting()"`, `"activityTable("`, `"activityTable('features'"` and `"activityTable('wmodels'"` (the writing tables go through the shared, sorted renderer), and the existing `test_sort_rules_run_in_node` keeps passing (sortLedger and nextSort unchanged). The writing section comes after `id="ledger-tables"` and before `id="reviewers"`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.** In `build()` add `"writing": _writing_or_error(repos, errors)` where `_writing_or_error` calls `writerusage.summary(repos)`, appends `f"fetch failed for {os.path.basename(repo)}: writing sessions use the last fetched history"` to its `notes` for each repo in `errors` (the fetch failures `build` already collects; R8a), and returns `{"error": str(e)}` on `(RelayError, OSError, ValueError, KeyError, TypeError)`. Add a snapshot test with a failed fetch that asserts that note. In the page:
  - After `<div id="ledger-tables"></div>` add `<h3>Writing sessions</h3><p class="muted">Claude Code and Codex sessions while they held a relay feature, from their own logs on this machine. Work outside relay in those sessions counts toward the feature they held.</p><div id="writing-notes" class="muted"></div><div id="writing-tables"></div>`.
  - Refactor `renderLedger` so its table-building body becomes `function activityTable(kind,title,rows,cols,empty)` returning the `h3` and `table-wrap` nodes (sorting state in `ledgerSort[kind]`, focus restore unchanged), and `renderLedger` calls it for `repos` and `models` with today's columns and `'No reviewer runs in this period.'`.
  - `ledgerSort` gains `features:{key:'name',dir:'ascending'}` and `wmodels:{key:'name',dir:'ascending'}`.
  - `renderWriting()` reads `data.writing?.[$('ledger-period').value]`, builds both tables with columns `name` (Feature / Model), `sessions` Sessions, `turns` Turns, `input`, `cached_share`, `output`, `minutes`, empty text `'No writing sessions in this period.'`, and fills `#writing-notes` with: `data.writing.error` when present; one line per provider in `unattributed` ("Unattributed claude: N turns, X input tokens, Y minutes"); one line per `unreadable` entry ("Unreadable: claude session <first 8 chars>: <reason>"); and each of `notes`. It skips rebuilding when its JSON is unchanged, like `ledgerShown`.
  - Call `renderWriting()` after `renderLedger()` wherever `renderLedger()` is called (load and the period select).
- [ ] **Step 4: Run the tests and look at the page.** `python3.11 -m unittest tests.test_ui_snapshot tests.test_ui_server -v`. Then start a throwaway server as in models-tab (temp `RELAY_HOME`, Runtime with a builder returning fixture `writing` data) and check the tables, sorting and notes in a browser.
- [ ] **Step 5: Commit.** `git commit -m "feat: writing sessions on the Models tab"`

---

### Task 7: `relay cost` writing section

**Files:**
- Modify: `relaylib/commands.py` (`cmd_cost`)
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: Task 5 `writerusage.summary(checkouts, periods=...)`; `status.checkouts(status.projects_root())`; `ledger.parse_since`.

- [ ] **Step 1: Write the failing test.** Patch `writerusage.summary` to return two `feature_models` rows (one feature and stage, two models; both names printed), one unattributed provider and one unreadable entry; `relay cost --since 12h` prints the review header (or "no reviewer runs"), then `WRITING`, a row with the feature name and numbers, `unattributed claude`, and `unreadable claude session`. Assert `summary` was called with `periods=(0.5,)` for `12h`.
- [ ] **Step 2: Run it to see it fail.**
- [ ] **Step 3: Implement.** Compute `days = ledger.parse_since(args.since).total_seconds() / 86400`. Keep the review section; when there are no reviewer runs, print the existing message and continue instead of returning. Then:

```python
    from . import status, writerusage
    writing = writerusage.summary(status.checkouts(status.projects_root()), periods=(days,))
    rows = writing[str(days)]
    print(f"\nWRITING {'FEATURE AND MODEL':64} {'SESS':>4} {'TURNS':>5} {'INPUT':>10} {'CACHED':>10} {'OUTPUT':>8} {'MIN':>6}")
    for r in rows["feature_models"]:                   # feature, stage and model (R11)
        print(f"        {r['name'][:64]:64} {r['sessions']:>4} {r['turns']:>5} {r['input']:>10} {r['cached']:>10} "
              f"{r['output']:>8} {r['minutes']:>6.1f}")
    for provider, u in sorted(rows["unattributed"].items()):
        print(f"  unattributed {provider}: {u['turns']} turns, {u['input']} input, {u['minutes']:.1f} min")
    for u in writing["unreadable"]:
        print(f"  unreadable {u['provider']} session {u['session'][:8]}: {u['reason']}")
    for note in writing["notes"]:
        print(f"  note: {note}")
```

`relay cost` does not fetch (R8a): `holds` reads the origin refs as they are.
- [ ] **Step 4: Run the commands tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat: relay cost shows writing sessions"`

---

### Task 8: README and the full suite

**Files:**
- Modify: `README.md` (the Models paragraph and Settings list)

- [ ] **Step 1: Update README.** In the dashboard paragraph after the review-runs sentence: "Writing sessions shows the Claude Code and Codex sessions that held a relay feature, by feature and stage and by model, read from each CLI's own logs on this machine (`~/.claude/projects`, `~/.codex/sessions`). relay reads only timestamps, model names and token counts, never the conversation, and only for sessions that held a feature. Work a session does outside relay while it holds a feature counts toward that feature; work you write by hand is not counted; turns outside any hold show as unattributed, and a log relay cannot read shows as unreadable rather than zero. The log formats are not published contracts, so a CLI update can change them." Add `relay cost --since 7d` now also listing writing sessions.
- [ ] **Step 2: Check em-dashes and run the full suite.** `python3.11 -c "import sys; [print(p) for p in sys.argv[1:] if chr(0x2014) in open(p).read()]" README.md relaylib/*.py relaylib/ui/page.html docs/relay/writer-usage/*.md` prints nothing; `python3.11 -m unittest discover -s tests -t . -v` passes.
- [ ] **Step 3: Commit.** `git commit -m "docs: writing sessions in the Models tab and relay cost"`
