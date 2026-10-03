"""A desktop notification when a feature needs the owner. macOS only; silent elsewhere; never fails a command."""
import os
import shutil
import subprocess
import sys


def _quote(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def send(cfg, title, message):
    """Returns True if a notification was handed to the system."""
    if not (cfg.get("notify") or {}).get("enabled", True):
        return False
    binary = os.environ.get("RELAY_NOTIFY_BIN") or (shutil.which("osascript") if sys.platform == "darwin" else None)
    if not binary:
        return False
    script = f'display notification "{_quote(message)}" with title "{_quote(title)}"'
    try:
        return subprocess.run([binary, "-e", script], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
