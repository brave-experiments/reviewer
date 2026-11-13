# Review agents

`reviewer.agent` in `config.json` (set by `make setup`) picks the agent that runs the
scheduled `/review-prs` sweeps, the review-request poll and the ping replies. It is
independent of any other agent setting.

| Value | Status |
|---|---|
| `claude` (default) | Confined by an allowlist. |
| `bravebot` | Confined by the same allowlist, and its programs by its sandbox. Needs a bravebot release with brave/bravebot#4131 and brave/bravebot#4124. |

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

Both agents are held to all four.

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

bravebot is given the same settings file as claude, with the `Edit` deny rules below added.
A one-shot run applies its rules as a session does: an `allow` rule lets the write or
program it names go ahead, a `deny` rule refuses, and whatever no rule allows is refused
while the session carries on. The run exits 4 when it refused anything, where claude exits
0, and `review-session.sh` collects what the session wrote either way.

This needs a bravebot release with brave/bravebot#4131 (`allow` rules apply in a one-shot run)
and brave/bravebot#4124 (`--trust-workspace` also trusts the `--add-dir` directories). An older
bravebot refuses every write the session makes and does not show the model the work
directory, so the review posts nothing.

| Control | How |
|---|---|
| Writes | The `Edit` allow rule for the work directory. `review-agent.sh` adds `Edit` deny rules for the bot directory and each read-only directory, because a one-shot run also applies the operator's own `allow` rules, and a deny rule wins over any of them. |
| Programs | The `Bash` allow rule for `select-candidates.py`; no other program is allowed. It runs in bravebot's sandbox (`--sandbox workspace-write`), which may write the work directory (`--sandbox-allow-write`) and not the bot directory or a read-only directory (`--sandbox-deny-write`), and may not read ssh keys, gh config directories or `.envrc` (`--sandbox-deny-read`). |
| Reads | The file tools reach the bot directory, where the session starts, and the `--add-dir` directories: the work directory, and the target repository for a reply. The `Read` deny rules hold. `--trust-workspace` trusts all of them, so the model reads the PR as claude's does. |
| Network | No fetch tool in `--tools`, and no program but the one script. `--run-network` is `open`: bravebot closes the network with a network namespace, and on a Linux kernel that refuses an unprivileged one it refuses to start the program, so `select-candidates.py` would never run. claude runs it with the network open as well. |
| Tool surface | `--tools` is a fixed list with no `fetch_url`, `download_url` or `ask_user`. A reply session also gets no `run` and no `spawn_agent`. |
| GitHub | No token and an empty `GH_CONFIG_DIR`, as for claude. |

Whatever a rule says, bravebot also refuses a write to a `.git`, `.bravebot` or `.husky`
directory or to `.gitmodules`, `.gitconfig` or `.pre-commit-config.yaml`, and a write in
which its credential scan finds a value (bravebot's `docs/specs/permissions.md`, PERM-9).

### Differences from claude

- `select-candidates.py` runs in bravebot's sandbox. claude runs it with the operator's
  full access.
- The `Glob`, `Grep`, `Agent` and `Task` rules name Claude Code's tools. bravebot's own
  tools for the same work are the ones `--tools` offers.
- bravebot starts the MCP servers the operator declared. `--tools` keeps their tools from
  the model, but nothing keeps the servers from starting while the skills still load:
  `--safe` stops both. claude's `--strict-mcp-config` starts none.
- Both agents read the operator's own settings as well as the reviewer's, so an `allow`
  rule in the operator's home settings applies to a review session too. The deny rules
  from `review-session-settings.py` and `review-agent.sh` still win.
