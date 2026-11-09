#!/bin/bash
# The cron jobs a project gets when its profile defines none of its own.
# Sourced by scripts/sync-schedules.sh, which supplies the variables and the
# bot_cron_* helpers (scripts/lib/cron-jobs.sh) and wraps what the group
# prints in a crontab block for this project alone.
#
# A project that wants different jobs, or the same jobs at different hours,
# copies this file to projects/<name>/schedules.sh.

schedule_review() {
  echo ""
  echo "# Review PRs — skip if no recent open PRs"
  echo "# Gate check runs before git sync to avoid wasted fetches"
  echo "# Weekdays: 2x/day"
  bot_cron_job "0 13,20 * * 1-5" "./scripts/check-new-prs.sh" \
    "./scripts/sync-target-repo.sh && $(bot_cron_as_reviewer "$(bot_cron_review review-prs '1d open --auto --reviewer-priority' '' "$BOT_REVIEW_MODEL")")" \
    "review-prs-cron.log"
  echo "# Weekends: once/day at noon"
  bot_cron_job "0 12 * * 0,6" "./scripts/check-new-prs.sh" \
    "./scripts/sync-target-repo.sh && $(bot_cron_as_reviewer "$(bot_cron_review review-prs '1d open --auto --reviewer-priority' '' "$BOT_REVIEW_MODEL")")" \
    "review-prs-cron.log"
}
