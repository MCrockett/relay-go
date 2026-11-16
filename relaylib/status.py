"""relay status: every feature across the projects folder and its worktrees, and what waits on the owner."""
import copy
import datetime
import glob
import json
import os
import time

from . import (agentask, cimode, config, freshness, gitops, health, localtests, merged, othersessions, ownership,
               scancache, sessions, state, waiting)
from .errors import RelayError

COLUMNS = ("repo", "feature", "stage", "status", "round", "owner", "verdict")


def projects_root():
    return os.environ.get("RELAY_ROOT") or os.path.expanduser(config.load()["projects"]["root"])


def checkouts(root_dir):
    """Every repo directly under root_dir, plus every worktree those repos list, wherever it lives. Listed once per
    snapshot build and shared by its steps (dashboard-scan R7)."""
    return list(scancache.per_build(("checkouts", os.path.realpath(root_dir)), lambda: _checkouts(root_dir)))


def _checkouts(root_dir):
    seen, out = set(), []
    for repo_dir in sorted(glob.glob(os.path.join(root_dir, "*"))):
        if not os.path.exists(os.path.join(repo_dir, ".git")):
            continue
        try:
            trees = gitops.worktrees(repo_dir)
        except RelayError:
            trees = []
        try:
            runs = os.path.realpath(os.path.join(gitops.common_dir(repo_dir), "relay", "test-runs")) + os.sep
        except RelayError:
            runs = None
        for path in [repo_dir] + trees:
            real = os.path.realpath(path)
            if runs and (real + os.sep).startswith(runs):
                continue  # a local test run's own worktree (ci-modes R14)
            if real not in seen and os.path.isdir(path):
                seen.add(real)
                out.append(path)
    return out


def tests_flag(repo_dir, st, info=None):
    """The tests flag of a build-stage feature with a PR (ci-modes R15), or None. Reads the PR's base for the mode
    (D3), so it reuses `info` when the row already fetched it. Any failure becomes an `unknown` flag."""
    if st.get("stage") != "build" or not st.get("pr"):
        return None
    try:
        info = info or gitops.pr_info(repo_dir, st["pr"])
        mode = cimode.settings(repo_dir, info["baseRefName"])
        if mode.mode == "pr":
            return "tests: GitHub CI"
        if mode.mode == "none":
            return "tests: not gated (ci = none)"
        if not mode.test_command.strip():
            return "tests: local, no test command"
        code = gitops.code_sha(repo_dir, freshness.bookkeeping_prefix(st), info["headRefOid"])
        return localtests.flag(localtests.read(repo_dir, code, mode.test_command), code)
    except (RelayError, OSError, KeyError) as e:
        return f"tests: unknown ({e})"


def _build_flags(repo_dir, st, fetched, ref=None, got=None):
    flags = []
    if repo_dir not in fetched:
        fetched[repo_dir] = None
        try:
            gitops.fetch(repo_dir)
        except RelayError as e:
            fetched[repo_dir] = str(e)
    if fetched[repo_dir]:
        return [f"stale GO: cannot confirm, fetch failed ({fetched[repo_dir]})"]
    try:
        info = gitops.pr_info(repo_dir, st["pr"])
        if got is not None:
            got["info"] = info
        base = "origin/" + info["baseRefName"]
        fresh, why = freshness.check(repo_dir, st, "build", base, ref=ref)
    except (RelayError, OSError) as e:
        fresh, why = False, str(e)
    if not fresh:
        flags.append(f"stale GO: {why}")
    return flags


def finished(repo_dir, st):
    """(done, merged, hidden): done for a done stage or a merged, closed or gone PR; hidden is the reason a
    closed or gone feature left the inbox and status (merge-and-tidy D11, D12). An unreadable PR stays."""
    if st.get("status") == "done":
        return True, False, None
    if not st.get("pr"):
        return False, False, None
    outcome, reason = merged.resolve(repo_dir, st)
    return outcome in ("merged", "closed", "gone"), outcome == "merged", reason if outcome in ("closed", "gone") else None


