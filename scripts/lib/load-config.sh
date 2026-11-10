#!/bin/bash
# Shared config loader for reviewer shell scripts.
#
# Usage (from any script):
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/load-config.sh"
#
# Exports:
#   BOT_PROJECT_NAME, BOT_ORG, BOT_PR_REPO, BOT_ISSUE_REPO, BOT_DEFAULT_BRANCH,
#   BOT_PROFILE, BOT_PROFILE_DIR,
#   REVIEWER_USERNAME (reviewer.username, required: the one GitHub account the
#   scheduled jobs act as), REVIEWER_GH_CONFIG_DIR (its gh config directory),
#   REVIEWER_AGENT (reviewer.agent: claude | bravebot; the agent scheduled reviews run),
#   REVIEWER_CLAUDE_BIN, REVIEWER_BRAVEBOT_BIN, REVIEWER_BRAVEBOT_MODEL,
#   BOT_REVIEW_MODEL, BOT_AGENT_TOOLS, BOT_BP_DOCS_DIR,
#   BOT_TARGET_REPO_DIR (absolute; prefer over the raw BOT_TARGET_REPO_PATH)
#
# Also provides:
#   bot_config '.some.jq.path'  — raw jq query against the config file

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

BOT_CONFIG_FILE="$BOT_DIR/config.json"
if [ ! -f "$BOT_CONFIG_FILE" ]; then
  BOT_CONFIG_FILE="$BOT_DIR/config.example.json"
fi

if [ ! -f "$BOT_CONFIG_FILE" ]; then
  echo "Error: No config.json or config.example.json found in $BOT_DIR" >&2
  return 1 2>/dev/null || exit 1
fi

bot_config() {
  jq -r "$1 // empty" "$BOT_CONFIG_FILE"
}

# Boolean-safe read. jq's `//` yields the right-hand side when the left is
# null *or false*, so bot_config on a `false` value returns empty and every
# caller falls through to its default — silently inverting the setting.
# Prints "true"/"false", or nothing when the key is genuinely absent.
bot_config_bool() {
  jq -r "if $1 == null then empty else ($1 | tostring) end" "$BOT_CONFIG_FILE"
}

# Resolve bestPractices.docsDir to an absolute directory. Stored relative to
# this directory, but parent-relative values exist in the wild, so both bases
# are tried.
resolve_docs_dir() {
  local path="$1"
  [ -z "$path" ] && return 0

  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
    return 0
  fi

  local bot_base parent_base
  bot_base="$BOT_DIR/$path"
  parent_base="$(dirname "$BOT_DIR")/$path"

  if [ -d "$bot_base" ]; then
    (cd "$bot_base" && pwd)
  elif [ -d "$parent_base" ]; then
    (cd "$parent_base" && pwd)
  else
    printf '%s\n' "$bot_base"
  fi
}

# Resolve project.targetRepoPath to an absolute directory.
#
# The path may be relative to this directory or to its *parent* ("src/brave"
# versus "../src/brave"). Both spellings are in the wild, so try both bases and
# use whichever one is actually a git repo. Absolute paths are used as-is.
resolve_target_repo() {
  local path="$1"
  [ -z "$path" ] && return 0

  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
    return 0
  fi

  local bot_base parent_base
  bot_base="$BOT_DIR/$path"
  parent_base="$(dirname "$BOT_DIR")/$path"

  if [ -e "$bot_base/.git" ]; then
    (cd "$bot_base" && pwd)
  elif [ -e "$parent_base/.git" ]; then
    (cd "$parent_base" && pwd)
  else
    # Neither exists — return the documented base so errors name a sane path.
    printf '%s\n' "$bot_base"
  fi
}

BOT_PROJECT_NAME=$(bot_config '.project.name')
BOT_ORG=$(bot_config '.project.org')
BOT_PR_REPO=$(bot_config '.project.prRepository')
BOT_ISSUE_REPO=$(bot_config '.project.issueRepository')
BOT_DEFAULT_BRANCH=$(bot_config '.project.defaultBranch')
BOT_TARGET_REPO_PATH=$(bot_config '.project.targetRepoPath')
# Which projects/<name>/ supplies the review rules. Absent means the generic one.
BOT_PROFILE_CONFIGURED=$(bot_config '.project.profile')
BOT_PROFILE="${BOT_PROFILE_CONFIGURED:-default}"
BOT_PROFILE_DIR="$BOT_DIR/projects/$BOT_PROFILE"

