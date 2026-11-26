"""Delegated merges (D1): the conditions under which the hordle release role may merge without the owner, the
merge commit relay pushes to the checked base, and the bounded wait for GitHub to mark the PR merged. The
conditions live here in one function (R9) so a test can drive each alone; owneractions.merge_delegated
orchestrates them."""
import os

from . import cimode, gitops, state
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
