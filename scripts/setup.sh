#!/bin/bash
# Setup script for reviewer
# Fully idempotent — safe to re-run at any time.
# Creates config.json, .envrc, the org-members cache and the dev virtualenv, checks the
# reviewer's gh login, and offers to install the cron jobs.
# Never overwrites an existing file without an explicit confirmation.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$PROJECT_ROOT/config.json"

echo "==================================="
echo "  Reviewer Setup"
echo "==================================="
echo ""

# ─── Step 1: config.json ─────────────────────────────────────────────────────

if [ -f "$CONFIG_FILE" ]; then
  echo "✓ config.json found — reconfiguring with current values as defaults"
  echo ""
else
  if [ ! -t 0 ]; then
    echo "Error: No config.json found and no interactive terminal available."
    echo "  Either run setup.sh directly from a terminal, or create config.json first:"
    echo "    cp config.example.json config.json        # then edit with your values"
    exit 1
  fi
  echo "No config.json found — starting setup wizard."
  echo ""
fi

# Load previous values if config exists (for use as defaults)
if [ -f "$CONFIG_FILE" ]; then
  _prev() { jq -r "$1 // empty" "$CONFIG_FILE" 2>/dev/null; }
  PREV_PR_REPO=$(_prev '.project.prRepository')
  PREV_DEFAULT_BRANCH=$(_prev '.project.defaultBranch')
  PREV_ISSUE_REPO=$(_prev '.project.issueRepository')
  PREV_TARGET_REPO=$(_prev '.project.targetRepoPath')
  PREV_PROFILE=$(_prev '.project.profile')
  PREV_REVIEWER_USER=$(_prev '.reviewer.username')
  PREV_REVIEWER_DIR=$(_prev '.reviewer.ghConfigDir')
  PREV_REVIEW_AGENT=$(_prev '.reviewer.agent')
  PREV_BRAVEBOT_BIN=$(_prev '.reviewer.bravebotBin')
fi

prompt_required() {
  local var_name="$1" prompt_text="$2" default="$3" value=""
  while [ -z "$value" ]; do
    if [ -n "$default" ]; then
      read -p "$prompt_text[$default]: " value
      value="${value:-$default}"
    else
      read -p "$prompt_text" value
    fi
    if [ -z "$value" ]; then
      echo "  ⚠️  This field is required."
    fi
  done
  eval "$var_name=\$value"
}

# Re-asks until the answer is one of the words given. Blank takes the default.
prompt_choice() {
  local var_name="$1" prompt_text="$2" default="$3" allowed=" $4 " value=""
  while true; do
    read -p "$prompt_text [$default]: " value
    value="$(tr '[:upper:]' '[:lower:]' <<< "${value:-$default}")"
    case "$allowed" in
      *" $value "*) break ;;
    esac
    echo "  ⚠️  Choose one of:${allowed}"
  done
  eval "$var_name=\$value"
}

