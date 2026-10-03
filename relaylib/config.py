"""Configuration: defaults, the owner's ~/.relay/config.toml, an optional per-repo docs/relay/config.toml, role specs."""
import copy
import os
import re
import tomllib
from dataclasses import dataclass

from .errors import RelayError

RELAY_HOME_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROVIDERS = ("claude", "codex")
ROLE_KEYS = ("spec", "plan", "build", "audit")
DEFAULTS = {
    "roles": {
        "spec": "claude:claude-opus-5-5",
        "plan": "claude:claude-opus-5-5",
        "build": "codex:gpt-6-astra",
        "audit": "claude:claude-opus-5-5",
        "reviewer_models": {"claude": "claude-sonnet-5", "codex": "gpt-6-astra@high"},
    },
    "limits": {
        "max_rounds": 4,
        "review_timeout_min": 20,
        "handoff_context_pct": 60,
        "daily_share_pct": 15,
        "weekly_caution_pct": 90,
        "weekly_stop_pct": 100,
        "budget_stale_hours": 6,
        "timezone": "America/Detroit",
    },
    "build": {"require_ci": True},
    "review": {
        # reviewer effort, unless a preference entry names its own (provider:model@effort)
        "effort": "medium",           # every round
        "final_effort": "high",       # the last round before the stage comes to the owner
        "release_effort": "high",     # the build review of a PR to main
        # who reviews, by the stage author's provider; the first available entry wins
        "prefer": {
            "claude": ["codex:gpt-6-astra", "claude:claude-sonnet-5", "claude:claude-fable-5-1"],
            "codex": ["claude:claude-sonnet-5", "claude:claude-fable-5-1"],
            "owner": ["codex:gpt-6-astra", "claude:claude-sonnet-5"],
        },
    },
    "notify": {"enabled": True},
    "ui": {"health_grace_minutes": 1, "quiet_minutes": 30},
    "projects": {"root": "~/Projects"},  # the folder whose repos relay status and the dashboard scan
}


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    effort: str | None = None


def parse_model_spec(text, provider=None):
    original = text
    text = text.strip()
    if ":" in text:
        provider, text = text.split(":", 1)
    if provider not in PROVIDERS:
        raise RelayError(f"unknown provider in {original!r}; expected one of {', '.join(PROVIDERS)}")
    model, _, effort = text.partition("@")
    if not model:
        raise RelayError(f"model name is empty in {original!r}")
    return ModelSpec(provider, model, effort or None)


def config_path():
    return os.environ.get("RELAY_CONFIG") or os.path.join(relay_home(), "config.toml")


def relay_home():
    """Per-machine state: ledger, merged-PR cache. Never committed."""
    return os.environ.get("RELAY_HOME") or os.path.expanduser("~/.relay")


def _merge(base, over):
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load(repo_root=None):
    cfg = copy.deepcopy(DEFAULTS)
    paths = [config_path()]
    if repo_root:
        from .state import relay_dir  # state does not import config, so no import cycle
        paths.append(os.path.join(relay_dir(repo_root), "config.toml"))
    for path in paths:
        if os.path.isfile(path):
            with open(path, "rb") as f:
                try:
                    _merge(cfg, tomllib.load(f))
                except tomllib.TOMLDecodeError as e:
                    raise RelayError(f"{path}: {e}")
    return cfg


def reviewer_model(cfg, provider):
    return parse_model_spec(cfg["roles"]["reviewer_models"][provider], provider)


def review_effort(cfg, last_round, release):
    """The effort a reviewer runs at when its preference entry does not name one."""
    review = cfg.get("review") or {}
    if release:
        return review.get("release_effort", "high")
    if last_round:
        return review.get("final_effort", "high")
    return review.get("effort", "medium")


def review_preferences(cfg, author):
    """Ordered reviewer ModelSpecs for work by `author` (claude, codex, or anything else = owner)."""
    table = (cfg.get("review") or {}).get("prefer") or {}
    entries = table.get(author if author in PROVIDERS else "owner") or []
    return [parse_model_spec(e) for e in entries]


def set_role(path, key, value):
    """Rewrite one role line in config.toml, leaving every other line as written."""
    if key.startswith("review."):  # an ordered preference list: "provider:model[@effort], ..."
        section, name = "review.prefer", key.split(".", 1)[1]
        if name not in PROVIDERS + ("owner",):
            raise RelayError(f"review.{name}: the author must be claude, codex or owner")
        entries = [e.strip() for e in value.split(",") if e.strip()]
        if not entries:
            raise RelayError("give at least one reviewer, e.g. codex:gpt-6-astra@high")
        for e in entries:
            parse_model_spec(e)
        value = None
        new_line = f"{name} = [" + ", ".join(f'"{e}"' for e in entries) + "]"
    elif key.startswith("reviewer."):
        section, name = "roles.reviewer_models", key.split(".", 1)[1]
        parse_model_spec(value, name)
    elif key in ROLE_KEYS:
        section, name = "roles", key
        parse_model_spec(value)
    elif key == "review":
        raise RelayError("reviewers are a preference list per author now: relay roles set review.<author> "
                         "\"provider:model, ...\" (see relay roles)")
    else:
        raise RelayError(f"unknown role {key!r}; expected {', '.join(ROLE_KEYS)} or reviewer.<provider>")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lines = []
    if os.path.exists(path):
        with open(path) as f:
            lines = f.read().splitlines()
    header = f"[{section}]"
    if value is not None:
        new_line = f'{name} = "{value}"'
    def bare(line):  # a table header may carry an inline comment: "[review.prefer]  # ..."
        return line.split("#", 1)[0].strip()

    heads = [i for i, line in enumerate(lines) if bare(line) == header]
    if not heads:
        lines += ["", header, new_line]
    else:
        start = heads[0]
        end = next((i for i in range(start + 1, len(lines)) if bare(lines[i]).startswith("[")), len(lines))
        pattern = re.compile(rf"^{re.escape(name)}\s*=")
        for i in range(start + 1, end):
            if pattern.match(lines[i]):
                lines[i] = new_line
                break
        else:
            lines.insert(start + 1, new_line)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
