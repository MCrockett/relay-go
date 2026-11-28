# take-base-branch

Requested through relay dispatch (qmx0hasm1) by claude hub session b98b7951-453c-40f9-81ce-6e74b537d43e, 2026-11-28.

## Request

Bug: relay take / relay new branch from origin/develop whenever it exists, even in repos whose real base is main. It hit hordle (studio-role, worked around with --base main) and UpstreamPartners (read-manifest-join branched from origin/develop ed8627a, 2026-09-24, two months behind main 8465a22; its PR would carry ~18 stale September doc files). Fix: pick the base from the repo's relay config (its production/base branch setting, e.g. production_branches or an explicit base_branch), falling back to origin/HEAD, never just 'develop if present'. relay-go itself does work on develop, so keep that case right via config. Also warn at take/new time when the chosen base is far behind origin/HEAD. Add tests for: repo with stale develop + main default; repo whose base is develop. Small change (--small). One open PR per repo: coordinate with hub-start-session (qmx0h7k1c). Owner merges.

## Owner's words

> yes, dispatch the relay bug
