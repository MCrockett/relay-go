#!/usr/bin/env bash
# install.sh: link relay's skills into Claude Code and Codex, and bin/relay into ~/.local/bin.
# Run once per machine, and again after pulling a new skill. Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
link_skills() {  # link_skills <destination dir>
  local DST="$1"; mkdir -p "$DST"
  for d in "$HERE"/skills/*/; do
    n="$(basename "$d")"; [[ -f "$d/SKILL.md" ]] || continue
    if [[ -e "$DST/$n" && ! -L "$DST/$n" ]]; then echo "skip    $n (a real directory exists in $DST)"; continue; fi
    ln -sfn "$(cd "$d" && pwd)" "$DST/$n"; echo "linked  $n -> $DST"
  done
}
link_skills "$HOME/.claude/skills"
link_skills "$HOME/.codex/skills"
mkdir -p "$HOME/.local/bin"
ln -sfn "$HERE/bin/relay" "$HOME/.local/bin/relay"; echo "linked  relay -> ~/.local/bin/relay"

PLUGIN_DIR="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
if [[ -n "$PLUGIN_DIR" && -d "$PLUGIN_DIR" ]]; then
  if [[ -e "$PLUGIN_DIR/relay.30s.py" && ! -L "$PLUGIN_DIR/relay.30s.py" ]]; then
    echo "skip    relay.30s.py (a real file exists in $PLUGIN_DIR)"
  else
    ln -sfn "$HERE/menubar/relay.30s.py" "$PLUGIN_DIR/relay.30s.py"
    echo "linked  relay.30s.py -> $PLUGIN_DIR"
  fi
else
  echo "SwiftBar: choose a plugin folder in SwiftBar settings (PluginDirectory), then run ./install.sh again."
fi
cat <<'SNIPPET'
Claude usage capture: with the owner's approval, add this after input=$(cat); in the status-line command:
printf '%s' "$input" | ~/.local/bin/relay usage-snapshot claude >/dev/null 2>&1 &
Keep the rest of the status-line command unchanged. This installer does not edit Claude settings.
SNIPPET