if [ -t 0 ]; then
  echo "─── Reviewed Project ───"
  prompt_required CFG_PR_REPO "Repo whose pull requests are reviewed (owner/repo): " "$PREV_PR_REPO"
  read -p "Default branch for ${CFG_PR_REPO} [${PREV_DEFAULT_BRANCH:-master}]: " CFG_DEFAULT_BRANCH
  CFG_DEFAULT_BRANCH="${CFG_DEFAULT_BRANCH:-${PREV_DEFAULT_BRANCH:-master}}"
  read -p "Repo where best-practice proposals are filed as issues (owner/repo) [${PREV_ISSUE_REPO:-$CFG_PR_REPO}]: " CFG_ISSUE_REPO
  CFG_ISSUE_REPO="${CFG_ISSUE_REPO:-${PREV_ISSUE_REPO:-$CFG_PR_REPO}}"

  CFG_ORG="${CFG_PR_REPO%%/*}"
  CFG_PROJECT_NAME="${CFG_PR_REPO##*/}"

  echo ""
  echo "─── Local Checkout of the Reviewed Project ───"
  echo "Path to a clone of ${CFG_PR_REPO}. Reviews read PR head commits through git"
  echo "worktrees of it, and its docs/best-practices are the rules PRs are checked against."
  echo "Relative to this directory ($PROJECT_ROOT) or absolute."
  prompt_required CFG_TARGET_REPO "Checkout path (e.g. ../src/brave, /abs/path/to/repo): " "$PREV_TARGET_REPO"

  echo ""
  echo "─── Project Profile ───"
  echo "Which projects/<name>/ supplies the review rules: label sets, UI paths and"
  echo "the project guidance the reviewers read. 'default' is the generic profile."
  if [ -n "$PREV_PROFILE" ]; then
    DEFAULT_CFG_PROFILE="$PREV_PROFILE"
  elif [ -f "$PROJECT_ROOT/projects/$CFG_PROJECT_NAME/profile.json" ]; then
    DEFAULT_CFG_PROFILE="$CFG_PROJECT_NAME"
  else
    DEFAULT_CFG_PROFILE="default"
  fi
  read -p "Profile [$DEFAULT_CFG_PROFILE]: " CFG_PROFILE
  CFG_PROFILE="${CFG_PROFILE:-$DEFAULT_CFG_PROFILE}"
  if [ ! -f "$PROJECT_ROOT/projects/$CFG_PROFILE/profile.json" ]; then
    echo "  ⚠️  projects/$CFG_PROFILE/profile.json does not exist — reviews fall back"
    echo "     to the generic profile until you create it."
  fi

  echo ""
  echo "─── Reviewer Account ───"
  echo "Every scheduled job acts as one dedicated GitHub account, which must not be the"
  echo "author of the PRs it reviews (GitHub does not let an account approve its own PR)."
  echo "Its gh login is kept in its own directory under ~/.config, apart from your own"
  echo "gh config. See docs/reviewer-account.md."
  echo ""
  while true; do
    prompt_required CFG_REVIEWER_USER "Reviewer GitHub username: " "$PREV_REVIEWER_USER"
    if [[ "$CFG_REVIEWER_USER" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$ ]]; then
      break
    fi
    echo "  ⚠️  '$CFG_REVIEWER_USER' is not a GitHub username."
    PREV_REVIEWER_USER=""
  done
  CFG_REVIEWER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/gh-$CFG_REVIEWER_USER"
  if [ "$CFG_REVIEWER_USER" = "${PREV_REVIEWER_USER:-}" ] && [ -n "${PREV_REVIEWER_DIR:-}" ]; then
    CFG_REVIEWER_DIR="$PREV_REVIEWER_DIR"
  fi
  echo "  gh login for $CFG_REVIEWER_USER: $CFG_REVIEWER_DIR"

  echo ""
  echo "─── Agent ───"
  echo "Which agent runs the scheduled reviews, review requests and ping replies:"
  echo "claude | bravebot. See docs/review-agents.md for how each is confined."
  echo ""
  prompt_choice CFG_REVIEW_AGENT "Agent for scheduled reviews" "${PREV_REVIEW_AGENT:-claude}" "claude bravebot"

  # bravebot is a node script that lives under nvm for most people, and cron's PATH
  # has no nvm directory. Its absolute path is stored so no job has to find it.
  CFG_BRAVEBOT_BIN="${PREV_BRAVEBOT_BIN:-}"
  if [ "$CFG_REVIEW_AGENT" = "bravebot" ]; then
    DEFAULT_BRAVEBOT_BIN="${PREV_BRAVEBOT_BIN:-$(command -v bravebot 2>/dev/null || true)}"
    while true; do
      if [ -n "$DEFAULT_BRAVEBOT_BIN" ]; then
        read -p "Path to the bravebot binary [$DEFAULT_BRAVEBOT_BIN]: " CFG_BRAVEBOT_BIN
        CFG_BRAVEBOT_BIN="${CFG_BRAVEBOT_BIN:-$DEFAULT_BRAVEBOT_BIN}"
      else
        read -p "Path to the bravebot binary (not on PATH): " CFG_BRAVEBOT_BIN
      fi
      if [ -n "$CFG_BRAVEBOT_BIN" ] && [ -x "$CFG_BRAVEBOT_BIN" ] && [[ "$CFG_BRAVEBOT_BIN" = /* ]]; then
        break
      fi
      echo "  ⚠️  Give the absolute path of an executable file."
      DEFAULT_BRAVEBOT_BIN=""
    done
  fi

  # Values go through the environment, not argv or string interpolation, so no
  # user input can be read as JSON or as shell.
  CFG_PROJECT_NAME="$CFG_PROJECT_NAME" \
  CFG_ORG="$CFG_ORG" \
  CFG_PR_REPO="$CFG_PR_REPO" \
  CFG_ISSUE_REPO="$CFG_ISSUE_REPO" \
  CFG_DEFAULT_BRANCH="$CFG_DEFAULT_BRANCH" \
  CFG_TARGET_REPO="$CFG_TARGET_REPO" \
  CFG_PROFILE="${CFG_PROFILE:-default}" \
  CFG_REVIEWER_USER="$CFG_REVIEWER_USER" \
  CFG_REVIEWER_DIR="$CFG_REVIEWER_DIR" \
  CFG_REVIEW_AGENT="$CFG_REVIEW_AGENT" \
  CFG_BRAVEBOT_BIN="$CFG_BRAVEBOT_BIN" \
  CONFIG_FILE="$CONFIG_FILE" \
  BOT_ROOT="$PROJECT_ROOT" \
  python3 -c "
import json, os

def val(name):
    return os.environ.get(name) or None

target_repo = os.environ['CFG_TARGET_REPO']
bot_root = os.environ['BOT_ROOT']

existing = {}
try:
    with open(os.environ['CONFIG_FILE']) as _f:
        existing = json.load(_f)
except (OSError, ValueError):
    pass
existing_reviewer = existing.get('reviewer') or {}
existing_labels = existing.get('labels') or {
    'prLabels': [], 'issueLabels': [], 'disabledTestLabel': ''
}
existing_schedules = existing.get('schedules') or {
    'syncRepo': False, 'syncRepoPath': None
}

# docsDir is stored relative to this directory, derived from the *resolved*
# checkout so it does not depend on which base the path was typed against.
if os.path.isabs(target_repo):
    _target_abs = os.path.normpath(target_repo)
else:
    _bot_base = os.path.normpath(os.path.join(bot_root, target_repo))
    _parent_base = os.path.normpath(os.path.join(os.path.dirname(bot_root), target_repo))
    if os.path.exists(os.path.join(_bot_base, '.git')):
        _target_abs = _bot_base
    elif os.path.exists(os.path.join(_parent_base, '.git')):
        _target_abs = _parent_base
    else:
        _target_abs = _bot_base
docs_dir = os.path.join(os.path.relpath(_target_abs, bot_root), 'docs')

config = {
    'project': {
        **(existing.get('project') or {}),
        'name': os.environ['CFG_PROJECT_NAME'],
        'org': os.environ['CFG_ORG'],
        'prRepository': os.environ['CFG_PR_REPO'],
        'issueRepository': os.environ['CFG_ISSUE_REPO'],
        'defaultBranch': os.environ['CFG_DEFAULT_BRANCH'],
        'targetRepoPath': target_repo,
        'profile': os.environ['CFG_PROFILE'],
    },
    # What the wizard does not ask about (claudeBin, bravebotModel, ...) is
    # carried forward; a re-run to change one answer must not reset the rest.
    'reviewer': {
        **{
            'claudeBin': None,
            'bravebotModel': None,
        },
        **existing_reviewer,
        'username': os.environ['CFG_REVIEWER_USER'],
        'ghConfigDir': val('CFG_REVIEWER_DIR'),
        'agent': os.environ['CFG_REVIEW_AGENT'],
        'bravebotBin': val('CFG_BRAVEBOT_BIN'),
    },
    'labels': existing_labels,
    'bestPractices': {
        'docsDir': docs_dir,
        'indexFile': 'best_practices.md',
        'securityFile': 'SECURITY.md',
    },
    'schedules': existing_schedules,
}
with open(os.environ['CONFIG_FILE'], 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\\n')
"
  echo ""
  echo "✓ Config written to $CONFIG_FILE"
  echo ""
fi

# Source config (needed for all subsequent steps)
source "$SCRIPT_DIR/lib/load-config.sh"

# ─── Step 2: .envrc ──────────────────────────────────────────────────────────
#
# Every cron line begins `cd $PROJECT_ROOT && source .envrc && ...`, so a
# missing .envrc returns 1 and silently cancels the whole job.
if [ -f "$PROJECT_ROOT/.envrc" ]; then
  echo "✓ .envrc already exists"
else
  cat > "$PROJECT_ROOT/.envrc" <<'ENVRC'
# Generated by scripts/setup.sh. Gitignored — safe to edit.
# Loads and validates config.json. Sourced by every cron job (a missing or failing
# .envrc cancels the job), and by direnv for interactive shells here. It sets no
# gh or git identity: scheduled jobs get the reviewer's through scripts/as-reviewer.sh.
_bot_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
_bot_dir="${_bot_dir:-$PWD}"
source "$_bot_dir/scripts/lib/load-config.sh"
unset _bot_dir
ENVRC
  echo "✓ .envrc created"
fi
echo ""

# ─── Step 3: Org members cache ───────────────────────────────────────────────

ORG_MEMBERS_FILE="$PROJECT_ROOT/.ignore/org-members.txt"
mkdir -p "$PROJECT_ROOT/.ignore"

if [ -f "$ORG_MEMBERS_FILE" ]; then
  echo "✓ Org members file found: $(wc -l < "$ORG_MEMBERS_FILE") members"
else
  echo "No org members cache found."
  if [ -t 0 ]; then
    read -p "  Generate it now via GitHub API? (Y/n) " -n 1 -r
    echo
  else
    REPLY=n
  fi
  if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    echo "  Fetching members of $BOT_ORG..."
    if gh api "/orgs/$BOT_ORG/members" --paginate --jq '.[].login' > "$ORG_MEMBERS_FILE" 2>/dev/null; then
      echo "  ✓ Wrote $(wc -l < "$ORG_MEMBERS_FILE") members to $ORG_MEMBERS_FILE"
    else
      echo "  ⚠️  Failed to fetch org members (check gh auth and org access)"
      echo "     Create it manually: one GitHub username per line"
      rm -f "$ORG_MEMBERS_FILE"
    fi
  else
    echo "  Skipped. Create it manually when ready:"
    echo "    gh api /orgs/$BOT_ORG/members --paginate --jq '.[].login' > $ORG_MEMBERS_FILE"
  fi
fi
echo ""

# ─── Step 4: Dev virtualenv ──────────────────────────────────────────────────

if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  echo "✓ .venv already exists"
elif "$SCRIPT_DIR/install-dev-deps.sh" >/dev/null 2>&1; then
  echo "✓ .venv created (make test / make lint)"
else
  echo "⚠️  Could not create .venv. Run scripts/install-dev-deps.sh to see why."
fi
echo ""

# ─── Step 5: Reviewer gh account ─────────────────────────────────────────────
# Scheduled /review-prs jobs run through scripts/as-reviewer.sh, which reads the
# login from the directory below. It is a gh config directory of its own, so the
# login never enters your gh config.

REVIEWER_LOGGED_IN=false
{
  reviewer_gh() {
    env -u GH_TOKEN -u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN \
      GH_CONFIG_DIR="$REVIEWER_GH_CONFIG_DIR" gh "$@"
  }
  reviewer_login() { reviewer_gh api user --jq .login 2>/dev/null || true; }
  same_login() {
    [ "$(tr '[:upper:]' '[:lower:]' <<< "$1")" = "$(tr '[:upper:]' '[:lower:]' <<< "$2")" ]
  }

  mkdir -p "$REVIEWER_GH_CONFIG_DIR"
  chmod 700 "$REVIEWER_GH_CONFIG_DIR"

  REVIEWER_LOGIN=""
  if reviewer_gh auth token --hostname github.com >/dev/null 2>&1; then
    REVIEWER_LOGIN=$(reviewer_login)
  fi

  if [ -z "$REVIEWER_LOGIN" ]; then
    echo "Reviewer account: $REVIEWER_USERNAME has no gh login yet."
    echo ""
    echo "  The login is stored in $REVIEWER_GH_CONFIG_DIR"
    echo "  (GH_CONFIG_DIR points gh there; your own gh config is not touched)."
    echo ""
    echo "  1. Open https://github.com in a browser signed in as $REVIEWER_USERNAME."
    echo "     If you are signed in as someone else, use a private window."
    echo "  2. Run:"
    echo "       GH_CONFIG_DIR=$REVIEWER_GH_CONFIG_DIR gh auth login --hostname github.com --web --skip-ssh-key"
    echo "  3. gh prints a one-time code and opens github.com/login/device."
    echo "     Enter the code as $REVIEWER_USERNAME and authorize."
    echo ""
    echo "  To use a personal access token instead (classic, 'repo' scope):"
    echo "       GH_CONFIG_DIR=$REVIEWER_GH_CONFIG_DIR gh auth login --hostname github.com --with-token"
    echo ""
    if [ -t 0 ]; then
      read -p "  Run step 2 now? (Y/n) " -n 1 -r
      echo
      if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        reviewer_gh auth login --hostname github.com --web --skip-ssh-key || true
        REVIEWER_LOGIN=$(reviewer_login)
      fi
    fi
  fi

  if [ -z "$REVIEWER_LOGIN" ]; then
    echo "⚠️  Reviewer '$REVIEWER_USERNAME' is not logged in. Scheduled reviews will"
    echo "   fail until it is (there is no other account to fall back to). Run the command above,"
    echo "   or re-run 'make setup'."
  elif ! same_login "$REVIEWER_LOGIN" "$REVIEWER_USERNAME"; then
    echo "⚠️  $REVIEWER_GH_CONFIG_DIR is logged in as '$REVIEWER_LOGIN', not"
    echo "   '$REVIEWER_USERNAME'. Scheduled reviews refuse to run as the wrong account."
    echo "   Log that one out, then sign in again as $REVIEWER_USERNAME:"
    echo "     GH_CONFIG_DIR=$REVIEWER_GH_CONFIG_DIR gh auth logout --hostname github.com"
    echo "     GH_CONFIG_DIR=$REVIEWER_GH_CONFIG_DIR gh auth login --hostname github.com --web --skip-ssh-key"
  else
    REVIEWER_LOGGED_IN=true
    echo "✓ Reviewer '$REVIEWER_LOGIN' is logged in ($REVIEWER_GH_CONFIG_DIR)"
    echo "  Scheduled /review-prs jobs and the review-request poll run as this account."
    REVIEWER_PERMS=$(reviewer_gh api "repos/$BOT_PR_REPO" --jq '.permissions | if .admin or .maintain or .push then "write" elif .triage then "triage" elif .pull then "read" else "none" end' 2>/dev/null || echo "")
    case "$REVIEWER_PERMS" in
      write) echo "  ✓ Has write access to $BOT_PR_REPO: its approvals count." ;;
      triage|read)
        echo "  ⚠️  Has only $REVIEWER_PERMS access to $BOT_PR_REPO. Its reviews post, but an"
        echo "     approval from an account without write access does not satisfy required reviews."
        ;;
      none) echo "  ⚠️  Cannot see $BOT_PR_REPO. Invite $REVIEWER_USERNAME as a collaborator." ;;
      *) echo "  ℹ️  Could not read $REVIEWER_USERNAME's access to $BOT_PR_REPO." ;;
    esac
  fi
}
echo ""

# ─── Step 6: Schedules ───────────────────────────────────────────────────────
#
# The crontab lines are built from config.json when they are installed -- which
# agent the sessions start, and the PATH that agent's binary needs -- so an
# installed block goes stale when the agent above changes. Nothing is installed
# without an answer, and the default is whatever is installed now.

SCHEDULES_INSTALLED=false
source "$SCRIPT_DIR/lib/cron-blocks.sh"
CRON_NOW=$(crontab -l 2>/dev/null || true)
DEFAULT_SCHEDULES=n
for name in "${BOT_CRON_MARKER_NAMES[@]}"; do
  if grep -qxF "# === $name ($BOT_PROJECT_NAME:review) scheduled jobs ===" <<< "$CRON_NOW"; then
    DEFAULT_SCHEDULES=y
  fi
done

echo "─── Schedules ───"
if ! command -v crontab >/dev/null 2>&1; then
  echo "ℹ️  crontab is not available on this machine — skipping schedules."
elif [ ! -t 0 ]; then
  echo "ℹ️  No terminal — skipping. Run 'make schedules' to install them."
else
  echo "Review cron jobs for profile '$BOT_PROFILE', built from the agent chosen above."
  [ "$DEFAULT_SCHEDULES" = n ] || echo "Currently installed for $BOT_PROJECT_NAME — reinstalling picks up a changed agent."
  read -p "Install the review schedules? (y/n) [$DEFAULT_SCHEDULES]: " SCHEDULE_CHOICE
  SCHEDULE_CHOICE="$(tr '[:upper:]' '[:lower:]' <<< "${SCHEDULE_CHOICE:-$DEFAULT_SCHEDULES}")"
  case "$SCHEDULE_CHOICE" in
    y|yes)
      if "$SCRIPT_DIR/sync-schedules.sh"; then
        SCHEDULES_INSTALLED=true
      else
        echo "⚠️  Installing the schedules failed (see above). Fix it, then run 'make schedules'."
      fi ;;
    *) echo "  Left the crontab alone." ;;
  esac
fi
echo ""

# ─── Done ─────────────────────────────────────────────────────────────────────

echo "==================================="
echo "  Setup Complete!"
echo "==================================="
echo ""
echo "  Project:   $BOT_PROJECT_NAME"
echo "  PR repo:   $BOT_PR_REPO"
echo "  Checkout:  $BOT_TARGET_REPO_DIR"
echo ""

NEXT=()
if [ ! -d "$BOT_TARGET_REPO_DIR/.git" ] && [ ! -f "$BOT_TARGET_REPO_DIR/.git" ]; then
  NEXT+=("Clone $BOT_PR_REPO to $BOT_TARGET_REPO_DIR (or fix project.targetRepoPath in config.json)")
fi
if [ "$REVIEWER_LOGGED_IN" != true ]; then
  NEXT+=("Log in the reviewer: GH_CONFIG_DIR=$REVIEWER_GH_CONFIG_DIR gh auth login --hostname github.com --web --skip-ssh-key")
fi
if [ "$SCHEDULES_INSTALLED" != true ]; then
  NEXT+=("Run 'make schedules' to install the cron jobs (or re-run 'make setup'); a changed agent needs it again")
fi

if [ ${#NEXT[@]} -gt 0 ]; then
  echo "Next steps:"
  for i in "${!NEXT[@]}"; do
    echo "  $((i+1)). ${NEXT[$i]}"
  done
  echo ""
fi

echo "Review once by hand: ./scripts/review-session.sh -- 1d open --auto"
echo "Try the checks:      make check"
echo ""