def _row(repo_dir, slug, st, fetched):
    d = state.feature_dir(repo_dir, slug)
    flags, status = [], st.get("status")
    handoff = os.path.join(d, "handoff.md")
    if os.path.exists(handoff):
        rel = os.path.relpath(handoff, repo_dir)
        published = (ownership.committed(repo_dir, handoff)
                     and gitops.show(repo_dir, f"origin/{st.get('branch')}", rel) is not None)
        if published:  # work has stopped until someone opens a session and takes it over: on the owner now
            age_h = (datetime.datetime.now().timestamp() - os.path.getmtime(handoff)) / 3600
            flags.append(("handoff (over 24h old)" if age_h > 24 else "handoff") + ": open a session and run `relay take`")
        else:  # still being written, or its push failed; relay take would refuse it
            flags.append("handoff drafted, not pushed yet")
    done, was_merged, hidden = finished(repo_dir, st)
    if done:
        status = "done"
    else:
        try:
            up = freshness.upstream_invalidated(repo_dir, st)
        except RelayError as e:
            up = None
            flags.append(str(e))
        if up:
            flags.append(f"stale {up} GO: re-review before going on")
        got = {}
        if status == "ready-to-merge":
            flags += _build_flags(repo_dir, st, fetched, got=got)
        tests = tests_flag(repo_dir, st, got.get("info"))
        if tests:
            flags.append(tests)
    if not done:  # fallback and same-provider reviews stay visible until the feature is merged
        for reviewed, note in (st.get("review_notes") or {}).items():
            flags.append(f"{reviewed} review: {note}")
    stale = any(f.startswith("stale") for f in flags)
    return {
        "repo": st.get("repo"), "feature": slug, "stage": "done" if done else st.get("stage"), "status": status,
        "round": (st.get("rounds") or {}).get(st.get("stage"), 0),
        "owner": (st.get("owner") or {}).get("provider"),
        "verdict": (st.get("verdicts") or {}).get(st.get("stage"), ""),
        "pr": st.get("pr"), "flags": flags, "path": d,
        "owner_actions": len(st.get("owner_actions") or []) + (1 if was_merged else 0), "hidden": hidden,
        "waiting_on_owner": (status in ("waiting-owner", "review-error")
                             or (status == "ready-to-merge" and not stale)
                             or any(f.startswith("handoff") and "take" in f for f in flags)),
        "updated": st.get("updated", ""), "checkout": repo_dir,
    }


def _remote_features(checkout):
    """(ref, slug, state, has_handoff) for every feature on an origin branch, as of the last fetch.
    Status does not fetch here: it stays fast and read-only. A snapshot build keeps the answer under the
    repository's origin commits, so a build in which no origin ref moved parses no state (dashboard-scan R2)."""
    if not scancache.active():
        return _read_remote_features(checkout)
    branches = gitops.remote_branches(checkout)
    key = ("remote_features", gitops.common_dir(checkout),
           tuple((ref, gitops.head_sha(checkout, ref)) for ref in branches))
    got = scancache.recall(key)
    if got is None:
        got = _read_remote_features(checkout)
        if not scancache.failed(checkout):  # a failed read is not kept (F2)
            scancache.remember(key, got)
    return [(ref, slug, copy.deepcopy(st), handoff) for ref, slug, st, handoff in got]


def _read_remote_features(checkout):
    out = []
    for ref in gitops.remote_branches(checkout):
        files = gitops.ls_files(checkout, ref, state.RELAY_DIR)
        lowered = {f.lower() for f in files}
        for path in files:
            parts = path.split("/")
            if len(parts) != 4 or parts[3].lower() != "state.md":
                continue
            try:
                st = state.parse_at(checkout, ref, path, f"{ref}:{path}")
            except RelayError:
                continue
            if st.get("branch") and ref != f"origin/{st['branch']}":
                continue  # inherited copy on a stacked branch; the feature's own branch is authoritative
            out.append((ref, parts[2], st, f"{'/'.join(parts[:3])}/handoff.md".lower() in lowered))
    return out


def _remote_row(checkout, ref, slug, st, has_handoff, fetched):
    """A feature that exists only on an origin branch nobody has checked out (for example, a PR waiting
    for the owner while the checkout moved on). A GO there is checked against that branch."""
    done, was_merged, hidden = finished(checkout, st)
    status = "done" if done else st.get("status")
    flags = [f"on {ref} (not checked out)"]
    if has_handoff and not done:
        flags.append("handoff: open a session and run `relay take`")
    if not done:
        got = {}
        if status == "ready-to-merge":
            flags += _build_flags(checkout, st, fetched, ref=ref, got=got)
        tests = tests_flag(checkout, st, got.get("info"))
        if tests:
            flags.append(tests)
    if not done:  # fallback and same-provider reviews stay visible until the feature is merged
        for reviewed, note in (st.get("review_notes") or {}).items():
            flags.append(f"{reviewed} review: {note}")
    stale = any(f.startswith("stale") for f in flags)
    return {
        "repo": st.get("repo"), "feature": slug, "stage": "done" if done else st.get("stage"), "status": status,
        "round": (st.get("rounds") or {}).get(st.get("stage"), 0),
        "owner": (st.get("owner") or {}).get("provider"),
        "verdict": (st.get("verdicts") or {}).get(st.get("stage"), ""),
        "pr": st.get("pr"), "flags": flags, "path": f"{ref}:{state.RELAY_DIR}/{slug}",
        "owner_actions": len(st.get("owner_actions") or []) + (1 if was_merged else 0), "hidden": hidden,
        "waiting_on_owner": not done and (status in ("waiting-owner", "review-error")
                                          or (status == "ready-to-merge" and not stale) or has_handoff),
        "updated": st.get("updated", ""), "checkout": checkout,
    }


