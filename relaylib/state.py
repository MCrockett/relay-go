"""state.md: a feature's machine state, stored as JSON frontmatter (JSON is valid YAML)."""
import datetime
import glob
import json
import os

from .errors import RelayError

RELAY_DIR = "docs/relay"
HOLDING_STATUSES = ("drafting", "changes-requested", "in-review", "review-error", "waiting-owner", "ready-to-merge")
BODY = "\n# Relay state\n\nWritten by `relay`. Do not edit by hand.\n"


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def relay_dir(root):
    """docs/relay spelled the way it exists on disk; a repo may track it as Docs/relay, which a
    case-sensitive checkout would otherwise not find."""
    path = root
    for part in RELAY_DIR.split("/"):
        try:
            names = os.listdir(path)
        except OSError:
            names = []
        path = os.path.join(path, next((n for n in names if n.lower() == part), part))
    return path


def feature_dir(root, slug):
    return os.path.join(relay_dir(root), slug)


def state_path(root, slug):
    return os.path.join(feature_dir(root, slug), "state.md")


def new_state(slug, repo, owner, branch, small=False):
    return {
        "feature": slug, "repo": repo,
        "stage": "build" if small else "idea", "status": "drafting",
        "owner": owner, "small": small,
        "skipped": ["spec", "plan"] if small else [],
        "authors": {}, "rounds": {}, "extra_rounds": {}, "verdicts": {}, "history": {},
        "reviewed": {"spec": None, "plan": None, "build": None},
        "refresh": False, "retried": False,
        "branch": branch, "pr": None, "owner_actions": [], "updated": now_iso(),
    }


def parse_state(text, where="state.md"):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RelayError(f"{where}: missing frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise RelayError(f"{where}: unterminated frontmatter")
    try:
        return json.loads("\n".join(lines[1:end]))
    except json.JSONDecodeError as e:
        raise RelayError(f"{where}: bad state JSON: {e}")


def read_state(path):
    with open(path) as f:
        return parse_state(f.read(), path)


def write_state(path, st):
    st["updated"] = now_iso()
    if st.get("status") != "review-error":  # every way out of a failed review clears its reason
        st.pop("review_error", None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("---\n" + json.dumps(st, indent=2, sort_keys=True) + "\n---\n" + BODY)


def list_features(root):
    out = []
    for path in sorted(glob.glob(os.path.join(relay_dir(root), "*", "state.md"))):
        out.append((os.path.basename(os.path.dirname(path)), read_state(path)))
    return out


def select_feature(root, branch, explicit=None, is_done=None):
    """is_done(st) decides which features are finished; commands pass merged.is_done."""
    done = is_done or (lambda st: st.get("status") == "done")
    feats = list_features(root)
    slugs = [slug for slug, _ in feats]
    if explicit:
        if explicit not in slugs:
            raise RelayError(f"no feature {explicit!r}; have: {', '.join(slugs) or 'none'}")
        return explicit
    on_branch = [slug for slug, st in feats if st.get("branch") == branch]
    if len(on_branch) == 1:
        return on_branch[0]
    active = [slug for slug, st in feats if not done(st)]
    if not on_branch and len(active) == 1:
        return active[0]
    candidates = on_branch or active or slugs
    raise RelayError("cannot tell which feature you mean; pass --feature. Candidates: "
                    + (", ".join(candidates) or "none"))
