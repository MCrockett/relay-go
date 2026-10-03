"""Review prompts: prompts/<name>.md + prompts/_verdict.md + the owner's rules (global, then the project's)."""
import os
from string import Template

from .config import RELAY_HOME_DIR, relay_home
from .state import relay_dir

PROMPT_DIR = os.path.join(RELAY_HOME_DIR, "prompts")
GLOBAL_HEADER = ("# Owner rules\n\nJudgment rules for every relay stage and review, in every project. One line each: "
                 "date, rule, reason. Add with `relay rule --global`.\n\n")
PROJECT_HEADER = ("# Project rules\n\nJudgment rules for this project's relay reviews, applied after the owner's "
                  "global rules. Add with `relay rule` inside the project.\n\n")


def global_rules_path():
    return os.path.join(relay_home(), "RULES.md")


def project_rules_path(root):
    return os.path.join(relay_dir(root), "RULES.md")


def rules_text(root=None):
    """The rules in effect: the owner's global rules, then this project's, each only when it has content."""
    parts = []
    for title, path in (("Global rules", global_rules_path()),
                        ("Project rules", project_rules_path(root) if root else None)):
        if path and os.path.exists(path):
            with open(path) as f:
                body = f.read().strip()
            if body:
                parts.append(f"### {title}\n\n{body}")
    return "\n\n".join(parts)


def render(name, ctx, root=None):
    parts = []
    for fname in (f"{name}.md", "_verdict.md"):
        with open(os.path.join(PROMPT_DIR, fname)) as f:
            parts.append(Template(f.read()).safe_substitute({k: str(v) for k, v in ctx.items()}))
    rules = rules_text(root)
    if rules:
        parts.append("## Owner rules (apply these)\n\n" + rules)
    return "\n\n".join(parts)
