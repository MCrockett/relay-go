"""Who is running relay: claude, codex, or the owner. Never guesses."""
import socket
import uuid
from dataclasses import dataclass

from .config import PROVIDERS
from .errors import RelayError

CLAUDE_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID")
CODEX_MARKERS = ("CODEX_SESSION_ID", "CODEX_THREAD_ID")
AGENT_MARKERS = CLAUDE_MARKERS + CODEX_MARKERS + ("RELAY_PROVIDER",)
STRIP_FOR_CHILD = CLAUDE_MARKERS + CODEX_MARKERS + (
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION", "RELAY_PROVIDER", "RELAY_SESSION")


@dataclass(frozen=True)
class Identity:
    provider: str  # claude | codex | owner
    session: str


def _session_for(provider, env):
    if env.get("RELAY_SESSION"):
        return env["RELAY_SESSION"]
    if provider == "owner":
        return "owner@" + socket.gethostname()
    if provider == "claude":
        sid = env.get("CLAUDE_CODE_SESSION_ID")
    else:
        sid = env.get("CODEX_SESSION_ID") or env.get("CODEX_THREAD_ID")
    if not sid:
        raise RelayError(f"no session id for {provider}; set RELAY_SESSION")
    return sid


def detect(env, by=None):
    if by:
        if by not in PROVIDERS + ("owner",):
            raise RelayError(f"--by must be claude, codex or owner, not {by!r}")
        return Identity(by, _session_for(by, env))
    if env.get("RELAY_PROVIDER"):
        return detect(env, env["RELAY_PROVIDER"])
    has_claude = any(env.get(k) for k in CLAUDE_MARKERS)
    has_codex = any(env.get(k) for k in CODEX_MARKERS)
    if has_claude and not has_codex:
        return Identity("claude", _session_for("claude", env))
    if has_codex and not has_claude:
        return Identity("codex", _session_for("codex", env))
    what = "both Claude and Codex" if has_claude else "no agent"
    raise RelayError(f"found {what} markers in the environment; pass --by claude|codex|owner")


def in_agent_session(env):
    return any(env.get(k) for k in AGENT_MARKERS)


def require_owner_terminal(env, what):
    if in_agent_session(env):
        raise RelayError(f"{what} is owner-only: the owner runs it in their own terminal. An agent may run it with "
                         "--relayed, and only when the owner told it to in this conversation")


def child_env(env, provider):
    out = {k: v for k, v in env.items() if k not in STRIP_FOR_CHILD}
    out["RELAY_PROVIDER"] = provider
    out["RELAY_SESSION"] = f"review-{uuid.uuid4()}"
    return out
