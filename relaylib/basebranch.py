"""The branch on origin a new feature starts from (relay new, relay take).

Order: `--base`, then `[relay] base_branch` in the repo's docs/relay/config.toml, then origin/develop when it is
active (it has commits origin/HEAD lacks, and is not stale), then origin/HEAD. The owner chose this fallback over
plain origin/HEAD on 2026-11-28. Many repos keep main as the GitHub default but work on develop, so
origin/HEAD alone is not enough; a develop that origin/HEAD has left behind is the case this guards against."""
import os
import re
import tomllib

from . import gitops
from .errors import RelayError
from .state import relay_dir

STALE_DAYS = 14
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def configured(root):
    """[relay] base_branch from the repo's own config file, or None. Only the repo's file counts: the base is a
    fact about the repo, not an owner-wide setting."""
    path = os.path.join(relay_dir(root), "config.toml")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise RelayError(f"{path}: {e}")
    value = (data.get("relay") or {}).get("base_branch") if isinstance(data.get("relay"), dict) else None
    if value is None:
        return None
    if not isinstance(value, str) or not BRANCH.fullmatch(value) or ".." in value:
        raise RelayError(f"{path}: [relay] base_branch must be a branch name")
    return value


def _exists(root, ref):
    return gitops.git(root, "rev-parse", "--verify", "-q", ref, check=False).returncode == 0


def origin_head(root):
    """The branch origin/HEAD points at (for example "main"), or None when it is not set locally."""
    p = gitops.git(root, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD", check=False)
    name = p.stdout.strip()
    if p.returncode != 0 or not name.startswith("origin/"):
        return None
    name = name[len("origin/"):]
    return name if name and _exists(root, f"origin/{name}") else None


def _tip_time(root, ref):
    p = gitops.git(root, "log", "-1", "--format=%ct", ref, check=False)
    try:
        return int(p.stdout.strip())
    except ValueError:
        return None


def behind(root, base, head):
    """(commits on origin/<head> missing from origin/<base>, days the base tip is older), or None when unknown."""
    p = gitops.git(root, "rev-list", "--count", f"origin/{base}..origin/{head}", check=False)
    t_base, t_head = _tip_time(root, f"origin/{base}"), _tip_time(root, f"origin/{head}")
    try:
        missing = int(p.stdout.strip())
    except ValueError:
        return None
    if p.returncode != 0 or t_base is None or t_head is None:
        return None
    return missing, max(0, (t_head - t_base) // 86400)


def stale(root, base, head):
    """True when origin/<head> has commits the base lacks and the base tip is STALE_DAYS or more older."""
    if base == head:
        return False
    gap = behind(root, base, head)
    return bool(gap) and gap[0] > 0 and gap[1] >= STALE_DAYS


def active_develop(root, head):
    """True when origin/develop should be the base: origin/HEAD is unknown, or is develop, or develop has commits
    origin/HEAD lacks and is not stale. A develop that is only behind origin/HEAD is never chosen."""
    if not _exists(root, "origin/develop"):
        return False
    if not head or head == "develop":
        return True
    ahead = behind(root, head, "develop")
    return bool(ahead) and ahead[0] > 0 and not stale(root, "develop", head)


def choose(root, explicit=None):
    """(base branch name, how it was chosen, warning or None). Raises RelayError when no base can be found."""
    head = origin_head(root)
    if explicit:
        base, why = explicit, "--base"
    elif (cfg := configured(root)) is not None:
        base, why = cfg, "[relay] base_branch"
    elif active_develop(root, head):
        base, why = "develop", "origin/develop" + (f" (origin/HEAD is {head})" if head and head != "develop" else "")
    elif head:
        base, why = head, "origin/HEAD" + (" (origin/develop is stale or has no work of its own)" if _exists(root, "origin/develop") else "")
    else:
        raise RelayError("cannot tell which branch to start from: no origin/develop and origin/HEAD is not set. "
                         "Pass --base <branch>, or set [relay] base_branch in docs/relay/config.toml")
    if not _exists(root, f"origin/{base}"):
        raise RelayError(f"no origin/{base} to branch from ({why}); pass --base <branch>")
    warning = None
    if head and stale(root, base, head):
        missing, days = behind(root, base, head)
        warning = (f"origin/{base} ({why}) is {days} days behind origin/{head} and lacks {missing} of its "
                   f"commits. If {head} is the real base, set [relay] base_branch = \"{head}\" in "
                   "docs/relay/config.toml.")
    return base, why, warning
