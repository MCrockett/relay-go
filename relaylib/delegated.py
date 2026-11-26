"""Delegated merges (D1): the conditions under which the hordle release role may merge without the owner, the
merge commit relay pushes to the checked base, and the bounded wait for GitHub to mark the PR merged. The
conditions live here in one function (R9) so a test can drive each alone; owneractions.merge_delegated
orchestrates them."""
import datetime
import json
import os
import re

from . import cimode, config, gitops, state
from .errors import RelayError

PROTECTED_NAMES = {"agents.md", "claude.md", "workflow.md"}
PREFIX = "not delegated (D1): "
CONTROL_PLANE = {"hordle", "relay-go", "upstreampartners"}  # matched on the GitHub repository name, ignoring case


class Refused(RelayError):
    """A D1 refusal; the text already starts with PREFIX."""


def refuse(reason):
    return Refused(PREFIX + reason)


def rev(repo, ref):
    """The commit `ref` names, or a RelayError."""
    p = gitops.git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    if p.returncode or not p.stdout.strip():
        raise RelayError(f"{ref} is not known here")
    return p.stdout.strip()


def production_branches(repo, base_sha, base, name):
    """(list, None) or (None, reason). Read from the pinned base commit only, never the PR branch (R3)."""
    path = f"{state.RELAY_DIR}/config.toml"
    text = gitops.show(repo, base_sha, path)
    if text is None:
        return None, f"{name} has not opted in: no [merge] production_branches on {base}"
    data = cimode._read_toml(text, f"origin/{base}:{path}")  # a TOML error raises RelayError naming the file
    merge = data.get("merge")
    if not isinstance(merge, dict) or "production_branches" not in merge:
        return None, f"{name} has not opted in: no [merge] production_branches on {base}"
    listed = merge["production_branches"]
    if (not isinstance(listed, list) or not listed
            or not all(isinstance(b, str) and b.strip() for b in listed)):
        return None, f"[merge] production_branches on {base} must list at least one branch"
    return listed, None


def repo_name(remote):
    """The GitHub repository's name: the part after the owner in what owneractions.github_repo returns."""
    return remote.split("/", 1)[-1]


def control_plane(remote):
    return repo_name(remote).lower() in CONTROL_PLANE


def not_control_plane(remote):
    """R4: a reason, or None."""
    if control_plane(remote):
        return f"{repo_name(remote)} is control plane; the owner merges it"
    return None


def not_production(base, listed):
    """R5: a reason, or None."""
    return f"{base} is a production branch" if base in listed else None


def ci_reason(mode):
    """R7: `none` never qualifies; `pr` and `local` do (their pass is merge_readiness's business)."""
    return "ci = none never qualifies for D1" if mode.mode == "none" else None


def protected_path(repo, base_sha, pr_head):
    """R8: the first changed path the owner merges (AGENTS.md, CLAUDE.md, WORKFLOW.md anywhere, the relay config,
    anything under .github/), or None. `--no-renames` shows a rename as a delete and an add, so both sides count."""
    out = gitops.git(repo, "diff", "--name-only", "--no-renames", "-z", f"{base_sha}...{pr_head}").stdout
    for path in sorted(p for p in out.split("\0") if p):
        low = path.lower()
        if (os.path.basename(low) in PROTECTED_NAMES or low == f"{state.RELAY_DIR}/config.toml"
                or low.startswith(".github/")):
            return path
    return None


REVIEW_NAME = re.compile(r"build-(\d+)r?\.(?:claude|codex)(?:-\d+)?\.md")
NO_EVIDENCE = "no review evidence for the build GO"
NOT_IN_TABLE = "the build GO was not a review from the preference table"


def parse_front(text):
    """The JSON front matter of a review file as a dict, or None when it is missing or not an object."""
    if not isinstance(text, str) or not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end < 0:
        return None
    try:
        meta = json.loads(text[4:end])
    except ValueError:
        return None
    return meta if isinstance(meta, dict) else None


def _at(meta):
    try:
        at = datetime.datetime.fromisoformat(meta["at"])
    except (KeyError, TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=datetime.timezone.utc)


def review_evidence(repo, st, commit):
    """(meta, None) or (None, reason): the review the build GO came from, read with `git show` from the published
    commit, never the caller's checkout (R6). The file used is the `build-<round>.*.md` with the highest round;
    a tie goes to the later `at`, and a tie that stays or an `at` that does not parse is malformed."""
    folder = f"{state.RELAY_DIR}/{st['feature']}/reviews"
    found = []
    for path in gitops.ls_files(repo, commit, folder):
        match = REVIEW_NAME.fullmatch(os.path.basename(path))
        if match and os.path.dirname(path).lower() == folder.lower():
            found.append((int(match[1]), path))
    if not found:
        return None, NO_EVIDENCE
    top = max(n for n, _ in found)
    paths = [p for n, p in found if n == top]
    texts = gitops.cat_files(repo, [f"{commit}:{p}" for p in paths])
    metas = [parse_front(texts.get(f"{commit}:{p}")) for p in paths]
    if any(m is None for m in metas):
        return None, NO_EVIDENCE
    if len(metas) > 1:
        stamps = [_at(m) for m in metas]
        if any(a is None for a in stamps):
            return None, NO_EVIDENCE
        latest = max(stamps)
        if stamps.count(latest) > 1:
            return None, NO_EVIDENCE
        metas = [metas[stamps.index(latest)]]
    meta = metas[0]
    snap = (st.get("reviewed") or {}).get("build") or {}
    if (meta.get("verdict") != "GO" or not isinstance(meta.get("reviewer"), str) or not meta["reviewer"]
            or not isinstance(meta.get("model"), str) or not meta["model"]
            or not snap.get("head") or meta.get("head") != snap["head"]):
        return None, NO_EVIDENCE
    return meta, None


def table_reason(st, meta):
    """R6: a reason, or None when the review's provider and model are an entry of the owner's table for the build's
    author. The table is config.load() with no repo, so a repo's config.toml cannot add a reviewer. Effort is not
    compared."""
    try:
        cfg = config.load()
        entries = config.review_preferences(cfg, (st.get("authors") or {}).get("build"))
    except RelayError as e:
        return str(e)
    for spec in entries:  # an entry always names a model (parse_model_spec refuses one that does not)
        if spec.provider == meta["reviewer"] and spec.model == meta["model"]:
            return None
    return NOT_IN_TABLE


def conditions(repo, st, commit, remote, base, base_sha, pr_head):
    """R9: (reason, facts). The first failure of R3 to R8, in that order, or (None, facts). The caller adds PREFIX.
    `base_sha` is the base commit relay checked: the opt-in is read at it and the protected-path diff starts from it."""
    name = repo_name(remote)
    try:
        listed, reason = production_branches(repo, base_sha, base, name)
    except RelayError as e:  # a TOML error names its file
        return str(e), None
    if reason:
        return reason, None
    reason = not_control_plane(remote) or not_production(base, listed)
    if reason:
        return reason, None
    meta, reason = review_evidence(repo, st, commit)
    reason = reason or table_reason(st, meta)
    if reason:
        return reason, None
    try:
        mode = cimode.settings(repo, base)
    except RelayError as e:
        return str(e), None
    reason = ci_reason(mode)
    if reason:
        return reason, None
    path = protected_path(repo, base_sha, pr_head)
    if path:
        return f"touches {path}, which the owner merges", None
    return None, {"reviewer": meta["reviewer"], "mode": mode.mode}
