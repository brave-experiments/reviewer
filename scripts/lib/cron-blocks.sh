#!/bin/bash
# The crontab blocks this project's scheduled jobs live in.
#
# The marker carries the project name so several deployments can share one
# crontab. It also carries this repo's name.
#
# This repo used to be part of brave-dev-loop, whose crontab blocks used the
# marker "brave-dev-loop (<project>:review)" for the same jobs. Installing here
# strips that review block too, otherwise both repos would run every sweep and
# poll twice. Only the review group is stripped under the old names: the run
# and maintenance blocks belong to brave-dev-loop and are left alone.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/cron-blocks.sh"
#   marker=$(bot_cron_marker "$BOT_PROJECT_NAME" review)
#   cleaned=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" <<< "$existing")

# Current spelling first.
BOT_CRON_MARKER_NAMES=(reviewer)

# Spellings written by brave-dev-loop before the split. Only their review group
# is this repo's to remove.
BOT_CRON_LEGACY_MARKER_NAMES=(brave-dev-loop brave-dev-bot)

# The only group of jobs this repo has.
BOT_CRON_GROUPS=(review)

# The marker to write. Parentheses rather than brackets: the marker has to
# survive pattern matching without escaping. The group, when there is one,
# follows the project name after a colon.
bot_cron_marker() {
  printf '%s (%s%s)\n' "${BOT_CRON_MARKER_NAMES[0]}" "$1" "${2:+:$2}"
}

# Remove one block, by exact marker, from the text on stdin.
_bot_strip_one_block() {
  awk -v start="# === $1 scheduled jobs ===" \
      -v end="# === end $1 ===" \
      '$0==start{skip=1} $0==end{skip=0;next} !skip'
}

# Remove this project's blocks from the crontab on stdin. Fixed-string
# matching, so a project name with regex characters in it is not a special
# case. The optional group is accepted for symmetry with the install and
# remove scripts; there is only one.
bot_strip_cron_blocks() {
  local project="$1" group="${2:-review}" name text
  text=$(cat)
  for name in "${BOT_CRON_MARKER_NAMES[@]}" "${BOT_CRON_LEGACY_MARKER_NAMES[@]}"; do
    text=$(_bot_strip_one_block "$name ($project:$group)" <<< "$text")
  done
  printf '%s\n' "$text"
}

# brave-dev-loop wrote a single ungrouped block before it grouped its jobs, and
# this repo never did, so there is nothing to refuse.
bot_has_ungrouped_cron_block() {
  return 1
}
