"""Is a GO still about what it reviewed? (spec section 1, Freshness)."""
import hashlib
import os

from . import gitops
from .errors import RelayError
from .state import RELAY_DIR, feature_dir

DATA_EXTENSIONS = (".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".md", ".txt")
NEVER_DATA_NAMES = {"agents.md", "claude.md", "workflow.md"}  # delegated-merge's protected names, anywhere
CONFIG = f"{RELAY_DIR}/config.toml"

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


def _entry_problem(entry):
    """Why one data_paths entry is unusable (data-paths-freshness D6), or None."""
    if not isinstance(entry, str):
        return f"{entry!r} is not a string"
    if not entry or entry != entry.strip():
        return f"{entry!r} is empty or has surrounding spaces"
    if entry.startswith("/") or "\\" in entry or "//" in entry:
        return f"{entry!r} must be a plain path from the repository root"
    if any(c in entry for c in "*?["):
        return f"{entry!r} has a glob character"
    if any(part in (".", "..") for part in entry.rstrip("/").split("/")):
        return f"{entry!r} has a . or .. part"
    if not entry.endswith("/") and not entry.lower().endswith(DATA_EXTENSIONS):
        return f"{entry!r} is not a data file ({', '.join(DATA_EXTENSIONS)}) or a folder ending in /"
    return None


def data_paths(root, base_commit):
    """(entries, why) from [freshness] data_paths in docs/relay/config.toml at the base commit, never the PR
    branch (D1). No file or key: ([], None). Anything unusable: ([], why), so a bad list never helps (D6)."""
    from . import cimode
    text = gitops.show(root, base_commit, CONFIG)
    if text is None:
        return [], None
    try:
        data = cimode._read_toml(text, CONFIG)
    except RelayError as e:
        return [], str(e)
    section = data.get("freshness")
    if section is None or (isinstance(section, dict) and "data_paths" not in section):
        return [], None
    listed = section.get("data_paths") if isinstance(section, dict) else None
    if not isinstance(listed, list):
        return [], "[freshness] data_paths must be a list of paths"
    for entry in listed:
        why = _entry_problem(entry)
        if why:
            return [], why
    return list(listed), None


def never_data(path):
    low = path.lower()
    return (os.path.basename(low) in NEVER_DATA_NAMES or low.startswith(".github/")
            or low.startswith(f"{RELAY_DIR}/"))


def is_data(path, entries):
    """True when a valid entry covers path, it has a data extension, and it is not never-data (D3, D7)."""
    if not path.lower().endswith(DATA_EXTENSIONS) or never_data(path):
        return False
    return any(path == e or (e.endswith("/") and path.startswith(e)) for e in entries)


def applied(root, base_commit, head):
    """(entries, why): the data paths a digest leaves out for this PR. [] when the PR's own diff touches data
    (R4) or there is no merge-base (F2)."""
    entries, why = data_paths(root, base_commit)
    if not entries:
        return [], why
    mb = gitops.git(root, "merge-base", base_commit, head, check=False)
    if mb.returncode != 0 or not mb.stdout.strip():
        return [], why
    out = gitops.git(root, "diff", "--name-only", "--no-renames", "-z", mb.stdout.strip(), head, check=False)
    if out.returncode != 0 or any(is_data(p, entries) for p in out.stdout.split("\0") if p):
        return [], why
    return entries, why


def merge_digest(root, base_ref, head, prefix, entries=()):
    """Digest of the merge result of base_ref and head, minus docs/relay/ and the declared data paths.
    None if it cannot be computed (conflicts or a missing ref)."""
    p = gitops.git(root, "merge-tree", "--write-tree", base_ref, head, check=False)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    tree = p.stdout.split()[0]
    listing = gitops.git(root, "ls-tree", "-r", "--full-tree", tree).stdout.splitlines()
    kept = [line for line in listing if not line.split("\t", 1)[1].lower().startswith(prefix.lower())]
    if entries:  # the hashed lines keep today's form; the -z listing names each path exactly
        raw = [r for r in gitops.git(root, "ls-tree", "-r", "-z", "--full-tree", tree).stdout.split("\0") if r]
        if len(raw) != len(listing):
            return None
        drop = {line for line, r in zip(listing, raw) if is_data(r.split("\t", 1)[1], entries)}
        kept = [line for line in kept if line not in drop]
    return hashlib.sha256("\n".join(kept).encode()).hexdigest()


def _resolve(root, ref):
    try:
        return gitops.head_sha(root, ref)
    except RelayError:
        return None


def snapshot(root, st, base_ref=None):
    stage = st["stage"]
    snap = {"inputs": input_hashes(root, st, stage)}
    if stage == "build":
        head = gitops.head_sha(root)
        base = gitops.head_sha(root, base_ref)  # one base commit for the config, merge-base and merge-tree
        entries, _ = applied(root, base, head)
        snap.update(base_ref=base_ref, base_sha=base, head=head, data_paths=entries,
                    merge=merge_digest(root, base, head, bookkeeping_prefix(st), entries))
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
    base_ref = base_ref or snap["base_ref"]
    base = _resolve(root, base_ref)
    entries, bad = applied(root, base, head) if base else ([], None)
    note = f"; data paths on {base_ref} not used: {bad}" if bad else ""
    diff = gitops.git(root, "diff", "--quiet", snap["head"], head, "--", ".",
                      f":(exclude,icase){prefix}", check=False)
    if diff.returncode != 0:
        return False, "code changed since the GO" + note
    if base_ref != snap["base_ref"]:
        return False, f"PR base changed ({snap['base_ref']} -> {base_ref})" + note
    if base is None:
        return False, f"merge result against {base_ref} changed" + note
    if entries != (snap.get("data_paths") or []):
        return False, "data paths changed since the GO" + note
    merge = merge_digest(root, base, head, prefix, entries)
    if merge is None or merge != snap["merge"]:
        return False, f"merge result against {base_ref} changed" + note
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
