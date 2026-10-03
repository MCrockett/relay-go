#!/usr/bin/env bash
# Link relay's skills into the home volume on every start, so an image update reaches them.
set -euo pipefail
/opt/relay/install.sh >/dev/null
# Let git use gh's login for https remotes, once gh is logged in.
if gh auth status >/dev/null 2>&1; then gh auth setup-git >/dev/null 2>&1 || true; fi
exec "$@"
