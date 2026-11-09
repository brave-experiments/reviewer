#!/bin/bash
# Helpers for writing a project's cron jobs.
#
# The jobs themselves live in projects/<profile>/schedules.sh, one file per
# project, and every line in them is built from these functions so that every
# job gets the same prologue: the bot directory, the bot identity, its gate, and
# a hard reset of this repo to the branch the jobs run from.
#
# sync-schedules.sh sources this file and sets the variables the functions
# read before it sources the profile's schedules.sh:
#
#   PROJECT_ROOT      absolute path of this directory
#   BOT_REPO_BRANCH   branch the jobs check out and reset to
#   LOG_DIR           where job output is appended

# One crontab line.
#
#   bot_cron_job "<schedule>" "<gate>" "<command>" "<log file name>"
#
# The gate is a script that exits non-zero when there is nothing to do; it runs
# before the git sync so a job with no work costs no fetches. Pass "" for a job
# that always runs.
#
# The gate stays outside the `{ ...; }` group so a routine "nothing to do" skip
# stays quiet: its stdout goes nowhere. Its stderr goes to the job's log, so a
# gate that skipped because it could not get an answer -- gh down, rate limited,
# signed out -- says so there. Everything after it -- the git sync and the
# command itself -- goes inside, so the redirect captures the WHOLE chain rather
# than only its last command. Without the group, a failure in `git fetch` or
# `git checkout` breaks the `&&` chain before reaching the redirected command,
# and the job dies with nothing in its log at all: cron mails the output into
# the void and the operator sees a job that simply stopped running.
bot_cron_job() {
  local schedule="$1" gate="$2" command="$3" log="$4"
  local line="$schedule cd $PROJECT_ROOT && source .envrc"
  [ -n "$gate" ] && line="$line && $gate 2>> $LOG_DIR/$log"
  line="$line && { git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH"
  printf '%s && %s ; } >> %s/%s 2>&1\n' "$line" "$command" "$LOG_DIR" "$log"
}

# The command for a scheduled /review-prs sweep: scripts/review-session.sh,
# which runs prepare-review.py and collect-results.py itself and starts the
# agent session between them with no GitHub token and narrow permissions.
# Held under a named lock so two schedules never run the same job twice over
# one directory.
#
#   bot_cron_review <lock name> '<prepare-review.py args>' [slots] [model]
#
# `slots` makes the lock a counting semaphore instead of a single instance.
# Every job sharing a lock name has to ask for the same count: a job that asks
# for one takes slot 1 only, so it exits doing nothing whenever a job asking
# for three happens to hold that slot. Pass '' for one instance when a model
# follows. `model` is passed as `--model`.
bot_cron_review() {
  local slots="${3:-}" model="${4:-}"
  printf "./scripts/with-lock.sh %s%s -- ./scripts/review-session.sh%s -- %s" \
    "$1" "${slots:+ --slots $slots}" "${model:+ --model $model}" "$2"
}

# Wrap a command so it runs as the reviewer account (scripts/as-reviewer.sh).
# Always emitted, configured or not -- the wrapper reads config.json when the
# job fires, so adding a reviewer later takes effect without re-syncing cron.
#
#   bot_cron_as_reviewer '<command>'
bot_cron_as_reviewer() {
  printf './scripts/as-reviewer.sh -- %s' "$1"
}
