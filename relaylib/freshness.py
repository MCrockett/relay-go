"""Is a GO still about what it reviewed? (spec section 1, Freshness)."""
import hashlib
import os

from . import gitops
from .state import RELAY_DIR, feature_dir

INPUTS = {"spec": ["spec"], "plan": ["spec", "plan"], "build": ["spec", "plan"]}


def sha256_file(path):
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def input_hashes(root, st, stage, ref=None):
    """Hashes of the files a GO depends on: from the checkout, or from a git ref (a branch that is not
    checked out), with the tree path found case-insensitively."""
    skipped = st.get("skipped") or []
    names = [n for n in INPUTS[stage] if n not in skipped]
    if skipped or st.get("adopted"):  # small or adopted: idea.md holds (part of) the requirements
        names.append("idea")
    if ref is None:
        d = feature_dir(root, st["feature"])
        return {name: sha256_file(os.path.join(d, f"{name}.md")) for name in names}
    tree = {p.lower(): p for p in gitops.ls_files(root, ref, f"{RELAY_DIR}/{st['feature']}")}
    out = {}
    for name in names:
        path = tree.get(f"{RELAY_DIR}/{st['feature']}/{name}.md".lower())
        text = gitops.show(root, ref, path) if path else None
        out[name] = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
    return out


def bookkeeping_prefix(st):
    """What code comparisons ignore: all of docs/relay/, not only this feature's folder. A stacked PR
    below this one merges its own review and state files into develop, and that is not a code change.
    This feature's own spec, plan and idea stay bound by their hashes (input_hashes)."""
    return f"{RELAY_DIR}/"


def merge_digest(root, base_ref, head, prefix):
    """Digest of the merge result of base_ref and head, minus the feature folder.
    None if it cannot be computed (conflicts or a missing ref)."""
    p = gitops.git(root, "merge-tree", "--write-tree", base_ref, head, check=False)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    tree = p.stdout.split()[0]
    listing = gitops.git(root, "ls-tree", "-r", "--full-tree", tree).stdout.splitlines()
    kept = [line for line in listing if not line.split("\t", 1)[1].lower().startswith(prefix.lower())]
    return hashlib.sha256("\n".join(kept).encode()).hexdigest()


def snapshot(root, st, base_ref=None):
    stage = st["stage"]
    snap = {"inputs": input_hashes(root, st, stage)}
    if stage == "build":
        head = gitops.head_sha(root)
        snap.update(base_ref=base_ref, base_sha=gitops.head_sha(root, base_ref), head=head,
                    merge=merge_digest(root, base_ref, head, bookkeeping_prefix(st)))
    return snap


def check(root, st, stage, base_ref=None, ref=None):
    """ref: check the GO against a branch that is not checked out (default: the checkout's HEAD)."""
    snap = (st.get("reviewed") or {}).get(stage)
    if not snap:
        return False, "no GO recorded"
    head = ref or "HEAD"
    now = input_hashes(root, st, stage, ref)
    for name, recorded in snap["inputs"].items():
        if now.get(name) != recorded:
            return False, f"{name}.md changed since the GO"
    if stage != "build":
        return True, "inputs unchanged"
    prefix = bookkeeping_prefix(st)
    diff = gitops.git(root, "diff", "--quiet", snap["head"], head, "--", ".",
                      f":(exclude,icase){prefix}", check=False)
    if diff.returncode != 0:
        return False, "code changed since the GO"
    base_ref = base_ref or snap["base_ref"]
    if base_ref != snap["base_ref"]:
        return False, f"PR base changed ({snap['base_ref']} -> {base_ref})"
    merge = merge_digest(root, base_ref, head, prefix)
    if merge is None or merge != snap["merge"]:
        return False, f"merge result against {base_ref} changed"
    return True, "code and merge result unchanged"


def upstream_invalidated(root, st):
    """The earliest approved stage before the current one whose inputs changed since its GO."""
    for stage in ("spec", "plan"):
        if stage == st["stage"]:
            break
        if stage in st.get("skipped", []):
            continue
        if st["verdicts"].get(stage) == "GO" and not check(root, st, stage)[0]:
            return stage
    return None
