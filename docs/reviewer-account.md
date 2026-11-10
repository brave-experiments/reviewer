# Reviewer account

The one GitHub account every scheduled job in this repo acts as. There is no other identity here: nothing commits, pushes or signs.

## Why a separate account

GitHub does not let an account approve its own pull request. The reviewer must therefore be a different account from the authors of the PRs it reviews, and it is not the account of the person who owns the machine either, so that its login never mixes with theirs.

The reviewer is required. `reviewer.username` in `config.json` is validated when the config is loaded, and every scheduled job stops with an error if it is unset.

## Setup

`make setup` asks for the reviewer's username, saves it as `reviewer.username`, and walks you through the login:

```bash
GH_CONFIG_DIR=~/.config/gh-<reviewer> gh auth login --hostname github.com --web --skip-ssh-key
```

Setup offers to run that for you. Sign in to github.com as the reviewer first (a private window if you are signed in as someone else); gh prints a one-time code to enter at github.com/login/device. Setup then checks that the login resolves to the reviewer and reports the reviewer's access to the PR repository.

## Write access or triage

- Approving needs only triage, but approvals count toward required reviews only from an account with write access.
- Resolving a review thread needs write access (or authorship of the PR). Without it every re-review ends in "request changes" over threads the code already answers.

A reviewer with write access could also push and merge, which is why the scheduled agent session never holds its token (see below).

## The gh config directory

The login lives in its own gh config directory, `~/.config/gh-<reviewer>` by default (`reviewer.ghConfigDir` overrides it). It never enters your own gh config, and nothing global changes: `~/.gitconfig` and `gh`'s active account are left alone.

## `scripts/as-reviewer.sh`

Every scheduled job's cron line goes through `scripts/as-reviewer.sh`, which reads `config.json` when the job fires. It sets `GH_TOKEN` to the reviewer's token as well as `GH_CONFIG_DIR`: a token in the environment outranks a config directory, so pinning the directory alone would not be enough.

There is no fallback. If `reviewer.username` is unset, the directory has no login, or it is logged in as a different account, the wrapper stops with an error in the job's log. It never runs the command as whatever account the environment happens to hold.

`prepare-review.py` and `collect-results.py` re-run themselves through the wrapper when the gh login in force is not the reviewer, so a `/review-prs` typed into a session reviews as the reviewer too. A review posted by the PR's author can only comment, and it caches the commit as reviewed, so no later run would approve it.

To run any command by hand as the reviewer:

```bash
./scripts/as-reviewer.sh -- <command>
```

## The agent session never holds the token

`scripts/review-session.sh` runs the two scripts that talk to GitHub (`prepare-review.py` and `collect-results.py`) itself, as the reviewer. The agent session in between reads the PR with no token and an empty `GH_CONFIG_DIR`; see [skills.md](skills.md) and [review-agents.md](review-agents.md).

Review requests are polled for the reviewer: a human asks for a review from the account that will answer it.

## `.envrc`

`make setup` generates `.envrc`, which only sources `scripts/lib/load-config.sh`. Every cron line sources it, and a missing or failing `.envrc` cancels the job. It sets no git or gh identity.
