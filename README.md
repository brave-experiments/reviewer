# reviewer

Automated pull request review. It checks open PRs in a repository against that repository's documented best practices and posts inline comments, resolves threads that were addressed, approves PRs with nothing left to fix, and answers review requests and `@`-pings.

It runs as a dedicated GitHub reviewer account, with its gh login kept apart from yours. The agent that does the reading is configurable: `claude` or `bravebot` (`reviewer.agent`).

## How a review works

The `review-prs` skill (`.claude/skills/review-prs/`) is a file-based pipeline that keeps the model out of everything a script can do. The skill and subagent definitions sit under `.claude/` because that is the layout both supported agents load them from; nothing in the pipeline depends on which agent runs it.

1. `prepare-review.py` fetches PRs, diffs and comments, makes a git worktree per PR head, and writes one detect prompt per rule chunk plus one for bugs.
2. Detect subagents (`.claude/agents/review-prs-detect.md`) write candidate findings.
3. `select-candidates.py` drops the ones that would be dropped anyway and writes one validate prompt per PR.
4. Validate subagents (`.claude/agents/review-prs-validate.md`) investigate each candidate against the PR's source and keep only what they can prove.
5. `collect-results.py` posts, resolves, approves and updates the cache.

`scripts/review-session.sh` drives the whole thing unattended. The agent session holds no GitHub token and runs under a fixed permission set (`scripts/review-session-settings.py`); `prepare-review.py` and `collect-results.py` run outside it and do the talking to GitHub. See `.claude/skills/review-prs/SKILL.md` for the details.

## Agents

`reviewer.agent` in `config.json` picks the agent for the scheduled sweeps, the review-request poll and the ping replies. `make setup` asks for it.

| Value | Status |
| --- | --- |
| `claude` (default) | Confined by an allowlist (`--permission-mode dontAsk`). |
| `bravebot` | Runs, but is confined differently and less tightly. |

Other agents (codex, cursor) have no confined headless mode wired in. [docs/review-agents.md](docs/review-agents.md) says how each is confined and what is weaker with bravebot.

## Docs

- [docs/reviewer-account.md](docs/reviewer-account.md): the reviewer GitHub account and its login
- [docs/review-agents.md](docs/review-agents.md): the supported agents and how each is confined
- [docs/pr-labels.md](docs/pr-labels.md): the labels a first review applies
- [docs/ping-replies.md](docs/ping-replies.md): answering `@`-pings
- [docs/pr-descriptions.md](docs/pr-descriptions.md): what a PR body must look like

Per-project rules (labels, UI paths, extra review guidance) live in `projects/<profile>/profile.json`; see `projects/README.md`.

## Setup

```sh
make setup       # wizard: config.json, .envrc, org-members cache, .venv, reviewer gh login, cron jobs
```

Prerequisites: `gh`, `jq`, `python3`, `git`, and either `claude` or `bravebot`. You also need a local clone of the repository whose PRs are reviewed (`project.targetRepoPath`); its `docs/best-practices/` are the rules PRs are checked against.

`config.json` is gitignored; `config.example.json` shows every key. The reviewer is a separate GitHub account (`reviewer.username`) whose `gh` login lives in its own config directory, so it never touches yours. The account needs write access to the reviewed repository for its approvals to count.

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

The review cron blocks that brave-dev-loop installed (`brave-dev-loop` or `brave-dev-bot` marker) are removed when this repo installs its own with `make schedules`, so a job never runs twice. Run `make setup` here and give it the same project answers and the reviewer account; copy `.ignore/` across (the review cache lives there) to keep the per-PR review history.
