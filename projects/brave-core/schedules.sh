#!/bin/bash
# brave-core's cron jobs. Sourced by scripts/sync-schedules.sh, which supplies
# the variables and the bot_cron_* helpers (scripts/lib/cron-jobs.sh) and wraps
# what the group prints in a crontab block for this project alone.
#
# Every job is gated: the gate script exits non-zero when there is nothing to
# do, before any fetch, so an idle day costs nothing. Weekday schedules (1-5 =
# Mon-Fri) run more often than weekend ones (0,6 = Sat-Sun).
#
# This repo only reviews, so there is one group, and it is a function that
# prints its lines: `make schedules` (or `make schedules-review`) installs it.

schedule_review() {
  echo ""
  echo "# Review PRs — skip if no recent open PRs"
  echo "# Gate check runs before git sync to avoid wasted fetches"
  echo "# Three slots, the same count the review-request poll below asks for: a"
  echo "# sweep asking for one would take slot 1 only, and exit doing nothing"
  echo "# every time a poll happened to be holding it."
  echo "# Weekdays: 2x/day"
  bot_cron_job "0 13,20 * * 1-5" "./scripts/check-new-prs.sh" \
    "./scripts/sync-target-repo.sh && $(bot_cron_as_reviewer "$(bot_cron_review review-prs '1d open --auto --reviewer-priority' 3 "$BOT_REVIEW_MODEL")")" \
    "review-prs-cron.log"
  echo "# Weekends: once/day at noon"
  bot_cron_job "0 12 * * 0,6" "./scripts/check-new-prs.sh" \
    "./scripts/sync-target-repo.sh && $(bot_cron_as_reviewer "$(bot_cron_review review-prs '1d open --auto --reviewer-priority' 3 "$BOT_REVIEW_MODEL")")" \
    "review-prs-cron.log"

  echo ""
  echo "# Review requested from the bot, or an org member pinging it — skip unless someone asked"
  echo "# Gate check runs before git sync to avoid wasted fetches (288 runs/day, most exit early)"
  echo "# Every 5 min, on minutes no other job here uses. A review runs for far"
  echo "# longer than the gap between polls, so runs overlap rather than waiting:"
  echo "# three review-prs slots, shared with the sweep above, and a lock per PR"
  echo "# inside the job so two runs never review the same one."
  bot_cron_job "4,9,14,19,24,29,34,39,44,49,54,59 * * * *" "./scripts/check-review-requests.sh" \
    "./scripts/sync-target-repo.sh && $(bot_cron_as_reviewer "./scripts/with-lock.sh review-prs --slots 3 --timeout 14400 -- ./scripts/review-requested.sh")" \
    "review-requested-cron.log"
}
