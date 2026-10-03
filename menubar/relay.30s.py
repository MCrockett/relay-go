#!/usr/bin/env python3.11
"""SwiftBar: the relay owner inbox, refreshed every thirty seconds."""
import os
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from relaylib import owneractions, status
from relaylib.errors import RelayError
from relaylib.ui.server import page_url, running_info


def clean(text):
    return str(text).replace("|", "¦").replace("\n", " ").replace("\r", " ")


def link(row, info):
    repo, slug = row.get("checkout"), row["feature"]
    if not repo:
        return None
    if info:
        return page_url(info, os.path.realpath(repo), slug)
    try:
        origin = "https://github.com/" + owneractions.github_repo(repo)
        if row.get("pr"):
            return origin + f"/pull/{row['pr']}"
        _, st = owneractions.published(repo, slug)
        return origin + "/tree/" + quote(st["branch"], safe="/") + "/docs/relay/" + quote(slug)
    except (RelayError, OSError):
        return None


def render(rows, info, error=None):
    waiting = [r for r in rows if r["waiting_on_owner"]]
    lines = ["relay ?" if error else f"relay {len(waiting)}" if waiting else "relay ✓", "---"]
    if error:
        lines.append(clean(error))
    elif not waiting:
        lines.append("Nothing waiting on you")
    for row in waiting:
        reason = "; ".join(row.get("flags") or [row.get("status", "unknown")])
        text = clean(f"{row['repo']} / {row['feature']}: {reason}")
        url = link(row, info)
        lines.append(text + (" | href=" + url if url else ""))
    relay = os.path.expanduser("~/.local/bin/relay").replace("'", "'\\''")
    lines += ["---", f"Open relay UI | bash='{relay}' param1=ui param2=--background terminal=false",
              "Refresh | refresh=true"]
    return "\n".join(lines) + "\n"


def output():
    try:
        return render(status.scan(status.projects_root()), running_info())
    except Exception as e:
        return render([], None, str(e))


if __name__ == "__main__":
    print(output(), end="")
