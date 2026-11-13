# Review agents

`reviewer.agent` in `config.json` (set by `make setup`) picks the agent that runs the
scheduled `/review-prs` sweeps, the review-request poll and the ping replies. It is
independent of any other agent setting.

| Value | Status |
|---|---|
| `claude` (default) | Confined by an allowlist. |
| `bravebot` | Runs, but is confined differently, and less tightly. See below. |

`REVIEWER_AGENT` in the environment overrides the key.
Only `claude` and `bravebot` are accepted; `codex` and `cursor` have no confined headless
mode wired in here.

The command line is built in one place, `scripts/lib/review-agent.sh`, and used by
`scripts/review-session.sh` and `scripts/reply-to-pings.sh`.

## What a review session must not do

The session reads untrusted text: a PR's diff, its description and its source tree. It
runs without a GitHub token. `prepare-review.py` and `collect-results.py` run outside it,
and only the validators' results file leaves it. So the session may:

- read its work directory and the bot directory (credential paths excepted);
- write only inside its work directory;
- run one script, `select-candidates.py`, and launch subagents;
- not reach the network.

claude is held to all four. bravebot is held to none of them exactly; see below.

## claude

`review-session-settings.py` writes an allowlist and the session runs under
`--permission-mode dontAsk`, so anything not listed is refused and the session carries on.

- **Allowed:** `Glob`, `Grep`, `Edit` inside the work directory, and `Bash` for
  `python3 select-candidates.py` alone, plus the read-only commands Claude Code always
  allows. A review session also gets `Agent` and `Task`. A reply session gets no
  subagents and no `Bash`.
- **Reads:** only the working directories, the bot directory and the work directory.
- **Denied in every mode:** `WebFetch`, `WebSearch`, and reads of `~/.git-credentials`,
  `~/.netrc`, `~/.config/gh`, `~/.ssh`, `~/.claude/settings.json`, `~/.claude.json`, the
  bot directory's `.envrc`, `/proc` and the reviewer's gh config directory.
- **`--strict-mcp-config`:** no MCP server from the user's or a project's configuration.

The network is out of reach because nothing that reaches it is allowed: no fetch tool,
and no program but the one script.

## bravebot

A one-shot bravebot run has nobody to ask. A write, a program or a fetch that would need
approval ends the run on status 4, and an `allow` rule in a settings file decides nothing
there. A review session that cannot write its results file is useless, so the run uses
`--dangerously-skip-permissions`. That mode answers every question yes and also vouches
for every file the session reads, so what it reads is not quarantined.

Bypass also widens what the session can reach, beyond its own directories. With
`--trust-workspace` and `--sandbox workspace-write`, as here, the file tools and the
programs `run` starts may read and write any path the account can (bravebot's
`docs/specs/tools/request-path.md`, PATHREQ-7, and `docs/specs/sandboxing.md`, SANDBOX-28).
So `--sandbox-allow-write` on the work directory confines nothing. What bravebot still
refuses in bypass, to the file tools and to programs alike:

- reading or writing `~/.ssh`, `~/.bravebot`, the credential locations it knows and the
  directories that hold one, and the home directory and the directories above it as
  wholes (the entries already in them stay writable; no new entry is made directly in one);
- writing a persistence location: the shell's startup files, `~/Library/LaunchAgents`,
  systemd's per-user directories, and git's global configuration (`~/.gitconfig`,
  `~/.config/git/config`);
- any path a deny rule, `--sandbox-deny-read` or `--sandbox-deny-write` covers.

A write to a protected path (a `.git`, `.bravebot` or `.husky` directory, `.gitmodules`,
`.gitconfig`, `.pre-commit-config.yaml`) is one bravebot otherwise always puts to a person,
and bypass approves it too (`docs/specs/permission-modes.md`, MODE-4). On macOS the sandbox
still holds a program other than `git` back from those names (SANDBOX-14). The file tools
and a `git` program are not held back, and on Linux no program is.

That is bravebot 0.21.0, as its specs describe it.

What replaces the allowlist:

