"""Whether a bookkeeping commit may tell GitHub to skip CI (ci-skip-bookkeeping). relay's own gates read CI
through gitops.ci_for_code, which passes over marked commits to the newest result for the same code, so a
marked commit costs nothing as long as GitHub already has CI for that code and the push carries no new code."""
from . import gitops
from .state import RELAY_DIR

PREFIX = RELAY_DIR + "/"
EVIDENCE = ("green", "failing", "pending")


def _ref(root, ref):
    return gitops.git(root, "rev-parse", "--verify", "-q", ref, check=False).returncode == 0


def required_checks(root, branches):
    """True when GitHub requires status checks on any of branches that exist on origin (D4). A failed call
    raises, and the caller treats that as no marker."""
    for branch in branches:
        if not _ref(root, f"origin/{branch}"):
            continue
        pages = gitops.gh_json(root, ["api", "--paginate", "--slurp",
                                      f"repos/{{owner}}/{{repo}}/rules/branches/{branch}?per_page=100"])
        rules = [r for page in pages or [] for r in (page if isinstance(page, list) else [page])]
        if any(isinstance(r, dict) and r.get("type") == "required_status_checks" for r in rules):
            return True
        info = gitops.gh_json(root, ["api", f"repos/{{owner}}/{{repo}}/branches/{branch}"])
        checks = ((info or {}).get("protection") or {}).get("required_status_checks") or {}
        if checks.get("contexts") or checks.get("checks") or checks.get("enforcement_level") not in (None, "off"):
            return True
    return False


def marker_ok(root, branch, pr, cfg, status):
    """D2 with D4 and D10: True when the commit about to be made at root may carry the skip marker. status is
    the feature's status as that commit records it. Any error means False (D9)."""
    try:
        if (cfg.get("build") or {}).get("skip_ci", "auto") != "auto":
            return False
        base = gitops.pr_info(root, pr).get("baseRefName") if pr else None
        ref = next((r for r in (f"origin/{branch}", f"origin/{base or 'develop'}") if _ref(root, r)), None)
        if ref is None:
            return False
        if gitops.git(root, "diff", "--quiet", ref, "HEAD", "--", ".", f":(exclude,icase){PREFIX}",
                      check=False).returncode != 0:
            return False  # the push would carry code origin does not have: it must start CI
        if gitops.ci_for_code(root, gitops.head_sha(root), PREFIX) not in EVIDENCE:
            return False  # GitHub has no CI for this code yet, e.g. CI runs only on PRs and none is open
        required = required_checks(root, [base] if base else ["develop", "main"])
        return not (required and status == "ready-to-merge")  # the merge head reports required checks (D10)
    except Exception:
        return False


def commit(root, slug, st, cfg, message):
    """Commit the feature's relay folder, marked when marker_ok allows. Returns the sha of a marked commit, or
    None when nothing was marked (or nothing changed)."""
    prefix = f"{RELAY_DIR}/{slug}"
    mark = bool(gitops.changed_paths_under(root, prefix)) and \
        marker_ok(root, st["branch"], st.get("pr"), cfg, st.get("status"))
    made = gitops.commit_paths_under(root, prefix, message, skip_ci=mark)
    return gitops.head_sha(root) if made and mark else None


def mark_head(root, st, cfg):
    """ci-on-submit D4: before a draft whose code already passed CI is marked ready, make sure its head carries
    the marker, so the ready_for_review event starts no run. Adds an empty commit "relay: mark PR ready" only when
    the head is unmarked and marker_ok allows a marker; the index and working tree are left alone. Returns the
    new sha, or None when nothing was made."""
    if gitops.marked(gitops.git(root, "log", "-1", "--format=%B").stdout):
        return None
    if not marker_ok(root, st["branch"], st.get("pr"), cfg, st.get("status")):
        return None
    old = gitops.head_sha(root)
    new = gitops.git(root, "commit-tree", "HEAD^{tree}", "-p", old, "-m", "relay: mark PR ready",
                     "-m", gitops.SKIP_CI).stdout.strip()
    gitops.git(root, "update-ref", "-m", "relay: mark PR ready", "HEAD", new, old)
    return new


def unmark(root, branch, sha):
    """D7: after a failed push, drop the marker from the unpublished tip so a later push with code starts CI.
    Only when HEAD is still sha and origin's branch is not sha; otherwise, or on any error, nothing changes."""
    try:
        if gitops.head_sha(root) != sha:
            return False
        remote = gitops.network_git(root, "ls-remote", "origin", f"refs/heads/{branch}")
        if remote.returncode != 0 or remote.stdout.split()[:1] == [sha]:
            return False
        message = gitops.git(root, "log", "-1", "--format=%B", sha).stdout
        kept = "\n".join(line for line in message.splitlines() if line.strip() != gitops.SKIP_CI).strip()
        gitops.git(root, "commit", "--amend", "--only", "--allow-empty", "-q", "-m", kept)
        return True
    except Exception:
        return False


def push(root, branch, marked_sha):
    """gitops.push, unmarking marked_sha when the push fails (D7). The push error is raised as before."""
    try:
        gitops.push(root, branch)
    except Exception:
        if marked_sha:
            unmark(root, branch, marked_sha)
        raise
