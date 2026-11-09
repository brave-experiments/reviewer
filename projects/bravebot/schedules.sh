#!/bin/bash
# bravebot's cron jobs. Sourced by scripts/sync-schedules.sh, which supplies
# the variables and the bot_cron_* helpers (scripts/lib/cron-jobs.sh) and wraps
# what the group prints in a crontab block for this project alone.
#
# This repo only reviews, so there is one group, and it is a function that
# prints its lines: `make schedules` (or `make schedules-review`) installs it.
# The minutes are off the ones brave-core's copy of these jobs uses, because
# both projects can be deployed on one machine.

schedule_review() {
  echo ""
  echo "# Review PRs — skip if no recent open PRs"
  echo "# Gate check runs before git sync to avoid wasted fetches"
  echo "# Three slots, the same count the review-request poll below asks for: a"
  echo "# sweep asking for one would take slot 1 only, and exit doing nothing"
  echo "# every time a poll happened to be holding it. The minute is :30, off"
  echo "# every minute the poll uses and off brave-core's."
  echo "# Times are the machine's local clock, which cron reads; it is on Eastern time."
  echo "# 4x/day, five hours apart from 07:30 to 22:30"
  bot_cron_job "30 7,12,17,22 * * *" "./scripts/check-new-prs.sh" \
    "./scripts/sync-target-repo.sh && $(bot_cron_as_reviewer "$(bot_cron_review review-prs '1d open --auto --reviewer-priority' 3 "$BOT_REVIEW_MODEL")")" \
    "review-prs-cron.log"

  echo ""
  echo "# Review requested from the bot, or an org member pinging it — skip unless someone asked"
  echo "# Gate check runs before git sync to avoid wasted fetches (288 runs/day, most exit early)"
  echo "# A human clicking Request review on a bravebot PR gets an answer within"
  echo "# five minutes, without waiting for the next sweep above."
  echo "# Every 5 min, and runs overlap — a review is much longer than the gap —"
  echo "# bounded by three review-prs slots, with a lock per PR inside the job."
  echo "# Minutes are offset from brave-core's copy of this job, which can be"
  echo "# deployed on the same machine."
  bot_cron_job "3,8,13,18,23,28,33,38,43,48,53,58 * * * *" "./scripts/check-review-requests.sh" \
    "./scripts/sync-target-repo.sh && $(bot_cron_as_reviewer "./scripts/with-lock.sh review-prs --slots 3 --timeout 14400 -- ./scripts/review-requested.sh")" \
    "review-requested-cron.log"
}