| Control | How |
|---|---|
| File reads | `Read` deny rules from `review-session-settings.py`, which hold in every mode, and bravebot's own refusals above. Other reads are not limited to the session's directories. |
| File writes | `Edit` deny rules for the bot directory and each read-only directory, added by `review-agent.sh`, and bravebot's own refusals above. Every other path the account can write stays writable, the target repository included. See weakness 3. |
| Programs `run` starts | `--sandbox workspace-write`, `--sandbox-deny-write` on the bot directory, and `--sandbox-deny-read` on ssh keys, gh config directories and `.envrc`. Under bypass the sandbox lets programs write what the file tools can, so the deny rows and bravebot's refusals are what hold them. |
| Network for programs | None: `--run-network open`. `closed` makes bravebot refuse to start `python3` on Linux (it cannot confine a program that does not need the network), so `select-candidates.py` never runs. See weakness 5. |
| Tool surface | `--tools` is a fixed list with no `fetch_url`, `download_url` or `ask_user`. A reply session also gets no `run` and no `spawn_agent`. |
| GitHub | No token and an empty `GH_CONFIG_DIR`, as for claude. |

### What is weaker than claude

1. **`run` is not limited to one script.** claude may run only `select-candidates.py`.
   bravebot may run any program the sandbox allows. There is no allowlist for `run` that
   applies in this mode, because `allow` rules are ignored where nobody can be asked.
2. **Reads reach outside the session's directories.** claude reads only its working
   directories. bravebot's file tools read any path the account can, less the refusals
   and deny rules above: another checkout on the machine, for example.
3. **Writes reach outside the work directory, the target repository included.** The file
   tools and programs may write anywhere the account can, less the bot directory and
   bravebot's refusals. Nothing denies `project.targetRepoPath`, so a session misled by a
   PR could write a hook into its `.git/hooks`, or set `core.hooksPath` in its
   `.git/config`. The next `prepare-review.py` runs `git worktree add` in that repository
   as the reviewer, outside any confinement, and git runs the hook. The session never
   holds the reviewer's GitHub access, but code it leaves behind would. Denying writes to
   the target repository (an `Edit` deny rule and `--sandbox-deny-write`) closes this
   route. The system temporary directory and other checkouts stay writable.
4. **`--trust-workspace`** trusts the whole bot directory so the `/review-prs` skill under
   `.claude/skills` loads (a symlink into the checked-in `agents/skills`, made by
   `make setup`). It trusts the PR checkout nothing, but it does vouch for the
   bot directory's own files. Bypass trusts the working directory whether or not the flag
   is given (`docs/specs/permission-modes.md`, MODE-4), so dropping the flag would not
   undo the widened writes of weakness 3.
5. **Programs `run` starts can reach the network.** The session holds no GitHub token,
   and credential paths are denied to programs, but a program it runs can send out
   anything else it can read: the PR checkout, the bot directory's files, and, by
   weakness 2, most of the rest of the disk. The tool surface has no `fetch_url`, so only
   `run` can do it.

Reads not being quarantined is not on this list, since claude has no quarantine either.
Content from the PR reaches either model as plain input, so a prompt injection in a diff
reaches it as instructions. What bypass gives up is the quarantine bravebot would
otherwise add. The controls above bound what a misled session can then do. They do not
stop it writing a bad results file inside the work directory.

### What would close the gap

Two changes to bravebot:

1. **A one-shot mode that refuses what the rules do not allow and lets the run carry on**,
   as claude's `dontAsk` does. Today a refusal ends a one-shot run, and `allow` rules are
   not read where nobody can be asked (bravebot's `docs/specs/cli.md`, CLI-1).
   `--input-format lines --approve-input-lines` comes closest: the wrapper is sent each
   write, run and fetch, and could approve only writes inside the work directory and the
   one script. But a decline still ends the run (CLI-36), so one stray command costs the
   whole review. `--accept-edits` refuses every run, and `--auto` leaves runs to a
   classifier; under either, a refusal still ends the run.
2. **`--run-network closed` that can still start `python3` on Linux.** With 1, this
   matters less: the only program would be `select-candidates.py`.

And one change here, once bypass is gone. Outside bypass, content nobody vouched for is
never put in a message to the model (bravebot's `docs/specs/labels.md`), and a directory
given with `--add-dir` is vouched for by nothing (CLI-11, TRUST-26). The session starts in
the bot directory and reaches the work directory through `--add-dir`, so without bypass
the model could not see the PR it reviews. Everything the review reads is inside the work
directory (`prepare-review.py` puts each PR's worktree at `<work>/<pr>/source`), so the
session should start there with `--trust-workspace`, and reach the bot directory through
`--add-dir`. The skills, the subagents and `select-candidates.py` then have to be found
from the work directory. Under bypass the move changes nothing, since bypass trusts the
workspace and vouches for every read (MODE-4).

With 1 and that move the session would not need bypass, and the widened reach goes with it.