def with_asks(row, st, ref, records, now, claims=None):
    """Adds asks, wait_since and excerpt (waiting-visibility R6); waiting_on_owner becomes "has an ask".
    claims, when given, collects the sessions this feature accounts for (other-sessions D2)."""
    found = record = None
    if st is not None and row["feature"] != "?" and row["stage"] != "done":
        try:
            found, _, record = health.session_health(row["checkout"], st, records, now)
        except Exception:  # F5: this row keeps its state asks
            found = record = None
    stuck = (waiting.stuck_reason(row["checkout"], ref, row["feature"], st.get("stage"))
             if st is not None and row["status"] == "waiting-owner" else None)
    asks = waiting.asks(row, st, found, record, stuck)
    excerpt = None
    if record and any(a["kind"] in ("answer", "approve") for a in asks):
        words = agentask.last_words(record["provider"], record["session_id"])
        if words:
            excerpt = {"source": words["source"], "text": agentask.excerpt(words["text"])}
    row.update(asks=asks, wait_since=waiting.since(asks), excerpt=excerpt, waiting_on_owner=bool(asks))
    if claims is not None and st is not None and row["stage"] != "done":
        owner = (st.get("owner") or {}).get("session")
        claims.update(x for x in (owner, (record or {}).get("session_id")) if x)
    return row


def scan(root_dir):
    return scan_with_claims(root_dir)[0]


def _error_row(checkout, error):
    name = os.path.basename(checkout)
    return {"repo": name, "feature": "?", "stage": "?", "status": "?", "round": 0,
            "owner": "", "verdict": "", "pr": None, "flags": [str(error)], "path": checkout,
            "owner_actions": 0, "waiting_on_owner": True, "updated": "", "checkout": checkout}


def scan_with_claims(root_dir, records=None, fetched=None):
    """(rows, claimed): claimed holds the session ids the listed, not-done features account for (D2). A snapshot
    build passes its session records and its fetch results, so neither is read twice."""
    rows, now, claims = {}, time.time(), {}
    fetched = {} if fetched is None else fetched
    if records is None:
        try:
            records = sessions.read_records()
        except Exception:  # F2: no hook records, no session asks from them
            records = []
    for checkout in checkouts(root_dir):
        try:
            features = state.list_features(checkout)
        except (RelayError, OSError) as e:
            name = os.path.basename(checkout)
            rows[(name, "?")] = _error_row(checkout, e)
            with_asks(rows[(name, "?")], None, None, records, now)
            continue
        try:
            here = gitops.current_branch(checkout)
        except RelayError:
            here = None
        for slug, st in features:
            if (st.get("branch") and st["branch"] != here and not merged.is_done(checkout, st)
                    and gitops.remote_ref_exists(checkout, f"origin/{st['branch']}")):
                continue  # inherited copy on a stacked branch: the remote pass checks the feature's own branch
            mine = set()
            row = with_asks(_row(checkout, slug, st, fetched), st, "HEAD", records, now, mine)
            key = (gitops.origin_url(checkout), slug)
            if key not in rows or row["updated"] > rows[key]["updated"]:
                rows[key], claims[key] = row, mine
    local, origins, broken = set(rows), set(), {}
    for checkout in checkouts(root_dir):  # then features that live only on origin branches
        url = gitops.origin_url(checkout)
        if url in origins:
            continue
        origins.add(url)
        try:
            remote = _remote_features(checkout)
            error = scancache.failed(checkout)
        except RelayError as e:
            remote, error = [], str(e)
        if error:  # dashboard-scan F2: the repository's rows say why its origin branches could not be read
            broken[url] = (checkout, f"cannot read origin branches: {error}")
        for ref, slug, st, has_handoff in remote:
            key = (url, slug)
            if key in local:  # a checked-out copy is at least as current as the last fetch
                continue
            mine = set()
            row = with_asks(_remote_row(checkout, ref, slug, st, has_handoff, fetched), st, ref, records, now, mine)
            if key not in rows or row["updated"] > rows[key]["updated"]:
                rows[key], claims[key] = row, mine
    for url, (checkout, error) in broken.items():
        mine = [row for (u, _), row in rows.items() if u == url]
        for row in mine:
            row["flags"].append(error)
            row["scan_error"] = error  # kept by the dashboard's enrichment, which rebuilds the flags
        if not mine:
            rows[(url, "?")] = with_asks(_error_row(checkout, error), None, None, records, now)
    ordered = sorted(rows.values(), key=lambda r: (not r["waiting_on_owner"], r["repo"] or "", r["feature"]))
    return ordered, set().union(*claims.values())


