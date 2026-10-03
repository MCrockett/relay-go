# Repository Guidelines

## Project Structure

- [README.md](README.md) for how relay works. `relaylib/` holds the modules, `prompts/` the review prompts, `skills/` the agent skills.

## Build and Test

```bash
python3.11 -m unittest discover -s tests -t . -v
```

## Coding Style

- Python 3.11 standard library only. One module per responsibility in `relaylib/`.
- Plain English in prompts, skills and docs; no em-dashes.

## Testing

- `unittest`, files `tests/test_<module>.py`. Integration tests use temp git repos with a bare `origin` and fake reviewer/gh binaries from `tests/helpers.py`. Never call real Claude or Codex from tests.

## Commits and Pull Requests

- Commits: `<type>: short summary`, types feat, fix, refactor, test, docs, chore, style.
- Branches off `develop`; PRs into `develop`; `main` is release only. The owner merges.
- CI (`.github/workflows/test.yml`) must be green before merge.

## Security

- No secrets in the repo. relay never stores credentials; it uses the owner's existing Claude, Codex and gh logins.
