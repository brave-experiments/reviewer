"""The agent the review sessions run, from config.json.

reviewer.agent decides what review-agent.sh launches and which binary directory
the crontab block puts on cron's PATH. It defaults to claude, and a claude
deployment's crontab must not change by a byte.
"""

import json
import os
import subprocess

import pytest
import test_scripts

Schedules = test_scripts.TestProjectSchedules


def fake_agent(tmp_dir, name="bravebot"):
    """An executable in a directory that is on no PATH, as nvm's is for cron."""
    directory = os.path.join(tmp_dir, "nvm", "bin")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(path, 0o755)
    return path


def render(tmp_dir, profile="bravebot", reviewer=None, group=None):
    root = Schedules._bot_dir(tmp_dir, profile)
    path = os.path.join(root, "config.json")
    with open(path) as f:
        config = json.load(f)
    config["reviewer"].update(reviewer or {})
    with open(path, "w") as f:
        json.dump(config, f)
    args = [os.path.join(root, "scripts", "sync-schedules.sh"), "--print"]
    if group:
        args += ["--group", group]
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        # No bravebot on this PATH: the path has to come from config.
        env={"PATH": "/usr/bin:/bin", "HOME": tmp_dir},
    )


def path_line(block):
    (line,) = [ln for ln in block.splitlines() if ln.startswith("PATH=")]
    return line


@pytest.mark.parametrize("profile", ["brave-core", "bravebot", "default"])
def test_claude_is_the_default_and_adds_nothing(tmp_dir, profile):
    result = render(tmp_dir, profile)
    assert result.returncode == 0, result.stderr
    assert Schedules._jobs(result.stdout)
    assert "--agent" not in result.stdout
    assert "nvm" not in result.stdout


def test_a_review_agent_is_on_the_path_but_not_on_any_line(tmp_dir):
    """review-session.sh reads reviewer.agent when the job fires, so the lines do
    not change; only the block's PATH needs the binary's directory."""
    binary = fake_agent(tmp_dir)
    claude_only = render(tmp_dir, group="review").stdout
    out = render(
        tmp_dir,
        reviewer={"bravebotBin": binary, "agent": "bravebot"},
        group="review",
    ).stdout
    assert os.path.dirname(binary) in path_line(out).split("=", 1)[1].split(":")
    assert os.path.dirname(binary) not in claude_only
    assert Schedules._jobs(out) == Schedules._jobs(claude_only)


def test_a_review_agent_in_the_claude_directory_adds_no_path_entry(tmp_dir):
    """Two binaries in one directory are one PATH entry, not two."""
    directory = os.path.dirname(fake_agent(tmp_dir))
    result = render(
        tmp_dir,
        reviewer={
            "claudeBin": os.path.join(directory, "claude"),
            "bravebotBin": os.path.join(directory, "bravebot"),
            "agent": "bravebot",
        },
    )
    assert result.returncode == 0, result.stderr
    assert path_line(result.stdout).count(directory) == 1


def test_a_review_agent_that_cannot_be_found_stops_the_install(tmp_dir):
    result = render(tmp_dir, reviewer={"agent": "bravebot"})
    assert result.returncode != 0
    assert "bravebot binary is not on PATH" in result.stderr
    assert result.stdout == ""


def test_an_unknown_review_agent_is_refused(tmp_dir):
    result = render(tmp_dir, reviewer={"agent": "nope"})
    assert result.returncode != 0
    assert "unsupported agent 'nope'" in result.stderr


