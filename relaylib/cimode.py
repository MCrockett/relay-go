"""The build settings that gate a PR (ci-modes D3): the global config merged with the repo config as it is on
the tip of the PR's base branch on origin, never the caller's working tree. Does not import commands."""
import copy
import os
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
