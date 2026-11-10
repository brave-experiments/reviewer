#!/bin/bash
# Run a command as the reviewer account, the one GitHub account every scheduled
# job acts as (see docs/reviewer-account.md).
#
# Usage: as-reviewer.sh [--] <command...>
#
# The reviewer (reviewer.username in config.json) is required. This either runs
# the command as the reviewer or stops: a reviewer whose login is missing or
# belongs to someone else is an error, never a quiet fallback to whatever account
# the environment happens to hold.
#
# gh is pinned with GH_TOKEN, not just GH_CONFIG_DIR. GH_TOKEN outranks the config
# directory, so a token already in the environment would win over a bare
# GH_CONFIG_DIR. Claude Code's env settings can also overwrite GH_CONFIG_DIR inside
# the session, but not the token.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

[ "${1:-}" = "--" ] && shift
if [ $# -eq 0 ]; then
  echo "Usage: as-reviewer.sh [--] <command...>" >&2
  exit 2
fi

if [ -z "${REVIEWER_USERNAME:-}" ]; then
  echo "Error: reviewer.username is not set in config.json. Run 'make setup'." >&2
  exit 1
fi

# A clean environment for the lookups: an inherited token would be read back as
# the reviewer's.
gh_as_reviewer() {
  env -u GH_TOKEN -u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN \
    GH_CONFIG_DIR="$REVIEWER_GH_CONFIG_DIR" gh "$@"
}

if ! token=$(gh_as_reviewer auth token --hostname github.com 2>/dev/null) || [ -z "$token" ]; then
  echo "Error: reviewer '$REVIEWER_USERNAME' has no gh login in $REVIEWER_GH_CONFIG_DIR." >&2
  echo "  Run 'make setup' to log it in." >&2
  exit 1
fi

login=$(GH_TOKEN="$token" gh api user --jq .login 2>/dev/null || true)
if [ "$(tr '[:upper:]' '[:lower:]' <<< "$login")" != "$(tr '[:upper:]' '[:lower:]' <<< "$REVIEWER_USERNAME")" ]; then
  echo "Error: the gh login in $REVIEWER_GH_CONFIG_DIR resolves to '${login:-nobody}', not '$REVIEWER_USERNAME'." >&2
  echo "  Run 'make setup' to log the right account in." >&2
  exit 1
fi

unset GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN
export GH_CONFIG_DIR="$REVIEWER_GH_CONFIG_DIR" GH_TOKEN="$token"
exec "$@"