def test_the_environment_does_not_choose_the_agent_the_crontab_is_built_for(tmp_dir):
    """An override in the shell running `make schedules` is not the deployment's
    setting; config.json is."""
    root = Schedules._bot_dir(tmp_dir, "bravebot")
    result = subprocess.run(
        [os.path.join(root, "scripts", "sync-schedules.sh"), "--print"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": tmp_dir, "REVIEWER_AGENT": "nope"},
    )
    assert result.returncode == 0, result.stderr


def review_cmd(
    tmp_dir,
    agent,
    kind="review",
    extra_env=None,
    settings='{"permissions":{"deny":[]}}',
):
    """REVIEWER_AGENT_CMD, one word to a line, as review-agent.sh builds it."""
    root = Schedules._bot_dir(tmp_dir, "default")
    script = (
        f'source "{root}/scripts/lib/load-config.sh"\n'
        f'source "{root}/scripts/lib/review-agent.sh"\n'
        f"bot_review_agent_cmd {kind} \"/review-prs --work-dir W\" sonnet '{settings}' "
        f'"{tmp_dir}/work" "{tmp_dir}/target"\n'
        'printf "%s\\n" "${REVIEWER_AGENT_CMD[@]}"\n'
        'echo "TMP=$REVIEWER_AGENT_TMP"\n'
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": tmp_dir,
        "REVIEWER_AGENT": agent,
        "REVIEWER_BRAVEBOT_BIN": "/opt/bravebot",
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env
    )


def test_claude_reviews_exactly_as_before(tmp_dir):
    result = review_cmd(tmp_dir, "claude")
    assert result.returncode == 0, result.stderr
    out = result.stdout.splitlines()
    assert out[0].endswith("claude")
    assert out[1:4] == ["-p", "/review-prs --work-dir W", "--model"]
    assert "--permission-mode" in out and "dontAsk" in out
    assert out.count("--add-dir") == 2
    assert out[-2] == "--strict-mcp-config"


def test_bravebot_reviews_without_a_fetch_tool(tmp_dir):
    result = review_cmd(tmp_dir, "bravebot")
    assert result.returncode == 0, result.stderr
    out = result.stdout.splitlines()
    assert out[0] == "/opt/bravebot"
    assert out[1] == "/review-prs --work-dir W"
    tools = out[out.index("--tools") + 1].split(",")
    assert "fetch_url" not in tools and "download_url" not in tools
    assert "run" in tools
    # `closed` stops bravebot starting python3 at all, so select-candidates.py
    # cannot run and no review is ever posted.
    assert out[out.index("--run-network") + 1] == "open"
    # Programs write the work directory; the bot directory and the read-only
    # directories are refused.
    assert out[out.index("--sandbox-allow-write") + 1].endswith("/work")
    denied = [
        out[i + 1] for i, word in enumerate(out) if word == "--sandbox-deny-write"
    ]
    assert any(path.endswith("/bot") for path in denied), denied
    assert any(path.endswith("/target") for path in denied), denied
    assert not any(path.endswith("/work") for path in denied), denied


def test_bravebot_reviews_under_the_rules_and_not_by_bypassing_them(tmp_dir):
    """Bypass approves every write and run and lets programs write anywhere the
    account can; the session is held by the allow and deny rules instead."""
    out = review_cmd(tmp_dir, "bravebot").stdout.splitlines()
    assert "--dangerously-skip-permissions" not in out
    assert "--dangerously-bypass-approvals-and-sandbox" not in out
    assert "--accept-edits" not in out and "--auto" not in out
    assert out[out.index("--sandbox") + 1] == "workspace-write"
    # The model reads the work directory only where the run trusts it.
    assert "--trust-workspace" in out
    added = [out[i + 1] for i, word in enumerate(out) if word == "--add-dir"]
    assert [os.path.basename(path) for path in added] == ["work", "target"]


def test_bravebot_reviews_never_get_the_claude_model_alias(tmp_dir):
    """`--model sonnet` makes bravebot fail with BB1003 when the operator's own
    service is configured; with no reviewer.bravebotModel it names no model at all."""
    out = review_cmd(tmp_dir, "bravebot").stdout.splitlines()
    assert "--model" not in out and "sonnet" not in out
    out = review_cmd(
        tmp_dir, "bravebot", extra_env={"REVIEWER_BRAVEBOT_MODEL": "my-model"}
    ).stdout.splitlines()
    assert out[out.index("--model") + 1] == "my-model"


def test_a_bravebot_reply_session_gets_no_program_and_no_delegate(tmp_dir):
    out = review_cmd(tmp_dir, "bravebot", kind="reply").stdout.splitlines()
    tools = out[out.index("--tools") + 1].split(",")
    assert "run" not in tools and "spawn_agent" not in tools


def test_bravebot_settings_deny_writes_outside_the_work_directory(tmp_dir):
    """The operator's own allow rules apply to a one-shot run too, so the bot
    directory and the read-only directories are denied, which no allow rule
    overrides."""
    out = review_cmd(tmp_dir, "bravebot").stdout.splitlines()
    settings = out[out.index("--settings") + 1]
    with open(settings) as f:
        deny = json.load(f)["permissions"]["deny"]
    assert any(rule.startswith("Edit(//") and "/bot/" in rule for rule in deny)
    assert any(rule.endswith("/target/**)") for rule in deny)
    assert not any(rule.endswith("/work/**)") for rule in deny)


def test_bravebot_gets_the_allow_rules_claude_gets(tmp_dir):
    """The allowlist is what holds a bravebot session, so the rules
    review-session-settings.py wrote reach it unchanged beside the added denies."""
    given = {
        "permissions": {
            "allow": ["Edit(//w/**)", "Bash(python3 select-candidates.py *)"],
            "deny": ["WebFetch", "Read(//h/.ssh/**)"],
        }
    }
    out = review_cmd(
        tmp_dir, "bravebot", settings=json.dumps(given, separators=(",", ":"))
    ).stdout.splitlines()
    with open(out[out.index("--settings") + 1]) as f:
        written = json.load(f)["permissions"]
    assert written["allow"] == given["permissions"]["allow"]
    assert written["deny"][:2] == given["permissions"]["deny"]
    assert len(written["deny"]) == 4


def test_an_agent_that_cannot_review_is_refused(tmp_dir):
    result = review_cmd(tmp_dir, "codex")
    assert "cannot review" in result.stderr
