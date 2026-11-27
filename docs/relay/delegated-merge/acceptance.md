# delegated-merge: acceptance

What waits on the owner or on later features. relay's side is built and tested (R1 to R16); these need people,
other repos or a real GitHub repository, so no test in this repo can close them.

- [ ] **hordle: the `release` role and the change to its hook.** The hook refuses `relay merge` in every role run
  today (hordle foundation spec R28). hordle needs the `release` role, and its hook must allow `relay merge` for
  that role alone. Both go into hordle's `TODO.md` build order and follow by PRs the owner merges.
- [ ] **The first repo's opt-in PR.** The repo adds `[merge] production_branches` to `docs/relay/config.toml` on its
  base branch by a PR the owner merges (the file is protected, so the release role cannot merge it). With that PR
  the owner checks two things on GitHub: that the repo's non-production base branches accept the owner's push (no
  rule that allows only pull requests, which would refuse relay's push, R10), and that its production branches keep
  their protection.
- [ ] **One real delegated merge, seen end to end in that repo.** The release role runs `relay merge`; the owner
  sees the PR comment, the hub log line with `delegated: true`, the kept worktree and the note to the feature's
  session, the remote branch gone, and `relay status` showing the feature done.
- [ ] **The cost of decision 8 accepted.** A repo whose non-production base accepts only pull requests cannot use D1
  until the owner relaxes that rule.
