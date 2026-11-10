#!/bin/bash
# Idempotent cron job setup for reviewer.
#
# The jobs are per-project: this script builds the crontab block, but what goes
# in it comes from projects/<profile>/schedules.sh (projects/default/ when the
# profile has no file of its own). Schedule changes are made there and
# committed to source control.
#
#   ./scripts/sync-schedules.sh                    install/update the review block
#   ./scripts/sync-schedules.sh --print            print the block, touch no crontab
#
# The inverse is remove-schedules.sh.
#
# Installing also removes the review block brave-dev-loop used to write for the
# same project (cron-blocks.sh), so the sweeps and polls do not run twice.

set -e

PRINT_ONLY=false
GROUP=review
while [ $# -gt 0 ]; do
  case "$1" in
    --print) PRINT_ONLY=true ;;
    --group)
      [ $# -ge 2 ] || { echo "Error: --group needs a value (review or all)." >&2; exit 1; }
      GROUP="$2"
      shift
      ;;
    *) echo "Usage: sync-schedules.sh [--print] [--group review|all]" >&2; exit 1 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/cron-blocks.sh"
source "$SCRIPT_DIR/lib/cron-jobs.sh"

case "$GROUP" in
  review|all) ;;
  *) echo "Error: unknown group '$GROUP' (this repo only has: review)." >&2; exit 1 ;;
esac

LOG_DIR="$PROJECT_ROOT/logs"
$PRINT_ONLY || mkdir -p "$LOG_DIR"

# Cron's PATH is not this shell's, so the agent binary's directory is resolved
# here and put on the block's PATH.
CLAUDE_BIN_DIR=$(dirname "$REVIEWER_CLAUDE_BIN")

# Which agent the review jobs drive, read from config.json when the schedules
# are installed (not from REVIEWER_AGENT in this shell: an environment
# override on the machine running `make schedules` is not the deployment's
# setting). Read again when each job fires, so it needs only the PATH here.
REVIEW_AGENT=$(bot_config '.reviewer.agent')
REVIEW_AGENT="${REVIEW_AGENT:-claude}"

# The binary an agent runs from, as an absolute path. A bravebot installed under
# nvm is invisible to cron's PATH, so the path is resolved here.
resolve_agent_bin() {
  local bin
  case "$1" in
    claude) bin="$REVIEWER_CLAUDE_BIN" ;;
    bravebot) bin="$REVIEWER_BRAVEBOT_BIN" ;;
    *) echo "Error: unsupported agent '$1' (expected: claude | bravebot)" >&2; return 1 ;;
  esac
  if [[ "$bin" != /* ]]; then
    bin=$(command -v "$bin") || {
      echo "Error: the $1 binary is not on PATH; set it in config.json (reviewer.${1}Bin) or run 'make setup'." >&2
      return 1
    }
  fi
  printf '%s\n' "$bin"
}

PATH_PREFIX="$CLAUDE_BIN_DIR"
if [ "$REVIEW_AGENT" != "claude" ]; then
  REVIEW_AGENT_BIN=$(resolve_agent_bin "$REVIEW_AGENT") || exit 1
  REVIEW_AGENT_DIR=$(dirname "$REVIEW_AGENT_BIN")
  [ "$REVIEW_AGENT_DIR" = "$CLAUDE_BIN_DIR" ] || PATH_PREFIX="$PATH_PREFIX:$REVIEW_AGENT_DIR"
fi

# Resolve sync repo path from config (optional — only for projects that sync from upstream)
SYNC_REPO_ENABLED=$(bot_config_bool '.schedules.syncRepo')
SYNC_REPO_PATH=$(bot_config '.schedules.syncRepoPath')

# This repo's own default branch (for cron git checkout/reset)
BOT_REPO_BRANCH=$(bot_config '.project.botRepoBranch')
BOT_REPO_BRANCH="${BOT_REPO_BRANCH:-main}"

# This project's jobs. A profile without a schedules.sh gets the generic set,
# so adding a project is still just a profile directory.
SCHEDULE_FILE="$BOT_PROFILE_DIR/schedules.sh"
if [ ! -f "$SCHEDULE_FILE" ]; then
  SCHEDULE_FILE="$PROJECT_ROOT/projects/default/schedules.sh"
fi
if [ ! -f "$SCHEDULE_FILE" ]; then
  echo "Error: no schedules.sh for profile '$BOT_PROFILE', and no default one either." >&2
  exit 1
fi

# The jobs file defines schedule_review, which prints the crontab lines.
source "$SCHEDULE_FILE"

if ! declare -F schedule_review >/dev/null; then
  echo "Error: ${SCHEDULE_FILE#$PROJECT_ROOT/} defines no review jobs for $BOT_PROJECT_NAME." >&2
  exit 1
fi

# The marker carries the project name and the group, so several deployments can
# share one crontab: installing one block leaves every other block alone.
# cron-blocks.sh owns the spelling.
MARKER=$(bot_cron_marker "$BOT_PROJECT_NAME" review)
JOBS=$(schedule_review)
BLOCK=$(cat <<BLOCK
# === $MARKER scheduled jobs ===
# Managed by sync-schedules.sh - do not edit manually
# Jobs: ${SCHEDULE_FILE#$PROJECT_ROOT/}
SHELL=/bin/bash
PATH=$PATH_PREFIX:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
$JOBS

# === end $MARKER ===
BLOCK
)

if $PRINT_ONLY; then
  echo "$BLOCK"
  exit 0
fi

EXISTING=$(crontab -l 2>/dev/null || true)

# Strip the previous block for this project, including the one brave-dev-loop
# wrote for the same jobs, which would otherwise stay installed and run a
# second copy of every sweep and poll. Another project's block is not this
# project's to touch, and is left where it is.
CLEANED=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" review <<< "$EXISTING")

# Combine preserved entries with the new block
NEW_CRONTAB=$(printf '%s\n%s\n' "$CLEANED" "$BLOCK" | sed '/^$/N;/^\n$/d')

echo "$NEW_CRONTAB" | crontab -

echo "Cron jobs installed for $BOT_PROJECT_NAME (review; ${SCHEDULE_FILE#$PROJECT_ROOT/})."
echo ""
echo "$BLOCK"
echo ""
echo "Run 'make view-schedules' for a summary, or 'crontab -l' for every project's block."
