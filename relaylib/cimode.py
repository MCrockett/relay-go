"""The build settings that gate a PR (ci-modes D3): the global config merged with the repo config as it is on
the tip of the PR's base branch on origin, never the caller's working tree. Does not import commands."""
import copy
import os
import re
import tomllib
from dataclasses import dataclass

from . import config, gitops
from .errors import RelayError
from .state import RELAY_DIR


@dataclass(frozen=True)
class Settings:
    mode: str          # pr, local or none (release already resolved)
    configured: str    # the raw value: pr, local, release or none
    test_command: str
    timeout_min: float
    base: str


def resolve(configured, base):
    """release is pr for a PR into main and local for any other base (R9)."""
    if configured == "release":
        return "pr" if base == "main" else "local"
    return configured


def _read_toml(text, label):
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise RelayError(f"{label}: {e}")


def settings(repo, base):
    build = copy.deepcopy(config.DEFAULTS["build"])
    path = config.config_path()
    if os.path.isfile(path):
        with open(path, "rb") as f:
            try:
                data = tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                raise RelayError(f"{path}: {e}")
        if isinstance(data.get("build"), dict):
            build.update(config.normalize_build(data["build"], path))
    label = f"origin/{base}:{RELAY_DIR}/config.toml"
    text = gitops.show(repo, f"origin/{base}", f"{RELAY_DIR}/config.toml")
    if text is not None:
        data = _read_toml(text, label)
        if isinstance(data.get("build"), dict):
            build.update(config.normalize_build(data["build"], label))
    return Settings(resolve(build["ci"], base), build["ci"], build["test_command"],
                    build["test_timeout_min"], base)


def require_command(s):
    if s.mode == "local" and not s.test_command.strip():
        raise RelayError(f'set [build] test_command in docs/relay/config.toml on {s.base}, or set ci = "none"')


def _pull_request_hit(text, base):
    """True when this workflow's pull_request trigger would run for a PR into base (D12). A text check, not a YAML
    parse: false positives are fine because the result is only a warning."""
    lines = text.splitlines()
    on_re = re.compile(r"""^["']?on["']?\s*:(.*)$""")
    for i, line in enumerate(lines):
        m = on_re.match(line)
        if not m:
            continue
        rest = m.group(1).split("#")[0].strip()
        if rest:  # inline: on: pull_request, on: [push, pull_request], on: {pull_request: ...}
            return "pull_request" in re.findall(r"[\w-]+", rest)
        block = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and not nxt.startswith((" ", "\t")):
                break
            block.append(nxt)
        return _block_hit(block, base)
    return False


def _block_hit(block, base):
    for j, line in enumerate(block):
        m = re.match(r"^(\s+)pull_request\s*:(.*)$", line)
        if not m:
            continue
        indent = len(m.group(1))
        inner = [m.group(2)]
        for nxt in block[j + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            inner.append(nxt)
        text = "\n".join(inner)
        at = text.find("branches:")
        if at < 0:
            return True
        return re.search(r"(?<![\w/.-])" + re.escape(base) + r"(?![\w/.-])", text[at:]) is not None
    return False


def workflow_warnings(repo, ref, base):
    """One warning line per workflow file whose pull_request trigger covers a PR into base (D12)."""
    out = []
    listing = gitops.git(repo, "ls-tree", "--name-only", ref, ".github/workflows/", check=False)
    for path in listing.stdout.splitlines() if listing.returncode == 0 else []:
        if not path.endswith((".yml", ".yaml")):
            continue
        text = gitops.show(repo, ref, path)
        if text is not None and _pull_request_hit(text, base):
            out.append(f"{path}: runs on pull_request to {base}; with ci = local that spends GitHub minutes")
    return out
