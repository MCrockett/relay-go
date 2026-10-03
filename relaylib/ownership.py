"""One writing agent per repo, checked in every worktree and on every origin branch (spec section 3)."""
import os
from dataclasses import dataclass

from . import gitops, merged
from .errors import RelayError
from .state import RELAY_DIR, HOLDING_STATUSES, feature_dir, list_features, parse_state


@dataclass
class Claim:
    slug: str
    where: str
    owner: dict
    status: str
    has_handoff: bool
    done: bool = False
    handed_over_from: str | None = None  # the session a handoff on this feature releases, once it has moved
    pr: int | None = None


def committed(wt, path):
    """A local handoff only counts once it is committed: tracked and unchanged from HEAD."""
    if not os.path.exists(path):
        return False
    rel = os.path.relpath(path, wt)
    tracked = gitops.git(wt, "ls-files", "--error-unmatch", "--", rel, check=False).returncode == 0
    clean = gitops.git(wt, "diff", "--quiet", "HEAD", "--", rel, check=False).returncode == 0
    return tracked and clean


def collect(root):
    """Claims from every worktree and every origin branch. A feature's state counts only on its own
    branch: a stacked branch carries inherited copies of the features beneath it, and those are ignored."""
    gitops.fetch(root)
    claims = []
    for wt in gitops.worktrees(root):
        try:
            wt_branch = gitops.current_branch(wt)
        except RelayError:
            continue
        for slug, st in list_features(wt):
            if st.get("branch") and st["branch"] != wt_branch:
                continue  # inherited from a branch this one is stacked on
            handoff = committed(wt, os.path.join(feature_dir(wt, slug), "handoff.md"))
            claims.append(Claim(slug, wt, st.get("owner") or {}, st.get("status"), handoff,
                                merged.is_done(root, st), st.get("handed_over_from"), st.get("pr")))
    for ref in gitops.remote_branches(root):
        files = gitops.ls_files(root, ref, RELAY_DIR)
        lowered = {f.lower() for f in files}
        for path in files:
            parts = path.split("/")
            if len(parts) != 4 or parts[3].lower() != "state.md":
                continue
            st = parse_state(gitops.show(root, ref, path) or "", f"{ref}:{path}")
            if st.get("branch") and ref != f"origin/{st['branch']}":
                continue  # inherited copy on a stacked branch, or a merged feature's copy on develop
            folder = "/".join(parts[:3])
            claims.append(Claim(parts[2], ref, st.get("owner") or {}, st.get("status"),
                                f"{folder}/handoff.md".lower() in lowered, merged.is_done(root, st),
                                st.get("handed_over_from"), st.get("pr")))
    return claims


def released_sessions(claims):
    """Ownership is per repo, so a handoff is per session: a published handoff on any feature
    releases every feature that session holds in the repo. Only a handoff on origin counts: a local
    commit whose push failed has not been handed to anyone."""
    return {c.handed_over_from or c.owner.get("session")
            for c in claims if c.has_handoff and c.where.startswith("origin/")}


def held_by(claims, session):
    """Branches and slugs of the features a session still holds, from their published copies on origin."""
    found = {}
    for c in claims:
        if (c.owner.get("session") == session and c.where.startswith("origin/") and not c.done
                and c.status in HOLDING_STATUSES):
            found[c.slug] = c.where[len("origin/"):]
    return sorted(found.items())


def conflicts(claims, session):
    released = released_sessions(claims)
    return [c for c in claims if c.status in HOLDING_STATUSES and not c.done
            and c.owner.get("session") != session and not c.has_handoff
            and c.owner.get("session") not in released]


def check_can_write(root, session):
    bad = conflicts(collect(root), session)
    if bad:
        lines = [f"  {c.slug} in {c.where}: {c.owner.get('provider')} session {c.owner.get('session')} ({c.status})"
                 for c in bad]
        raise RelayError("another session owns work in this repo:\n" + "\n".join(lines)
                        + "\nAsk it to run `relay handoff` then `relay handoff --commit`, "
                          "or the owner can run `relay override release`.")
