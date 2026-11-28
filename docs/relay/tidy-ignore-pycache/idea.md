# tidy-ignore-pycache

Requested through relay dispatch (qmwzwfbzj) by claude hub session b98b7951-453c-40f9-81ce-6e74b537d43e, 2026-11-27.

## Request

relay merge cleanup: untracked __pycache__ (and .pytest_cache) folders should not stop relay from removing a merged feature's worktree and branch. It kept them on relay-go #43, #44, #45 and hordle #30, #31 ("it has files git does not hold"). Treat Python cache dirs as removable; keep refusing for any other untracked file (e.g. kpi/morning.json in UpstreamPartners worktrees should still be reported, not silently deleted). Small change (--small). Owner merges.

## Owner's words

> dispatch the data-paths PR to UpstreamPartners, then cleanup