def mark_wins(new, old):
    """Which of two features naming one session marks it: the not-done one, then the newest (where-i-left-off D6)."""
    return old is None or (not new["done"], new["updated"]) > (not old["done"], old["updated"])


def local_marks(root_dir):
    """{session id: {slug, done}} from the feature states in the checkouts' working trees: no fetch, no network
    (where-i-left-off D6). Done is the state's own word; a PR merged on GitHub but not yet recorded is not done."""
    found = {}
    for checkout in checkouts(root_dir):
        try:
            features = state.list_features(checkout)
        except (RelayError, OSError):
            continue
        for slug, st in features:
            sid = (st.get("owner") or {}).get("session")
            if not sid:
                continue
            new = {"slug": slug, "done": "done" in (st.get("stage"), st.get("status")),
                   "updated": str(st.get("updated") or "")}
            if mark_wins(new, found.get(sid)):
                found[sid] = new
    return {sid: {"slug": m["slug"], "done": m["done"]} for sid, m in found.items()}


def _other_lines(others, now):
    lines = ["", "Other sessions waiting on you"]
    for e in others:
        held = health.age(now - e["since"])
        what = (f"needs approval: {', '.join(e['pending_tools']) or 'a tool'} · {held}" if e["state"] == "permission"
                else f"waiting {held}")
        lines.append(f"* {e['label']} · {e['provider']} · {what}")
        if e.get("excerpt"):
            label = "Summary" if e["excerpt"]["source"] == "summary" else "Agent"
            lines.append(f"    {label}: {e['excerpt']['text']}")
    return lines


def render(rows, others=()):
    now = time.time()
    if not rows:
        if not others:
            return "No relay features. Start one with `relay new <slug>` inside a repo."
        lines = ["No relay features. Start one with `relay new <slug>` inside a repo."] + _other_lines(others, now)
        lines.append(f"\n{len(others)} waiting on you (*)")
        return "\n".join(lines)
    heads = [c.upper() for c in COLUMNS] + ["FLAGS"]
    table = [[str(r[c] if r[c] is not None else "") for c in COLUMNS] + ["; ".join(r["flags"])] for r in rows]
    widths = [max(len(cell) for cell in col) for col in zip(heads, *table)]

    def fmt(cells):
        return "  ".join(cell.ljust(w) for cell, w in zip(cells, widths)).rstrip()

    lines = ["  " + fmt(heads)]
    for r, t in zip(rows, table):
        lines.append(("* " if r["waiting_on_owner"] else "  ") + fmt(t))
        if r.get("asks"):
            at = r["wait_since"]
            lines.append("    " + r["asks"][0]["text"] + (f" · waiting {health.age(now - at)}" if at is not None else ""))
            if r.get("excerpt"):
                label = "Summary" if r["excerpt"]["source"] == "summary" else "Agent"
                lines.append(f"    {label}: {r['excerpt']['text']}")
    if others:
        lines += _other_lines(others, now)
    lines.append(f"\n{sum(r['waiting_on_owner'] for r in rows) + len(others)} waiting on you (*)")
    return "\n".join(lines)


def other_sessions(root, claimed, now=None):
    """The other sessions waiting on the owner (other-sessions D1), or [] when they cannot be read (F1, F5)."""
    try:
        return othersessions.listed(sessions.read_records(), claimed, root, config.load(),
                                    time.time() if now is None else now)
    except Exception:
        return []


def cmd_status(args):
    root = projects_root()
    rows, claimed = scan_with_claims(root)
    waiting.one_ask_per_session(rows)  # here, not in the scan: the dashboard merges after its own enrichment
    hidden = [] if args.all else [r for r in rows if r.get("hidden")]
    if not args.all:
        rows = [r for r in rows if r["stage"] != "done"]
    if args.json:  # unchanged: feature rows only, oldest wait first (other-sessions D5, running-now D8)
        print(json.dumps(sorted(rows, key=waiting.oldest_first_key), indent=2))
    else:
        print(render(sorted(rows, key=waiting.sort_key), other_sessions(root, claimed)))  # newest wait first
        for r in hidden:  # why a closed or gone feature left the list (D12); named by the checkout it is in
            print(f"Hidden: {os.path.basename(r['checkout'])}/{r['feature']}: {r['hidden']}")
