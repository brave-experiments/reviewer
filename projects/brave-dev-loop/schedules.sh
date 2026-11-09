#!/bin/bash
# The cron jobs for reviewing brave-dev-loop's own pull requests. Sourced by
# scripts/sync-schedules.sh, which supplies the variables and the bot_cron_*
# helpers (scripts/lib/cron-jobs.sh).
#
# No sync-target-repo.sh in front of the command, unlike every other
# project's. That script moves a fork's default branch onto upstream's, and
# brave-dev-loop has no fork and no upstream remote, so it would exit having
# done nothing.
#
# The hour dodges the other two deployments a machine can be running:
# bravebot's reviews start at :30 and brave-core's on the hour.

schedule_review() {
  echo ""
  echo "# Review PRs — skip if no recent open PRs"
  echo "# Daily: once, late, after the day's human pull requests have landed"
  bot_cron_job "30 21 * * *" "./scripts/check-new-prs.sh" \
    "$(bot_cron_as_reviewer "$(bot_cron_review review-prs '1d open --auto --reviewer-priority' '' "$BOT_REVIEW_MODEL")")" \
    "review-prs-cron.log"
}
