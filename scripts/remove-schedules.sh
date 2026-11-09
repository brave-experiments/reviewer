#!/bin/bash
# Remove this project's cron jobs -- the inverse of sync-schedules.sh.
#
# Strips the crontab block sync-schedules.sh installed for this project, and the
# review block brave-dev-loop used to write for it (cron-blocks.sh), and leaves
# every other line alone: another project's block, and anything written by hand.
#
#   ./scripts/remove-schedules.sh                    remove the review block
#   ./scripts/remove-schedules.sh --print            print the crontab that would remain, touch nothing

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
    *) echo "Usage: remove-schedules.sh [--print] [--group review|all]" >&2; exit 1 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/cron-blocks.sh"

case "$GROUP" in
  review|all) ;;
  *) echo "Error: unknown group '$GROUP' (this repo only has: review)." >&2; exit 1 ;;
esac

EXISTING=$(crontab -l 2>/dev/null || true)

STRIPPED=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" review <<< "$EXISTING")
CLEANED=$(sed '/^$/N;/^\n$/d' <<< "$STRIPPED")

if $PRINT_ONLY; then
  printf '%s\n' "$CLEANED"
  exit 0
fi

if [ "$CLEANED" = "$(sed '/^$/N;/^\n$/d' <<< "$EXISTING")" ]; then
  echo "No cron jobs installed for $BOT_PROJECT_NAME; crontab left as it was."
  exit 0
fi

# An empty crontab is removed rather than installed as a blank file.
if [ -z "$(tr -d '[:space:]' <<< "$CLEANED")" ]; then
  crontab -r
else
  printf '%s\n' "$CLEANED" | crontab -
fi

echo "Cron jobs removed for $BOT_PROJECT_NAME."
echo "Run 'make schedules' to install them again, or 'crontab -l' for what remains."
