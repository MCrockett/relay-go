"""Sessions waiting on the owner that no listed feature accounts for (other-sessions D1 to D4). Read-only:
session records as the hooks wrote them, last messages read live and never stored (D8)."""
import os

from . import agentask, gitops, health
from .errors import RelayError


def _repo_name(checkout):
    try:
        return os.path.basename(os.path.dirname(gitops.common_dir(checkout)))
    except RelayError:
        return os.path.basename(checkout)


def place(cwd, checkouts):
    """(project, checkout): the repository holding cwd and the checkout folder when it differs from the
    repository's name (a worktree), else the folder itself and None (D3, where-i-left-off D3)."""
    if not cwd:
        return "Unknown folder", None
    real = os.path.realpath(cwd)
    inside = [c for c in (os.path.realpath(c) for c in checkouts) if real == c or real.startswith(c + os.sep)]
    if inside:
        checkout = max(inside, key=len)  # nested checkouts: the closest one
        name, folder = _repo_name(checkout), os.path.basename(checkout)
        return name, (None if name == folder else folder)
    home = os.path.realpath(os.path.expanduser("~"))
    if real == home or real.startswith(home + os.sep):
        return "~" + real[len(home):], None
    return real, None


def label(cwd, checkouts):
    """The repository (and worktree folder when it differs) holding cwd, else the folder itself (D3)."""
    name, checkout = place(cwd, checkouts)
    return name if checkout is None else f"{name} ({checkout})"


def _tools(record):
    out = []
    for p in record.get("pending") or []:
        if p["tool_name"] not in out:
            out.append(p["tool_name"])
    return out


def listed(records, claimed, root, cfg, now):
    """D4 entries for the records D1 lists, oldest wait first. cfg is the global configuration (D1)."""
    from . import status  # status imports this module
    grace = health.ui_value(cfg, "health_grace_minutes") * 60
    window = health.ui_value(cfg, "other_sessions_hours") * 3600
    candidates = [r for r in records
                  if r.get("state") in ("waiting", "permission") and r["session_id"] not in claimed
                  and grace < now - r["since"] <= window]
    if not candidates:
        return []
    try:
        checkouts = status.checkouts(root)
    except Exception:
        checkouts = []
    out = []
    for r in candidates:
        try:
            words = agentask.last_words(r["provider"], r["session_id"])
            if r["state"] == "waiting" and not words:
                continue  # never said anything: not asking for anything (D1)
            out.append({"provider": r["provider"], "session_id": r["session_id"], "label": label(r.get("cwd"), checkouts),
                        "folder": r.get("cwd"), "state": r["state"], "since": r["since"],
                        "pending_tools": _tools(r),
                        "excerpt": ({"source": words["source"], "text": agentask.excerpt(words["text"])}
                                    if words else None)})
        except Exception:  # F5: one session never hides the others
            continue
    return sorted(out, key=lambda e: (e["since"], e["session_id"]))
