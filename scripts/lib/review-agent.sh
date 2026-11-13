#!/bin/bash
# The command line for an unattended review-side agent session: a /review-prs
# sweep (review-session.sh) or a ping reply (reply-to-pings.sh), run by whichever
# agent reviewer.agent in config.json names (REVIEWER_AGENT).
#
#   source scripts/lib/load-config.sh
#   source scripts/lib/review-agent.sh
#   bot_review_agent_cmd review|reply <prompt> <model> <settings-json> <work-dir> [<read-only-dir>...]
#   "${REVIEWER_AGENT_CMD[@]}"
#
# The first directory is the run's work directory, the one place the session may
# write; the rest are readable only. The caller still runs the command with no
# GitHub token and an empty gh config directory.
#
# Both agents are held by the same allowlist, review-session-settings.py's rules:
# anything the rules do not allow is refused and the session carries on. claude
# reads them under --permission-mode dontAsk. A one-shot bravebot run applies them
# as a session does, and --trust-workspace lets the model read the --add-dir
# directories; both need a bravebot release that has them (docs/review-agents.md).
# bravebot also runs its programs in its sandbox, held to the work directory.

# The session's tools, by bravebot's names: nothing that fetches.
REVIEWER_BRAVEBOT_REVIEW_TOOLS="read_file,list_files,search,repo_map,read_git,write_file,edit_file,run,spawn_agent,spawn_processor,load_skill,search_skills"
# A reply session reads a thread and writes one file: no program, no delegate.
REVIEWER_BRAVEBOT_REPLY_TOOLS="read_file,list_files,search,write_file,edit_file,load_skill,search_skills"

# The sessions load /review-prs, /reply-to-ping and the subagents through .claude/ (or
# .bravebot/), which are gitignored symlinks into the checked-in agents/. A machine that
# pulled the commit moving the skills into agents/ has no such links until `make setup`
# runs, and every scheduled job would then fail, so each session re-makes them first:
# idempotent, quiet, and it never replaces a real path. Failure is only a warning, since
# a missing link fails the session loudly on its own.
bot_link_agents() {
  [ -f "$BOT_DIR/agents/setup.py" ] || return 0
  python3 "$BOT_DIR/agents/setup.py" link -q >&2 \
    || echo "Warning: could not link agents/ (run 'make link-agents')." >&2
  return 0
}

# Sets REVIEWER_AGENT_CMD. Returns 2 for an agent that cannot review.
#
# `settings` is the JSON review-session-settings.py printed. claude takes it inline;
# bravebot takes a path, so it is written to a file here and the file's name is left
# in REVIEWER_AGENT_TMP for the caller to delete when the session is over.
bot_review_agent_cmd() {
  local kind="$1" prompt="$2" model="$3" settings="$4"
  shift 4
  local work_dir="${1:-}" dir tools path settings_file model_flag
  REVIEWER_AGENT_CMD=()
  REVIEWER_AGENT_TMP=""

  case "${REVIEWER_AGENT:-claude}" in
    claude)
      REVIEWER_AGENT_CMD=("$REVIEWER_CLAUDE_BIN" -p "$prompt" --model "$model"
        --permission-mode dontAsk --settings "$settings")
      for dir in "$@"; do
        REVIEWER_AGENT_CMD+=(--add-dir "$dir")
      done
      REVIEWER_AGENT_CMD+=(--strict-mcp-config)
      ;;
    bravebot)
      tools="$REVIEWER_BRAVEBOT_REVIEW_TOOLS"
      [ "$kind" = "reply" ] && tools="$REVIEWER_BRAVEBOT_REPLY_TOOLS"
      # The prompt goes first: bravebot reads --model only after it.
      # --trust-workspace is what lets /review-prs, a skill under the bot
      # directory's .claude/skills (a symlink into agents/, made by `make setup`),
      # load at all, and what lets the model read the --add-dir directories.
      settings_file=$(mktemp "${TMPDIR:-/tmp}/review-settings.XXXXXX") || return 1
      # The Edit allow rule holds writes to the work directory. A one-shot run also
      # applies the operator's own allow rules, so writes to the bot directory and to
      # every directory that is only there to be read are denied as well: a deny rule
      # wins over any allow rule.
      printf '%s\n' "$settings" | jq --arg bot "$BOT_DIR" --args '
        .permissions.deny += ([$bot] + $ARGS.positional | map("Edit(//" + (. | ltrimstr("/")) + "/**)"))
      ' "${@:2}" > "$settings_file" || { rm -f "$settings_file"; return 1; }
      REVIEWER_AGENT_TMP="$settings_file"
      # $model is the reviewer's claude alias ("sonnet" by default), which bravebot
      # resolves to one of Brave's models and then refuses (BB1003) when the
      # operator has a service of their own. So it is not passed: the model is
      # reviewer.bravebotModel when set, else bravebot's own `model` setting.
      model_flag=()
      [ -n "${REVIEWER_BRAVEBOT_MODEL:-}" ] && model_flag=(--model "$REVIEWER_BRAVEBOT_MODEL")
      # --run-network closed is not used: bravebot closes the network with a network
      # namespace, and on a Linux kernel that refuses an unprivileged one it refuses
      # to start the program at all, so select-candidates.py would never run and no
      # review would be posted. The allow rules let no other program run.
      REVIEWER_AGENT_CMD=("$REVIEWER_BRAVEBOT_BIN" "$prompt" ${model_flag[@]+"${model_flag[@]}"}
        --settings "$settings_file" --trust-workspace
        --tools "$tools" --run-network open
        --sandbox workspace-write --sandbox-deny-write "$BOT_DIR")
      for dir in "$@"; do
        REVIEWER_AGENT_CMD+=(--add-dir "$dir")
      done
      # A program may write the work directory and nothing that is only there to be read.
      for dir in "${@:2}"; do
        REVIEWER_AGENT_CMD+=(--sandbox-deny-write "$dir")
      done
      [ -n "$work_dir" ] && REVIEWER_AGENT_CMD+=(--sandbox-allow-write "$work_dir")
      # What a program the session starts must not read. The settings file's Read
      # rules say the same to the file tools; these say it to `run`.
      for path in "$HOME/.ssh" "$HOME/.config/gh" "$BOT_DIR/.envrc" \
          "${REVIEWER_GH_CONFIG_DIR:-}" "${GH_CONFIG_DIR:-}"; do
        [ -n "$path" ] && [ -e "$path" ] && REVIEWER_AGENT_CMD+=(--sandbox-deny-read "$path")
      done
      ;;
    *)
      echo "Error: reviewer.agent '${REVIEWER_AGENT}' cannot review (expected: claude | bravebot)." >&2
      return 2
      ;;
  esac
}
