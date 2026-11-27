# test-runs-outside-git

Local test runs check out their worktree outside the git dir, so tools that deny **/.git/** (Vite, Vitest jsdom) can load the tests