# The account every scheduled job acts as (scripts/as-reviewer.sh), and the login
# the review-request queue and the ping queue are asked about. Required. Its gh
# login lives in a config directory of its own, under ~/.config unless
# config.json says otherwise, so it never enters the machine owner's gh config.
REVIEWER_USERNAME=$(bot_config '.reviewer.username')
REVIEWER_GH_CONFIG_DIR=$(bot_config '.reviewer.ghConfigDir')
if [ -n "$REVIEWER_USERNAME" ] && [ -z "$REVIEWER_GH_CONFIG_DIR" ]; then
  REVIEWER_GH_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/gh-$REVIEWER_USERNAME"
fi

REVIEWER_CLAUDE_BIN=$(bot_config '.reviewer.claudeBin')

# --allowedTools for an unattended agent session. Every caller uses this one
# value: a job started with a shorter list than the crontab's fails somewhere
# mid-session, on whichever tool the skill reaches for last.
BOT_AGENT_TOOLS="Bash,Read,Glob,Grep,Write,Edit,Task,WebFetch"

# Model for a /review-prs session, sweep or review request alike. The session
# only runs scripts and launches subagents that pick their own model, so it
# needs no Opus; on a large sweep its own context is re-read on every turn.
BOT_REVIEW_MODEL="${BOT_REVIEW_MODEL:-sonnet}"

# Agent for a scheduled /review-prs session or ping reply. REVIEWER_AGENT in
# the environment wins over reviewer.agent. See scripts/lib/review-agent.sh.
_REVIEWER_AGENT_ENV="${REVIEWER_AGENT:-}"
REVIEWER_AGENT=$(bot_config '.reviewer.agent')
if [ -n "$_REVIEWER_AGENT_ENV" ]; then
  REVIEWER_AGENT="$_REVIEWER_AGENT_ENV"
fi
unset _REVIEWER_AGENT_ENV
REVIEWER_AGENT="${REVIEWER_AGENT:-claude}"

BOT_BP_DOCS_DIR=$(bot_config '.bestPractices.docsDir')

# Absolute paths. Prefer these over the raw (base-ambiguous) config values.
BOT_TARGET_REPO_DIR=$(resolve_target_repo "$BOT_TARGET_REPO_PATH")
BOT_BP_DOCS_DIR_ABS=$(resolve_docs_dir "$BOT_BP_DOCS_DIR")

# Validate required fields
_missing=""
[ -z "$BOT_PROJECT_NAME" ] && _missing="$_missing project.name"
[ -z "$BOT_ORG" ] && _missing="$_missing project.org"
[ -z "$BOT_PR_REPO" ] && _missing="$_missing project.prRepository"
[ -z "$BOT_ISSUE_REPO" ] && _missing="$_missing project.issueRepository"
[ -z "$REVIEWER_USERNAME" ] && _missing="$_missing reviewer.username"
if [ -n "$_missing" ]; then
  echo "Error: Missing required config values in $BOT_CONFIG_FILE:$_missing" >&2
  echo "  Run 'make setup' or edit config.json directly." >&2
  return 1 2>/dev/null || exit 1
fi

# Fall back to 'claude' if claudeBin is not set
if [ -z "$REVIEWER_CLAUDE_BIN" ]; then
  REVIEWER_CLAUDE_BIN="$(which claude 2>/dev/null || echo "claude")"
fi

# bravebot's binary and model come from the environment or from
# reviewer.bravebotBin / reviewer.bravebotModel (written by `make setup`, because
# cron's PATH has no nvm directory for `which` to find it in).
if [ -z "${REVIEWER_BRAVEBOT_BIN:-}" ]; then
  REVIEWER_BRAVEBOT_BIN=$(bot_config '.reviewer.bravebotBin')
fi
if [ -z "${REVIEWER_BRAVEBOT_BIN:-}" ]; then
  REVIEWER_BRAVEBOT_BIN="$(which bravebot 2>/dev/null || echo "bravebot")"
fi
if [ -z "${REVIEWER_BRAVEBOT_MODEL:-}" ]; then
  REVIEWER_BRAVEBOT_MODEL=$(bot_config '.reviewer.bravebotModel')
fi
REVIEWER_BRAVEBOT_MODEL="${REVIEWER_BRAVEBOT_MODEL:-}"

export BOT_DIR BOT_CONFIG_FILE
export BOT_PROJECT_NAME BOT_ORG BOT_PR_REPO BOT_ISSUE_REPO BOT_DEFAULT_BRANCH BOT_TARGET_REPO_PATH BOT_TARGET_REPO_DIR
export BOT_PROFILE BOT_PROFILE_DIR
export REVIEWER_USERNAME REVIEWER_GH_CONFIG_DIR
export REVIEWER_CLAUDE_BIN REVIEWER_AGENT
export REVIEWER_BRAVEBOT_MODEL REVIEWER_BRAVEBOT_BIN
export BOT_AGENT_TOOLS BOT_REVIEW_MODEL
export BOT_BP_DOCS_DIR BOT_BP_DOCS_DIR_ABS
