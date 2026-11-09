# reviewer

Automated pull request review. It checks open PRs in a repository against that repository's documented best practices and posts inline comments, resolves threads that were addressed, approves PRs with nothing left to fix, and answers review requests and `@`-pings.

This was split out of [brave-dev-loop](https://github.com/brave-experiments/brave-dev-loop), which keeps the story-implementation loop (`run.sh`). The two are separate checkouts with separate `config.json` files and separate crontabs.

## How a review works

`/review-prs` (a Claude Code skill, `.claude/skills/review-prs/`) is a file-based pipeline that keeps the model out of everything a script can do:

1. `prepare-review.py` fetches PRs, diffs and comments, makes a git worktree per PR head, and writes one detect prompt per rule chunk plus one for bugs.
2. Detect subagents (Sonnet, `.claude/agents/review-prs-detect.md`) write candidate findings.
3. `select-candidates.py` drops the ones that would be dropped anyway and writes one validate prompt per PR.
4. Validate subagents (Opus, `.claude/agents/review-prs-validate.md`) investigate each candidate against the PR's source and keep only what they can prove.
5. `collect-results.py` posts, resolves, approves, updates the cache and notifies.

`scripts/review-session.sh` drives the whole thing unattended, under a fixed permission set (`scripts/review-session-settings.py`). See `.claude/skills/review-prs/SKILL.md` for the details, and `docs/`:

- `review-agents.md`: running the reviews with `claude` or `bravebot`
- `pr-labels.md`: the labels a first review applies
- `ping-replies.md`: answering `@`-pings
- `bot-identity.md`: which GitHub account does what
- `pr-descriptions.md`: what a PR body must look like

Per-project rules (labels, UI paths, extra review guidance) live in `projects/<profile>/profile.json`; see `projects/README.md`.

## Setup

```sh
make setup       # wizard: config.json, .envrc, org-members cache, .venv, reviewer gh login, cron jobs
```

Prerequisites: `gh`, `jq`, `python3`, `git`, and either `claude` or `bravebot`. You also need a local clone of the repository whose PRs are reviewed (`project.targetRepoPath`); its `docs/best-practices/` are the rules PRs are checked against.

`config.json` is gitignored; `config.example.json` shows every key the review side reads. The reviewer is a separate GitHub account (`reviewer.username`) whose `gh` login lives in its own config directory, so it never touches yours.

## Use

```sh
./scripts/review-session.sh -- 1d open --auto     # one sweep of PRs from the last day
./scripts/review-session.sh -- '#1234'            # one PR
make schedules                                    # install the cron jobs for the profile
make view-schedules                               # show what is installed
make remove-schedules                             # take them out
```

## Develop

```sh
make test      # pytest
make lint      # ruff
make check     # lint + tests + the security-action scan (make check-reviewdog)
```

## Moving from brave-dev-loop

The review cron blocks that brave-dev-loop installed (`brave-dev-loop` or `brave-dev-bot` marker) are removed when this repo installs its own with `make schedules`, so a job never runs twice. Copy `config.json` values across (`project.*`, `bot.*`, `reviewer.*`, `bestPractices.*`) and copy `.ignore/` (the review cache lives there) to keep the per-PR review history.
