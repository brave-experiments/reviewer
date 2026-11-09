"""Tests for reviewer's shell scripts: the crontab blocks, the schedules, the
reviewer-account wrapper, the review-session settings and the review queues.

Everything here runs the real scripts. `gh`, `crontab` and the agent binary are
stubs on PATH, because the suite runs on the machine whose schedules these are.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), os.pardir)
SCRIPT_DIR = os.path.join(REPO_ROOT, "scripts")
PROJECTS_DIR = os.path.join(REPO_ROOT, "projects")
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden")


class TestCronBlocks:
    """sync-schedules.sh replaces this project's crontab block by matching its
    marker exactly. The marker carries this repo's name, and that name is not
    the one brave-dev-loop used for the same jobs: a block it wrote matches
    nothing, so installing here would leave its sweeps and polls running beside
    this repo's, twice over."""

    LIB = os.path.join(SCRIPT_DIR, "lib", "cron-blocks.sh")

    @classmethod
    def _run(cls, snippet, stdin=""):
        return subprocess.run(
            ["bash", "-c", f'source "{cls.LIB}"\n{snippet}'],
            input=stdin,
            capture_output=True,
            text=True,
        ).stdout

    @staticmethod
    def _block(name, project, job, group="review"):
        label = f"{project}:{group}" if group else project
        return "\n".join(
            [
                f"# === {name} ({label}) scheduled jobs ===",
                job,
                f"# === end {name} ({label}) ===",
            ]
        )

    def _strip(self, crontab, project="brave-core"):
        return self._run(f'bot_strip_cron_blocks "{project}" review', crontab)

    def test_the_marker_written_is_the_current_name_and_group(self):
        assert self._run('bot_cron_marker "p" review').strip() == "reviewer (p:review)"

    def test_a_marker_without_a_group_has_none(self):
        assert self._run('bot_cron_marker "p"').strip() == "reviewer (p)"

    def test_the_only_group_is_review(self):
        assert self._run('echo "${BOT_CRON_GROUPS[*]}"').strip() == "review"

    def test_the_current_block_is_stripped(self):
        out = self._strip(self._block("reviewer", "brave-core", "0 1 * * * now"))
        assert "now" not in out

    def test_the_review_block_brave_dev_loop_wrote_is_stripped_too(self):
        """Left behind, its jobs keep running beside the ones just installed."""
        out = self._strip(self._block("brave-dev-loop", "brave-core", "0 1 * * * old"))
        assert "old" not in out

    def test_the_review_block_from_the_former_repo_name_is_stripped_too(self):
        out = self._strip(self._block("brave-dev-bot", "brave-core", "0 1 * * * old"))
        assert "old" not in out

    def test_every_spelling_goes_in_one_pass(self):
        crontab = "\n".join(
            [
                self._block("brave-dev-bot", "brave-core", "0 1 * * * oldest"),
                self._block("brave-dev-loop", "brave-core", "0 2 * * * old"),
                self._block("reviewer", "brave-core", "0 3 * * * now"),
            ]
        )
        out = self._strip(crontab)
        assert not any(job in out for job in ("oldest", "old", "now"))

    @pytest.mark.parametrize("group", ["run", "maintenance"])
    def test_brave_dev_loops_other_groups_are_not_this_repos_to_remove(self, group):
        """The run and maintenance blocks belong to brave-dev-loop, which is
        still installed beside this repo."""
        block = self._block("brave-dev-loop", "brave-core", "0 1 * * * theirs", group)
        assert "theirs" in self._strip(block)

    def test_a_block_brave_dev_loop_wrote_before_it_grouped_its_jobs_survives(self):
        block = self._block("brave-dev-loop", "brave-core", "0 1 * * * theirs", None)
        assert "theirs" in self._strip(block)

    def test_another_projects_block_survives(self):
        """One crontab, several deployments -- that is what the project name in
        the marker is for."""
        out = self._strip(self._block("reviewer", "bravebot", "0 3 * * * theirs"))
        assert "theirs" in out

    def test_entries_the_bot_never_wrote_survive(self):
        crontab = "\n".join(
            [
                "PATH=/usr/bin",
                "0 4 * * * backup",
                self._block("brave-dev-loop", "brave-core", "0 1 * * * old"),
            ]
        )
        out = self._strip(crontab)
        assert "PATH=/usr/bin" in out and "backup" in out and "old" not in out

    def test_a_project_name_with_regex_characters_matches_only_itself(self):
        crontab = "\n".join(
            [
                self._block("reviewer", "a.c", "0 1 * * * dotted"),
                self._block("reviewer", "abc", "0 2 * * * plain"),
            ]
        )
        out = self._strip(crontab, "a.c")
        assert "dotted" not in out and "plain" in out

    def test_nothing_is_ever_an_ungrouped_block_here(self):
        """brave-dev-loop grouped its jobs late; this repo never had an
        ungrouped block, so there is nothing for remove or install to refuse."""
        out = self._run(
            "bot_has_ungrouped_cron_block && echo yes || echo no",
            self._block("brave-dev-loop", "brave-core", "0 1 * * * old", None),
        )
        assert out.strip() == "no"

    def test_sync_schedules_uses_the_shared_stripper(self):
        """The marker spelling and its history belong in one place."""
        with open(os.path.join(SCRIPT_DIR, "sync-schedules.sh")) as f:
            body = f.read()
        assert "bot_strip_cron_blocks" in body
        assert "bot_cron_marker" in body


class TestAsReviewer:
    """Scheduled reviews act as a second GitHub account. The bot's token is
    exported into every cron job by .envrc and outranks any gh config directory,
    so the wrapper has to replace the token itself, and it must refuse rather
    than fall back to the bot when the reviewer cannot be used."""

    @staticmethod
    def _bot(tmp_dir, reviewer=None, login=None, actual=None):
        """A bot directory with a stub gh whose stored login is `actual`, or
        none, and whose config names `reviewer`."""
        bot = TestProjectSchedules._bot_dir(tmp_dir, "default")
        cfg_dir = os.path.join(tmp_dir, "xdg", "gh-" + (reviewer or "x"))
        os.makedirs(cfg_dir, exist_ok=True)
        if actual:
            with open(os.path.join(cfg_dir, "login"), "w") as f:
                f.write(actual)
        path = os.path.join(bot, "config.json")
        with open(path) as f:
            config = json.load(f)
        if reviewer:
            config["reviewer"] = {"username": reviewer, "ghConfigDir": cfg_dir}
        with open(path, "w") as f:
            json.dump(config, f)
        bindir = os.path.join(tmp_dir, "bin")
        os.makedirs(bindir, exist_ok=True)
        with open(os.path.join(bindir, "gh"), "w") as f:
            f.write(
                "#!/bin/bash\n"
                'case "$*" in\n'
                '  "auth token"*) [ -n "${GH_TOKEN:-}" ] && { echo "$GH_TOKEN"; exit 0; }\n'
                '     [ -f "$GH_CONFIG_DIR/login" ] || exit 1\n'
                '     echo "token-of-$(cat "$GH_CONFIG_DIR/login")" ;;\n'
                '  "api user"*) echo "${GH_TOKEN#token-of-}" ;;\n'
                "  *) exit 1 ;;\n"
                "esac\n"
            )
        os.chmod(os.path.join(bindir, "gh"), 0o755)
        return bot, bindir, cfg_dir

    def _run(self, bot, bindir):
        env = {
            **os.environ,
            "PATH": bindir + os.pathsep + os.environ["PATH"],
            "GH_TOKEN": "bot-token",
            "GH_CONFIG_DIR": "/the/bots/config",
        }
        return subprocess.run(
            [
                os.path.join(bot, "scripts", "as-reviewer.sh"),
                "--",
                "bash",
                "-c",
                'echo "$GH_TOKEN|$GH_CONFIG_DIR"',
            ],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_without_a_reviewer_the_command_runs_as_the_bot(self, tmp_dir):
        bot, bindir, _ = self._bot(tmp_dir)
        result = self._run(bot, bindir)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "bot-token|/the/bots/config"

    def test_with_a_reviewer_the_bots_token_is_replaced(self, tmp_dir):
        bot, bindir, cfg_dir = self._bot(tmp_dir, "rev", actual="rev")
        result = self._run(bot, bindir)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"token-of-rev|{cfg_dir}"

    def test_a_reviewer_who_is_not_logged_in_is_not_replaced_by_the_bot(self, tmp_dir):
        bot, bindir, _ = self._bot(tmp_dir, "rev")
        result = self._run(bot, bindir)
        assert result.returncode == 1
        assert "bot-token" not in result.stdout
        assert "make setup" in result.stderr

    def test_a_directory_logged_in_as_someone_else_is_refused(self, tmp_dir):
        bot, bindir, _ = self._bot(tmp_dir, "rev", actual="someone-else")
        result = self._run(bot, bindir)
        assert result.returncode == 1
        assert result.stdout == ""
        assert "someone-else" in result.stderr

    @pytest.mark.parametrize("reviewer, expected", [(None, "test-bot"), ("rev", "rev")])
    def test_review_requests_are_read_for_whoever_reviews(
        self, tmp_dir, reviewer, expected
    ):
        bot, _, cfg_dir = self._bot(tmp_dir, reviewer)
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"source {bot}/scripts/lib/load-config.sh && "
                'echo "$BOT_REVIEW_USERNAME|$BOT_REVIEWER_GH_CONFIG_DIR"',
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"{expected}|{cfg_dir if reviewer else ''}"

    def test_the_default_login_directory_is_under_config(self, tmp_dir):
        bot, _, _ = self._bot(tmp_dir, "rev")
        path = os.path.join(bot, "config.json")
        with open(path) as f:
            config = json.load(f)
        config["reviewer"] = {"username": "rev"}
        with open(path, "w") as f:
            json.dump(config, f)
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"source {bot}/scripts/lib/load-config.sh && "
                'echo "$BOT_REVIEWER_GH_CONFIG_DIR"',
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "XDG_CONFIG_HOME": "/xdg"},
        )
        assert result.stdout.strip() == "/xdg/gh-rev"


class TestProjectSchedules:
    """The crontab block is built by sync-schedules.sh, but the jobs in it come
    from projects/<profile>/schedules.sh — one file per project, so a machine
    running two of them installs two independent sets of jobs. This repo only
    reviews, so every job is a review sweep or the review-request poll."""

    GOLDEN = os.path.join(GOLDEN_DIR, "brave-core-cron.txt")

    @staticmethod
    def _bot_dir(tmp_dir, profile, name=None):
        """A bot directory holding the real scripts and profiles, configured
        for one project. sync-schedules.sh reads the config beside it, so the
        suite must not render against whatever this machine has installed."""
        bot = os.path.join(tmp_dir, "bot")
        os.makedirs(bot, exist_ok=True)
        for d in ("scripts", "projects"):
            dest = os.path.join(bot, d)
            if not os.path.isdir(dest):
                shutil.copytree(
                    os.path.join(REPO_ROOT, d),
                    dest,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
        config = {
            "project": {
                "name": name or profile,
                "org": "o",
                "prRepository": "o/p",
                "issueRepository": "o/p",
                "defaultBranch": "main",
                "profile": profile,
            },
            "bot": {"username": "test-bot", "claudeBin": "/usr/bin/claude"},
        }
        with open(os.path.join(bot, "config.json"), "w") as f:
            json.dump(config, f)
        return bot

    @classmethod
    def _render(cls, tmp_dir, profile, name=None):
        """The block sync-schedules.sh would install, with the bot directory
        replaced by a placeholder so the text does not depend on where it ran."""
        bot = cls._bot_dir(tmp_dir, profile, name)
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--print"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.replace(os.path.realpath(bot), "{BOT}").replace(
            bot, "{BOT}"
        )

    @staticmethod
    def _jobs(block):
        return [
            line
            for line in block.splitlines()
            if re.match(r"^[0-9*]", line)  # a cron line, not a comment or SHELL=
        ]

    @classmethod
    def _review_request_job(cls, tmp_dir, profile):
        (job,) = [
            j
            for j in cls._jobs(cls._render(tmp_dir, profile))
            if "review-requested.sh" in j
        ]
        return job

    def test_brave_core_jobs_are_what_is_installed_today(self, tmp_dir):
        """These jobs run unattended on a machine nobody watches. Moving them
        into a profile is a refactor, and a refactor that changes one cron line
        is a schedule change that no one asked for."""
        with open(self.GOLDEN) as f:
            assert self._render(tmp_dir, "brave-core") == f.read()

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot", "default"])
    def test_every_profile_has_review_jobs_and_only_review_jobs(self, tmp_dir, profile):
        jobs = self._jobs(self._render(tmp_dir, profile))
        assert jobs
        for job in jobs:
            assert "review-prs" in job, job
            assert "./run.sh" not in job and "sync-prd" not in job, job

    @pytest.mark.parametrize(
        "profile", ["brave-core", "bravebot", "default", "brave-dev-loop"]
    )
    def test_the_jobs_come_from_the_profiles_own_file(self, tmp_dir, profile):
        block = self._render(tmp_dir, profile)
        assert f"# Jobs: projects/{profile}/schedules.sh" in block
        with open(os.path.join(PROJECTS_DIR, profile, "schedules.sh")) as f:
            assert "schedule_review()" in f.read()

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot", "brave-dev-loop"])
    def test_a_gate_reports_into_its_jobs_log(self, tmp_dir, profile):
        """A gate that skipped because it could not ask GitHub has to say so
        somewhere. Its stdout is the routine "nothing to do" and stays out; its
        stderr goes to the same log the job writes."""
        gated = [
            j
            for j in self._jobs(self._render(tmp_dir, profile))
            if "./scripts/check-" in j
        ]
        assert gated
        for job in gated:
            log = re.search(r">> (\S+) 2>&1$", job).group(1)
            assert re.search(
                rf"\./scripts/check-[\w-]+\.sh 2>> {re.escape(log)} && \{{", job
            ), job

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot"])
    def test_a_review_requested_of_the_bot_is_polled_for(self, tmp_dir, profile):
        """Someone clicking "Request review" (or the re-request arrow) is an ask
        no schedule predicted. The poll is what turns it into a review, and it
        goes through the gate so the polls that find nothing cost nothing — no
        agent session appears in the crontab line at all."""
        job = self._review_request_job(tmp_dir, profile)
        assert (
            "./scripts/check-review-requests.sh 2>> {BOT}/logs/review-requested-cron.log"
            " && { git fetch origin" in job
        )
        assert "-- ./scripts/review-requested.sh" in job
        assert "/usr/bin/claude" not in job

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot"])
    def test_the_poll_runs_every_five_minutes(self, tmp_dir, profile):
        """The answer-within promise is the poll interval plus however long the
        queue ahead of a request takes. Fifteen minutes of that was the poll."""
        job = self._review_request_job(tmp_dir, profile)
        minutes = [int(m) for m in job.split()[0].split(",")]
        assert len(minutes) == 12
        assert all(b - a == 5 for a, b in zip(minutes, minutes[1:]))

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot"])
    def test_the_poll_does_not_wait_for_the_previous_poll(self, tmp_dir, profile):
        """A review runs for far longer than five minutes. A single exclusive
        lock would make every poll in between exit on it, so the interval would
        buy nothing at all — the queue would still drain one session at a time.
        The cap moves to a slot count, and review-requested.sh locks per PR."""
        job = self._review_request_job(tmp_dir, profile)
        assert "./scripts/with-lock.sh review-prs --slots 3 " in job

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot"])
    def test_a_poll_outlives_five_reviews_before_it_is_killed(self, tmp_dir, profile):
        """with-lock.sh kills the job at its timeout. Five reviews in one run
        do not fit in the two-hour default, and a run killed partway leaves the
        PRs it had not reached for the next poll — forever, if every run dies
        at the same point."""
        job = self._review_request_job(tmp_dir, profile)
        timeout = int(re.search(r"--timeout (\d+)", job).group(1))
        assert timeout >= 5 * 30 * 60

    def test_the_two_projects_poll_on_different_minutes(self, tmp_dir):
        """Deployed on one machine these are two checkouts, two crontab blocks
        and two locks — nothing stops them polling at once, and the job behind
        the gate builds a worktree per PR."""

        def minutes(profile):
            return set(self._review_request_job(tmp_dir, profile).split()[0].split(","))

        assert not (minutes("brave-core") & minutes("bravebot"))

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot"])
    def test_the_poll_and_the_sweep_draw_on_the_same_slots(self, tmp_dir, profile):
        """A PR review is a full checkout of the target repo per PR, so the
        total number running at once has to be bounded however they were
        started. One lock name, and — this is the part that bites — the same
        count from every job that uses it: a job asking for one slot takes slot
        1 only, and exits doing nothing whenever a job asking for three holds
        it. That is a sweep starved by the poll."""
        jobs = self._jobs(self._render(tmp_dir, profile))
        reviewing = [j for j in jobs if "review-prs" in j]
        # brave-core: weekday sweep, weekend sweep, the poll; bravebot: one
        # sweep job and the poll
        assert len(reviewing) == {"brave-core": 3, "bravebot": 2}[profile]
        counts = {
            re.search(r"with-lock\.sh review-prs --slots (\d+)", j).group(1)
            for j in reviewing
        }
        assert counts == {"3"}

    @pytest.mark.parametrize(
        "profile", ["brave-core", "bravebot", "default", "brave-dev-loop"]
    )
    def test_every_review_sweep_runs_on_the_review_model(self, tmp_dir, profile):
        """A sweep session over thirty PRs re-reads its own context on every
        turn; that is the part of the bill the model choice decides."""
        sweeps = [
            j
            for j in self._jobs(self._render(tmp_dir, profile))
            if "review-session.sh" in j
        ]
        assert sweeps
        for job in sweeps:
            assert (
                "./scripts/review-session.sh --model sonnet -- "
                "1d open --auto --reviewer-priority" in job
            )

    @pytest.mark.parametrize(
        "profile", ["brave-core", "bravebot", "default", "brave-dev-loop"]
    )
    def test_no_review_job_starts_a_session_with_every_tool(self, tmp_dir, profile):
        """A review reads the PR's source tree as the reviewer, which can write
        to the repository. Every review job goes through review-session.sh,
        which holds the session to narrow rules with no GitHub token; a review
        started straight from cron would get --allowedTools and the token."""
        reviewing = [
            j for j in self._jobs(self._render(tmp_dir, profile)) if "review-prs" in j
        ]
        assert reviewing
        for job in reviewing:
            assert "claude -p" not in job, job
            assert "review-session.sh" in job or "review-requested.sh" in job, job

    def test_bravebot_sweeps_four_times_a_day_and_answers_requests(self, tmp_dir):
        """One sweep job runs at 07:30, 12:30, 17:30 and 22:30, and an explicit
        request does not wait on it: the poll is a job of its own."""
        block = self._render(tmp_dir, "bravebot")
        assert "review-requested.sh" in block
        sweeps = [j for j in self._jobs(block) if "review-session.sh" in j]
        (sweep,) = sweeps
        assert sweep.split()[:5] == ["30", "7,12,17,22", "*", "*", "*"]

    def test_the_bravebot_sweeps_are_five_hours_apart_by_day(self, tmp_dir):
        """Four sweeps, so the daytime gaps are five hours and the overnight gap
        is the only longer one. A sweep bunched into one stretch of the day would
        leave the rest of it unreviewed."""
        sweeps = [
            j
            for j in self._jobs(self._render(tmp_dir, "bravebot"))
            if "review-session.sh" in j
        ]
        hours = sorted(int(h) for j in sweeps for h in j.split()[1].split(","))
        gaps = [b - a for a, b in zip(hours, hours[1:])] + [hours[0] + 24 - hours[-1]]
        assert len(hours) == 4
        assert gaps[:-1] == [5, 5, 5]
        assert gaps[-1] == 9

    def test_bravebot_sweep_minute_is_off_the_poll_minutes(self, tmp_dir):
        poll = self._review_request_job(tmp_dir, "bravebot").split()[0].split(",")
        assert "30" not in poll

    @pytest.mark.parametrize(
        "profile", ["brave-core", "bravebot", "default", "brave-dev-loop"]
    )
    def test_review_jobs_run_as_the_reviewer(self, tmp_dir, profile):
        """The sweeps and the review-request poll act as the reviewer account.
        The wrapper sits outside the lock, so the lock line every other test
        reads is unchanged."""
        jobs = self._jobs(self._render(tmp_dir, profile))
        assert jobs
        for job in jobs:
            assert (
                "&& ./scripts/as-reviewer.sh -- ./scripts/with-lock.sh review-prs"
                in job
            ), job

    @pytest.mark.parametrize(
        "profile", ["brave-core", "bravebot", "default", "brave-dev-loop"]
    )
    def test_every_job_runs_in_the_bot_dir_and_logs_there(self, tmp_dir, profile):
        for job in self._jobs(self._render(tmp_dir, profile)):
            assert " cd {BOT} && source .envrc" in job
            assert job.endswith("-cron.log 2>&1")

    @pytest.mark.parametrize(
        "profile", ["brave-core", "bravebot", "default", "brave-dev-loop"]
    )
    def test_every_job_resets_the_bot_repo_to_main_first(self, tmp_dir, profile):
        """A job that runs the checkout as it was left cannot pick up a fix.
        This repo's branch is main, not the target project's default branch
        (the config here says main too, so master would be a regression)."""
        for job in self._jobs(self._render(tmp_dir, profile)):
            assert "git checkout main && git reset --hard origin/main" in job
            assert "master" not in job

    def test_the_reset_branch_can_be_set_in_config(self, tmp_dir):
        bot = self._bot_dir(tmp_dir, "bravebot")
        path = os.path.join(bot, "config.json")
        with open(path) as f:
            config = json.load(f)
        config["project"]["botRepoBranch"] = "trunk"
        with open(path, "w") as f:
            json.dump(config, f)
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--print"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "git checkout trunk && git reset --hard origin/trunk" in result.stdout

    @pytest.mark.parametrize(
        "profile", ["brave-core", "bravebot", "default", "brave-dev-loop"]
    )
    def test_the_log_redirect_covers_the_whole_chain_not_just_the_last_command(
        self, tmp_dir, profile
    ):
        """A redirect applies to one command. Applied to the last one, a failure
        in the git sync ahead of it breaks the `&&` chain before anything is
        redirected: cron mails the error into the void and the job leaves an
        empty log, so it reads as a job that simply stopped running. That is how
        a broken target repo kept review-prs from running for 8 days. Grouping
        the chain puts every one of those failures in the log instead."""
        for job in self._jobs(self._render(tmp_dir, profile)):
            assert "&& { git fetch origin" in job, job
            assert re.search(r" ; \} >> \{BOT\}/logs/\S+\.log 2>&1$", job), job

    @pytest.mark.parametrize(
        "profile", ["brave-core", "bravebot", "default", "brave-dev-loop"]
    )
    def test_a_gate_that_finds_nothing_to_do_stays_outside_the_log(
        self, tmp_dir, profile
    ):
        """The gates run hundreds of times a day and almost always skip. Inside
        the group, every one of those skips would write to the log and bury the
        failures the group exists to capture."""
        for job in self._jobs(self._render(tmp_dir, profile)):
            before_group, _, _ = job.partition("{ git fetch origin")
            assert "./scripts/check-" in before_group, job

    def test_brave_dev_loop_syncs_no_target_repo(self, tmp_dir):
        """It has no fork and no upstream remote, so sync-target-repo.sh would
        exit having done nothing. Every other project's jobs run it."""
        for job in self._jobs(self._render(tmp_dir, "brave-dev-loop")):
            assert "sync-target-repo.sh" not in job, job
        for profile in ("brave-core", "bravebot"):
            for job in self._jobs(self._render(tmp_dir, profile)):
                assert "./scripts/sync-target-repo.sh && " in job, job

    def test_a_profile_with_no_schedules_file_gets_the_default_ones(self, tmp_dir):
        """Adding a project is still just a profile directory."""
        bot = self._bot_dir(tmp_dir, "brave-core")
        os.remove(os.path.join(bot, "projects", "brave-core", "schedules.sh"))
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--print"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "# Jobs: projects/default/schedules.sh" in result.stdout

    def test_no_default_schedules_file_either_is_an_error(self, tmp_dir):
        bot = self._bot_dir(tmp_dir, "brave-core")
        os.remove(os.path.join(bot, "projects", "brave-core", "schedules.sh"))
        os.remove(os.path.join(bot, "projects", "default", "schedules.sh"))
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--print"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "no schedules.sh" in result.stderr

    def test_a_schedules_file_without_review_jobs_is_an_error(self, tmp_dir):
        bot = self._bot_dir(tmp_dir, "brave-core")
        with open(
            os.path.join(bot, "projects", "brave-core", "schedules.sh"), "w"
        ) as f:
            f.write("#!/bin/bash\nschedule_run() { :; }\n")
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--print"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "defines no review jobs" in result.stderr

    def test_the_block_says_which_file_its_jobs_came_from(self, tmp_dir):
        """The operator's next question after reading a crontab line is where
        to change it."""
        block = self._render(tmp_dir, "bravebot")
        assert "# Jobs: projects/bravebot/schedules.sh" in block

    def test_the_block_is_marked_with_the_project_name_and_group(self, tmp_dir):
        """What keeps one project's install from stripping another's jobs."""
        block = self._render(tmp_dir, "bravebot", name="bravebot")
        assert block.startswith("# === reviewer (bravebot:review) scheduled jobs ===")
        assert block.rstrip().endswith("# === end reviewer (bravebot:review) ===")

    def test_the_marker_names_the_project_not_the_profile(self, tmp_dir):
        block = self._render(tmp_dir, "default", name="my-project")
        assert block.startswith("# === reviewer (my-project:review) scheduled jobs ===")

    def test_the_path_line_holds_the_agent_binarys_directory(self, tmp_dir):
        """Cron's PATH is not the shell's."""
        block = self._render(tmp_dir, "bravebot")
        assert re.search(r"^PATH=/usr/bin:/usr/local/sbin:", block, re.M)

    def test_printing_touches_no_crontab(self):
        """The suite runs on the machine whose schedules these are."""
        with open(os.path.join(SCRIPT_DIR, "sync-schedules.sh")) as f:
            body = f.read()
        assert body.index("if $PRINT_ONLY; then") < body.index("| crontab -")

    @classmethod
    def _group(cls, tmp_dir, profile, group):
        bot = cls._bot_dir(tmp_dir, profile)
        return subprocess.run(
            [
                os.path.join(bot, "scripts", "sync-schedules.sh"),
                "--print",
                "--group",
                group,
            ],
            capture_output=True,
            text=True,
        )

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot", "default"])
    def test_the_review_group_is_every_job_exactly_once(self, tmp_dir, profile):
        whole = self._jobs(self._render(tmp_dir, profile))
        result = self._group(tmp_dir, profile, "review")
        assert result.returncode == 0, result.stderr
        bot = os.path.join(tmp_dir, "bot")
        part = result.stdout.replace(os.path.realpath(bot), "{BOT}").replace(
            bot, "{BOT}"
        )
        assert self._jobs(part) == whole
        assert result.stdout.startswith(f"# === reviewer ({profile}:review) ")

    def test_all_is_the_review_group(self, tmp_dir):
        assert (
            self._group(tmp_dir, "bravebot", "all").stdout
            == self._group(tmp_dir, "bravebot", "review").stdout
        )

    @pytest.mark.parametrize("group", ["run", "maintenance", "nightly"])
    def test_a_group_this_repo_does_not_have_is_an_error(self, tmp_dir, group):
        """run and maintenance are brave-dev-loop's."""
        result = self._group(tmp_dir, "bravebot", group)
        assert result.returncode == 1
        assert f"unknown group '{group}'" in result.stderr
        assert result.stdout == ""

    def test_a_group_flag_with_no_value_is_an_error(self, tmp_dir):
        bot = self._bot_dir(tmp_dir, "bravebot")
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--group"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "--group needs a value" in result.stderr

    def test_an_unknown_option_prints_usage(self, tmp_dir):
        bot = self._bot_dir(tmp_dir, "bravebot")
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--bogus"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "Usage: sync-schedules.sh" in result.stderr

    def test_the_agent_binarys_directory_follows_reviewer_agent(self, tmp_dir):
        """A bravebot installed under nvm is invisible to cron's PATH, so the
        directory it lives in joins the block's PATH."""
        bot = self._bot_dir(tmp_dir, "bravebot")
        bindir = os.path.join(tmp_dir, "nvm", "bin")
        os.makedirs(bindir)
        path = os.path.join(bot, "config.json")
        with open(path) as f:
            config = json.load(f)
        config["bot"]["bravebotBin"] = os.path.join(bindir, "bravebot")
        config["reviewer"] = {"agent": "bravebot"}
        with open(path, "w") as f:
            json.dump(config, f)
        with open(os.path.join(bindir, "bravebot"), "w") as f:
            f.write("#!/bin/bash\n")
        os.chmod(os.path.join(bindir, "bravebot"), 0o755)
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--print"],
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "BOT_BRAVEBOT_BIN"},
        )
        assert result.returncode == 0, result.stderr
        assert f"PATH=/usr/bin:{bindir}:" in result.stdout

    def test_an_unsupported_review_agent_is_an_error(self, tmp_dir):
        bot = self._bot_dir(tmp_dir, "bravebot")
        path = os.path.join(bot, "config.json")
        with open(path) as f:
            config = json.load(f)
        config["reviewer"] = {"agent": "codex"}
        with open(path, "w") as f:
            json.dump(config, f)
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--print"],
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "BOT_REVIEW_AGENT"},
        )
        assert result.returncode == 1
        assert "unsupported agent 'codex'" in result.stderr


class TestRemoveSchedules:
    """remove-schedules.sh strips this project's block and nothing else. The
    suite runs on the machine whose schedules these are, so crontab is a fake
    on PATH that reads and writes a file in the temp directory."""

    FAKE_CRONTAB = """#!/bin/bash
f="$FAKE_CRONTAB_FILE"
case "$1" in
  -l) if [ -f "$f" ]; then cat "$f"; else echo "no crontab" >&2; exit 1; fi ;;
  -r) rm -f "$f" ;;
  -) cat > "$f" ;;
  *) echo "unexpected: $*" >&2; exit 2 ;;
esac
"""

    @staticmethod
    def _block(project, job, name="reviewer", group="review"):
        label = f"{project}:{group}" if group else project
        return "\n".join(
            [
                f"# === {name} ({label}) scheduled jobs ===",
                job,
                f"# === end {name} ({label}) ===",
            ]
        )

    def _run(
        self,
        tmp_dir,
        crontab,
        *args,
        script="remove-schedules.sh",
        profile="brave-core",
        returncode=0,
    ):
        bot = TestProjectSchedules._bot_dir(tmp_dir, profile)
        fake_bin = os.path.join(tmp_dir, "fake-bin")
        os.makedirs(fake_bin, exist_ok=True)
        fake = os.path.join(fake_bin, "crontab")
        with open(fake, "w") as f:
            f.write(self.FAKE_CRONTAB)
        os.chmod(fake, 0o755)
        tab = os.path.join(tmp_dir, "crontab.txt")
        if crontab is None:
            if os.path.exists(tab):
                os.remove(tab)
        else:
            with open(tab, "w") as f:
                f.write(crontab + "\n")
        env = dict(os.environ)
        env["PATH"] = fake_bin + os.pathsep + env.get("PATH", "")
        env["FAKE_CRONTAB_FILE"] = tab
        result = subprocess.run(
            [os.path.join(bot, "scripts", script), *args],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == returncode, result.stderr
        after = None
        if os.path.exists(tab):
            with open(tab) as f:
                after = f.read()
        if returncode:
            return result.stderr, after
        return result.stdout, after

    def test_this_projects_block_is_removed(self, tmp_dir):
        crontab = "\n".join(
            ["0 4 * * * backup", self._block("brave-core", "0 1 * * * mine")]
        )
        _, after = self._run(tmp_dir, crontab)
        assert "mine" not in after
        assert "reviewer (brave-core:review)" not in after
        assert "backup" in after

    def test_another_projects_block_survives(self, tmp_dir):
        crontab = "\n".join(
            [
                self._block("bravebot", "0 3 * * * theirs"),
                self._block("brave-core", "0 1 * * * mine"),
            ]
        )
        _, after = self._run(tmp_dir, crontab)
        assert "theirs" in after and "mine" not in after

    @pytest.mark.parametrize("name", ["brave-dev-loop", "brave-dev-bot"])
    def test_the_review_block_brave_dev_loop_wrote_is_removed_too(self, tmp_dir, name):
        """Otherwise both repos run every sweep and poll twice."""
        crontab = "\n".join(
            [
                self._block("brave-core", "0 1 * * * old", name=name),
                "0 4 * * * backup",
            ]
        )
        _, after = self._run(tmp_dir, crontab)
        assert "old" not in after and "backup" in after

    @pytest.mark.parametrize("group", ["run", "maintenance"])
    def test_brave_dev_loops_run_and_maintenance_blocks_are_left_alone(
        self, tmp_dir, group
    ):
        theirs = self._block(
            "brave-core", "0 1 * * * theirs", name="brave-dev-loop", group=group
        )
        crontab = "\n".join([theirs, self._block("brave-core", "0 2 * * * mine")])
        _, after = self._run(tmp_dir, crontab)
        assert "theirs" in after and "mine" not in after

    def test_a_crontab_left_empty_is_removed(self, tmp_dir):
        _, after = self._run(tmp_dir, self._block("brave-core", "0 1 * * * mine"))
        assert after is None

    def test_nothing_installed_is_not_an_error(self, tmp_dir):
        out, after = self._run(tmp_dir, None)
        assert after is None
        assert "No cron jobs installed" in out

    def test_a_crontab_without_this_project_is_left_as_it_was(self, tmp_dir):
        crontab = self._block("bravebot", "0 3 * * * theirs")
        out, after = self._run(tmp_dir, crontab)
        assert after == crontab + "\n"
        assert "No cron jobs installed" in out

    def test_removing_says_how_to_install_again(self, tmp_dir):
        out, _ = self._run(tmp_dir, self._block("brave-core", "0 1 * * * mine"))
        assert "Cron jobs removed for brave-core" in out
        assert "make schedules" in out

    def test_printing_touches_no_crontab(self, tmp_dir):
        crontab = "\n".join(
            ["0 4 * * * backup", self._block("brave-core", "0 1 * * * mine")]
        )
        out, after = self._run(tmp_dir, crontab, "--print")
        assert after == crontab + "\n"
        assert "backup" in out and "mine" not in out

    def test_it_uses_the_shared_stripper(self):
        """The marker spelling and its history belong in one place."""
        with open(os.path.join(SCRIPT_DIR, "remove-schedules.sh")) as f:
            assert "bot_strip_cron_blocks" in f.read()

    @pytest.mark.parametrize("args", [("--group", "review"), ("--group", "all")])
    def test_the_review_group_is_the_whole_removal(self, tmp_dir, args):
        crontab = "\n".join(
            [
                "0 4 * * * backup",
                self._block("brave-core", "0 2 * * * reviewjob"),
                self._block("bravebot", "0 3 * * * theirs"),
            ]
        )
        _, after = self._run(tmp_dir, crontab, *args)
        assert "reviewjob" not in after
        assert "backup" in after and "theirs" in after

    @pytest.mark.parametrize("group", ["run", "maintenance", "nightly"])
    def test_a_group_this_repo_does_not_have_removes_nothing(self, tmp_dir, group):
        crontab = self._block("brave-core", "0 1 * * * mine")
        err, after = self._run(tmp_dir, crontab, "--group", group, returncode=1)
        assert f"unknown group '{group}'" in err
        assert after == crontab + "\n"

    def test_a_group_flag_with_no_value_removes_nothing(self, tmp_dir):
        crontab = self._block("brave-core", "0 1 * * * mine")
        err, after = self._run(tmp_dir, crontab, "--group", returncode=1)
        assert "--group needs a value" in err
        assert after == crontab + "\n"

    def test_an_unknown_option_removes_nothing(self, tmp_dir):
        crontab = self._block("brave-core", "0 1 * * * mine")
        err, after = self._run(tmp_dir, crontab, "--bogus", returncode=1)
        assert "Usage: remove-schedules.sh" in err
        assert after == crontab + "\n"


class TestInstallSchedulesByGroup:
    """make schedules and make schedules-review install the one group this repo
    has, as a block that lives beside every other project's and beside
    brave-dev-loop's run and maintenance blocks. The crontab is the fake
    TestRemoveSchedules puts on PATH."""

    def _sync(self, tmp_dir, crontab, *args, **kw):
        return TestRemoveSchedules()._run(
            tmp_dir, crontab, *args, script="sync-schedules.sh", **kw
        )

    def test_the_review_group_installs_the_review_jobs(self, tmp_dir):
        out, after = self._sync(tmp_dir, None, "--group", "review")
        assert "review-requested.sh" in after and "review-session.sh" in after
        assert "./run.sh " not in after and "sync-prd.sh" not in after
        assert "# === reviewer (brave-core:review) scheduled jobs ===" in after
        assert "# === end reviewer (brave-core:review) ===" in after
        assert "Cron jobs installed for brave-core" in out

    @pytest.mark.parametrize("args", [(), ("--group", "all")])
    def test_no_group_and_all_install_the_same_as_review(self, tmp_dir, args):
        _, review = self._sync(tmp_dir, None, "--group", "review")
        _, other = self._sync(tmp_dir, None, *args)
        assert other == review

    def test_installing_a_group_again_replaces_it_not_duplicates_it(self, tmp_dir):
        _, once = self._sync(tmp_dir, None, "--group", "review")
        _, twice = self._sync(tmp_dir, once.rstrip("\n"), "--group", "review")
        assert twice.count("scheduled jobs ===") == 1
        assert twice.count("review-requested.sh") == once.count("review-requested.sh")

    def test_installing_leaves_other_lines_and_projects_alone(self, tmp_dir):
        theirs = "\n".join(
            [
                "0 4 * * * backup",
                "# === reviewer (bravebot:review) scheduled jobs ===",
                "0 3 * * * theirs",
                "# === end reviewer (bravebot:review) ===",
            ]
        )
        _, after = self._sync(tmp_dir, theirs)
        assert "backup" in after and "theirs" in after
        assert "(brave-core:review) scheduled jobs" in after

    @pytest.mark.parametrize("name", ["brave-dev-loop", "brave-dev-bot"])
    def test_installing_replaces_the_review_block_brave_dev_loop_wrote(
        self, tmp_dir, name
    ):
        """Left in place, every sweep and every poll would run twice."""
        old = "\n".join(
            [
                "0 4 * * * backup",
                f"# === {name} (brave-core:review) scheduled jobs ===",
                "0 1 * * * stale",
                f"# === end {name} (brave-core:review) ===",
            ]
        )
        _, after = self._sync(tmp_dir, old)
        assert "stale" not in after and "backup" in after
        assert name not in after
        assert "(brave-core:review) scheduled jobs" in after

    def test_installing_leaves_brave_dev_loops_run_and_maintenance_blocks(
        self, tmp_dir
    ):
        theirs = "\n".join(
            [
                "# === brave-dev-loop (brave-core:run) scheduled jobs ===",
                "0 1 * * * runjob",
                "# === end brave-dev-loop (brave-core:run) ===",
                "# === brave-dev-loop (brave-core:maintenance) scheduled jobs ===",
                "0 2 * * * maintjob",
                "# === end brave-dev-loop (brave-core:maintenance) ===",
            ]
        )
        _, after = self._sync(tmp_dir, theirs)
        assert "runjob" in after and "maintjob" in after
        assert "# === reviewer (brave-core:review) scheduled jobs ===" in after

    @pytest.mark.parametrize("group", ["run", "maintenance"])
    def test_a_group_this_repo_does_not_have_installs_nothing(self, tmp_dir, group):
        err, after = self._sync(tmp_dir, None, "--group", group, returncode=1)
        assert f"unknown group '{group}'" in err
        assert after is None

    def test_a_profile_without_a_file_installs_the_default_jobs(self, tmp_dir):
        out, after = self._sync(tmp_dir, None, profile="default")
        assert "projects/default/schedules.sh" in out
        assert "# === reviewer (default:review) scheduled jobs ===" in after

    def test_the_log_directory_is_created_on_install_not_on_print(self, tmp_dir):
        bot = TestProjectSchedules._bot_dir(tmp_dir, "brave-core")
        TestProjectSchedules._render(tmp_dir, "brave-core")
        assert not os.path.isdir(os.path.join(bot, "logs"))
        self._sync(tmp_dir, None)
        assert os.path.isdir(os.path.join(bot, "logs"))

    def test_remove_undoes_install(self, tmp_dir):
        _, after = self._sync(tmp_dir, "0 4 * * * backup")
        assert "(brave-core:review)" in after
        _, after = TestRemoveSchedules()._run(
            tmp_dir, after.rstrip("\n"), "--group", "review"
        )
        assert "(brave-core:review)" not in after
        assert after.strip() == "0 4 * * * backup"
        _, after = self._sync(tmp_dir, None)
        _, after = TestRemoveSchedules()._run(tmp_dir, after.rstrip("\n"))
        assert after is None

    def test_make_has_a_target_per_group_and_no_run_ones(self):
        with open(os.path.join(REPO_ROOT, "Makefile")) as f:
            makefile = f.read()
        for target, script in (
            ("schedules", "sync-schedules.sh"),
            ("schedules-review", "sync-schedules.sh --group review"),
            ("remove-schedules", "remove-schedules.sh"),
            ("remove-schedules-review", "remove-schedules.sh --group review"),
        ):
            assert re.search(
                rf"^{target}:\n\t\./scripts/{re.escape(script)}$", makefile, re.M
            ), target
            assert re.search(rf"^\.PHONY:.* {target}( |$)", makefile, re.M), target
        assert not re.search(r"^(remove-)?schedules-(run|maintenance):", makefile, re.M)


class TestNewPrsGate:
    """scripts/check-new-prs.sh, the gate in front of the scheduled sweep."""

    GATE = os.path.join(SCRIPT_DIR, "check-new-prs.sh")

    @staticmethod
    def _gh(tmp_dir, updated=(), rc=0):
        bindir = os.path.join(tmp_dir, "bin")
        os.makedirs(bindir, exist_ok=True)
        gh = os.path.join(bindir, "gh")
        lines = "".join(f"echo {u}\n" for u in updated)
        with open(gh, "w") as f:
            f.write(
                "#!/bin/bash\n"
                'echo "$*" >> "$GH_LOG"\n'
                f"[ {rc} -eq 0 ] || {{ echo 'gh: API rate limit exceeded' >&2; exit {rc}; }}\n"
                + lines
            )
        os.chmod(gh, 0o755)
        return bindir

    def _run(self, tmp_dir, bindir):
        log = os.path.join(tmp_dir, "gh.log")
        result = subprocess.run(
            [self.GATE],
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "GH_LOG": log},
        )
        with open(log) as f:
            return result, f.read()

    @staticmethod
    def _iso(**delta):
        return (datetime.now(timezone.utc) - timedelta(**delta)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def test_a_pr_updated_today_opens_the_gate(self, tmp_dir):
        bindir = self._gh(tmp_dir, [self._iso(days=5), self._iso(hours=3)])
        result, gh_log = self._run(tmp_dir, bindir)
        assert result.returncode == 0, result.stderr
        assert "pr list" in gh_log

    def test_only_old_prs_stop_at_the_gate(self, tmp_dir):
        bindir = self._gh(tmp_dir, [self._iso(days=5), self._iso(hours=25)])
        result, _ = self._run(tmp_dir, bindir)
        assert result.returncode == 1
        assert "skipping review-prs" in result.stdout

    def test_no_open_prs_stop_at_the_gate(self, tmp_dir):
        result, _ = self._run(tmp_dir, self._gh(tmp_dir))
        assert result.returncode == 1

    def test_does_not_use_the_search_api(self, tmp_dir):
        """`gh search` has a rate limit of its own, shared with the
        review-request poll."""
        _, gh_log = self._run(tmp_dir, self._gh(tmp_dir, [self._iso(hours=1)]))
        assert "search" not in gh_log.split()


class TestCheckNewPrsGate:
    """The review sweep's gate. A gh failure used to be read as "0 PRs", so two
    sweeps in a row were skipped with nothing in any log."""

    GATE = os.path.join(SCRIPT_DIR, "check-new-prs.sh")

    @staticmethod
    def _run(tmp_dir, failures, answer=None, attempts=3):
        """A gh that fails its first `failures` calls, then answers with the
        updatedAt of each open PR, one per line."""
        if answer is None:
            answer = TestNewPrsGate._iso(hours=1)
        bindir = os.path.join(tmp_dir, "bin")
        os.makedirs(bindir, exist_ok=True)
        calls = os.path.join(tmp_dir, "calls")
        gh = os.path.join(bindir, "gh")
        with open(gh, "w") as f:
            f.write(
                "#!/bin/bash\n"
                f'echo x >> "{calls}"\n'
                f'n=$(wc -l < "{calls}")\n'
                f"[ \"$n\" -gt {failures} ] || {{ echo 'HTTP 403: rate limit' >&2; exit 1; }}\n"
                f"printf '%s' '{answer}'\n"
            )
        os.chmod(gh, 0o755)
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "CHECK_NEW_PRS_ATTEMPTS": str(attempts),
            "CHECK_NEW_PRS_RETRY_DELAY": "0",
        }
        result = subprocess.run(
            [TestCheckNewPrsGate.GATE], capture_output=True, text=True, env=env
        )
        with open(calls) as f:
            return result, len(f.read().splitlines())

    def test_recent_prs_open_the_gate_quietly(self, tmp_dir):
        result, calls = self._run(tmp_dir, failures=0)
        assert result.returncode == 0
        assert result.stderr == ""
        assert calls == 1

    def test_no_recent_prs_closes_the_gate_quietly(self, tmp_dir):
        """A routine skip writes nothing to stderr, which the cron line sends
        to the job's log."""
        result, _ = self._run(tmp_dir, failures=0, answer="")
        assert result.returncode == 1
        assert result.stderr == ""

    def test_a_transient_gh_failure_is_retried(self, tmp_dir):
        result, calls = self._run(tmp_dir, failures=2)
        assert result.returncode == 0, result.stderr
        assert calls == 3

    def test_a_persistent_gh_failure_is_reported_not_read_as_empty(self, tmp_dir):
        result, calls = self._run(tmp_dir, failures=99)
        assert result.returncode == 1
        assert calls == 3
        assert "could not query" in result.stderr
        assert "gh: HTTP 403: rate limit" in result.stderr
        assert "No open PRs" not in result.stdout


class TestReviewSessionSettings:
    """The rules a scheduled review session runs under (review-session.sh)."""

    SCRIPT = os.path.join(SCRIPT_DIR, "review-session-settings.py")

    def _settings(self, tmp_dir, *extra):
        work = os.path.join(tmp_dir, "review-prs-x")
        result = subprocess.run(
            ["python3", self.SCRIPT, "--work-dir", work, *extra],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return work, json.loads(result.stdout)["permissions"]

    def test_writes_stay_in_the_work_directory(self, tmp_dir):
        """Edit rules govern every file-writing tool; a Write(...) path rule is
        ignored, so the confinement has to be spelled Edit."""
        work, rules = self._settings(tmp_dir)
        edits = [r for r in rules["allow"] if r.startswith(("Edit", "Write"))]
        assert edits == [f"Edit(/{os.path.abspath(work)}/**)"]

    def test_no_tool_is_allowed_whole(self, tmp_dir):
        """A bare Bash allows any command, and a bare Read any file, the
        credential stores included; reads come from the working directories."""
        _, rules = self._settings(tmp_dir)
        assert not {"Bash", "Read", "Edit", "Write", "WebFetch"} & set(rules["allow"])
        bash = [r for r in rules["allow"] if r.startswith("Bash(")]
        assert bash and all("select-candidates.py *)" in r for r in bash)

    def test_the_web_is_denied(self, tmp_dir):
        _, rules = self._settings(tmp_dir)
        assert {"WebFetch", "WebSearch"} <= set(rules["deny"])

    def test_credential_stores_are_unreadable(self, tmp_dir):
        """cat and grep run without a rule in every mode; a Read deny is what
        stops them on a path. .envrc is inside the bot directory, a working
        directory, and /proc holds every ancestor's environment."""
        gh_dir = os.path.join(tmp_dir, "gh-reviewer")
        os.makedirs(gh_dir)
        _, rules = self._settings(tmp_dir, "--deny-read", gh_dir)
        deny = rules["deny"]
        home = os.path.expanduser("~")
        for path in (
            f"{home}/.git-credentials",
            f"{home}/.config/gh/**",
            f"{os.path.realpath(REPO_ROOT)}/.envrc",
            "/proc/**",
            f"{gh_dir}/**",
        ):
            assert f"Read(/{path})" in deny, path


class TestReviewQueueWindow:
    """The queue is the members' PRs, capped after the org filter. With the cap
    on the GitHub query, 20 newer PRs from non-members left a member's older
    review request out of the queue (#40827 was never reviewed)."""

    def _queue(self, tmp_dir, prs, limit=None):
        bindir = os.path.join(tmp_dir, "bin")
        os.makedirs(bindir, exist_ok=True)
        # A gh that honours --limit, newest first, as `gh pr list` does.
        with open(os.path.join(bindir, "gh"), "w") as f:
            f.write(
                "#!/bin/bash\n"
                'while [ $# -gt 0 ]; do [ "$1" = --limit ] && lim=$2; shift; done\n'
                f"printf '%s\\n' {' '.join(shlex.quote(p) for p in prs)} | head -n \"$lim\"\n"
            )
        os.chmod(os.path.join(bindir, "gh"), 0o755)
        members = os.path.join(tmp_dir, "org-members.txt")
        with open(members, "w") as f:
            f.write("alice\n")
        script = (
            f"BOT_DIR={shlex.quote(os.path.abspath(REPO_ROOT))} BOT_PR_REPO=o/r "
            "BOT_REVIEW_USERNAME=rev; "
            f"source {shlex.quote(os.path.abspath(REPO_ROOT))}/scripts/lib/review-requests.sh; "
            f"bot_review_requested_prs {limit or ''}"
        )
        result = subprocess.run(
            ["bash", "-c", "set -euo pipefail; " + script],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "BOT_ORG_MEMBERS_FILE": members,
            },
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.split()

    def test_a_members_pr_behind_many_outsiders_is_still_queued(self, tmp_dir):
        outsiders = [f"{n} mallory" for n in range(300, 270, -1)]
        queue = self._queue(tmp_dir, outsiders + ["50 alice"])
        assert queue == ["50"]

    def test_the_limit_counts_members_only(self, tmp_dir):
        prs = []
        for n in range(200, 100, -1):
            prs += [f"{n} alice", f"{n}0 mallory"]
        assert len(self._queue(tmp_dir, prs)) == 50
        assert self._queue(tmp_dir, prs, limit=7) == [
            str(n) for n in range(200, 193, -1)
        ]


class TestReviewRequestQueue:
    """The queue is GitHub's own: a PR where the bot is a requested reviewer.
    Nothing local records what has been answered, which is what makes the answer
    the same on every machine the reviewer is deployed to — the bot submitting a
    review is what removes the PR from the queue, and a re-request is what puts
    it back."""

    GATE = os.path.join(SCRIPT_DIR, "check-review-requests.sh")
    JOB = os.path.join(SCRIPT_DIR, "review-requested.sh")

    @staticmethod
    def _configured_bot():
        """Whichever account lib/load-config.sh resolves here as the one that
        reviews. The shell loader reads config.json beside it, so the suite must
        not assume a name."""
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"source {os.path.join(SCRIPT_DIR, 'lib', 'load-config.sh')}"
                ' >/dev/null && printf "%s" "$BOT_REVIEW_USERNAME"',
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    @staticmethod
    def _stubs(
        tmp_dir,
        queue="101\n102\n103",
        gh_rc=0,
        failing_pr=None,
        head="",
        requested="",
        lagging=False,
    ):
        """A gh that answers the queue query and a claude that records its
        prompt, both ahead of the real ones on PATH. `head` is every PR's head
        commit and `requested` the reviewers still asked for after a session;
        with no head the job has nothing to count attempts against. `lagging`
        lists `requested` on the first read only, as GitHub does in the second
        after a review is submitted."""
        bindir = os.path.join(tmp_dir, "bin")
        os.makedirs(bindir, exist_ok=True)
        gh = os.path.join(bindir, "gh")
        with open(gh, "w") as f:
            f.write(
                "#!/bin/bash\n"
                'echo "$*" >> "$GH_LOG"\n'
                f"[ {gh_rc} -eq 0 ] || {{ echo 'gh: bad credentials' >&2; exit {gh_rc}; }}\n"
                'case "$*" in\n'
                # The ping queue's search: nobody has pinged. tests/test_pings.py
                # covers a queue with something in it.
                "  *search/issues*) exit 0 ;;\n"
                f"  *'pr view'*headRefOid*) echo '{head}'; exit 0 ;;\n"
                "  *'pr view'*reviewRequests*)\n"
                + (
                    f"    [ -e {tmp_dir}/requested.read ] && exit 0\n"
                    f"    touch {tmp_dir}/requested.read\n"
                    if lagging
                    else ""
                )
                + f"    printf '%s\\n' {requested!r}; exit 0 ;;\n"
                "esac\n"
                # The queue query asks for "<number> <author>" lines.
                f"printf '%s' '{queue}' | while read -r n || [ -n \"$n\" ]; do echo \"$n alice\"; done\n"
            )
        os.chmod(gh, 0o755)
        claude = os.path.join(bindir, "claude")
        with open(claude, "w") as f:
            f.write(
                "#!/bin/bash\n"
                'echo "$* GH_TOKEN=${GH_TOKEN-unset} GITHUB_TOKEN=${GITHUB_TOKEN-unset}'
                # `tr`: BSD wc (macOS) pads the count with spaces, GNU wc does not.
                ' GH_CONFIG_DIR=$(ls -A "$GH_CONFIG_DIR" | wc -l | tr -d " ")-entries cwd=$PWD" >> "$CLAUDE_LOG"\n'
                + (
                    f'case "$*" in *"#{failing_pr}"*) exit 2 ;; esac\n'
                    if failing_pr
                    else ""
                )
                + "exit 0\n"
            )
        os.chmod(claude, 0o755)
        # The wrapper's two GitHub steps, stubbed through REVIEW_PRS_SKILL_DIR.
        # The work directory is named for the PR ("pr#101-..."), so the session's
        # `--work-dir` says which PR it is reviewing, as its prompt used to.
        skill = os.path.join(tmp_dir, "skill")
        os.makedirs(skill, exist_ok=True)
        with open(os.path.join(skill, "prepare-review.py"), "w") as f:
            f.write(
                "import json, os, sys, tempfile\n"
                "open(os.environ['PREPARE_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                "d = tempfile.mkdtemp(prefix=f'pr{sys.argv[1]}-', dir=os.environ['WORK_ROOT'])\n"
                "m = {'auto_mode': '--auto' in sys.argv}\n"
                "if 'NEEDS_MODEL' in os.environ: m['needs_model'] = os.environ['NEEDS_MODEL'] == 'true'\n"
                "json.dump(m, open(f'{d}/manifest.json', 'w'))\n"
                "print(json.dumps({'work_dir': d}))\n"
            )
        with open(os.path.join(skill, "select-candidates.py"), "w") as f:
            f.write(
                "import os, sys\n"
                "open(os.environ['SELECT_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                "print('{}')\n"
            )
        with open(os.path.join(skill, "collect-results.py"), "w") as f:
            f.write(
                "import os, sys\n"
                "open(os.environ['COLLECT_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            )
        return bindir

    @staticmethod
    def _hold_pr_lock(tmp_dir, pr):
        """Another run, mid-review of `pr`: a live process holding its lock.

        A separate process and not a lock file written by hand, because the
        lock is the kernel's — the file on disk is just the inode it lives on,
        and its presence says nothing about whether anything is running."""
        lock = os.path.join(REPO_ROOT, ".ignore", f".review-pr-{pr}.lock")
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        proc = subprocess.Popen(
            [
                "bash",
                "-c",
                f"source {os.path.join(SCRIPT_DIR, 'lib', 'lock.sh')}\n"
                f'bot_acquire_lock "{lock}" || exit 1\n'
                "echo held\nexec sleep 300",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert proc.stdout.readline().strip() == "held", "could not take the PR lock"
        return proc

    def _run(self, script, tmp_dir, bindir, env=None):
        logs = {
            "GH_LOG": os.path.join(tmp_dir, "gh.log"),
            "CLAUDE_LOG": os.path.join(tmp_dir, "claude.log"),
            "PREPARE_LOG": os.path.join(tmp_dir, "prepare.log"),
            "COLLECT_LOG": os.path.join(tmp_dir, "collect.log"),
            "SELECT_LOG": os.path.join(tmp_dir, "select.log"),
        }
        work_root = os.path.join(tmp_dir, "work")
        os.makedirs(work_root, exist_ok=True)
        logs["WORK_ROOT"] = work_root
        members = os.path.join(tmp_dir, "org-members.txt")
        with open(members, "w") as f:
            f.write("alice\n")
        logs["BOT_ORG_MEMBERS_FILE"] = members
        logs["REVIEW_PRS_SKILL_DIR"] = os.path.join(tmp_dir, "skill")
        result = subprocess.run(
            [script],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "REVIEW_REQUESTED_SETTLE_SECONDS": "0",
                **logs,
                **(env or {}),
            },
        )

        def read(path):
            try:
                with open(path) as f:
                    return [line.strip() for line in f if line.strip()]
            except FileNotFoundError:
                return []

        return result, read(logs["GH_LOG"]), read(logs["CLAUDE_LOG"])

    def test_an_empty_queue_stops_at_the_gate(self, tmp_dir):
        """288 polls a day, and all but a few find nothing. The gate runs before
        the git sync and before any agent, so those cost one API call."""
        result, _, _ = self._run(self.GATE, tmp_dir, self._stubs(tmp_dir, queue=""))
        assert result.returncode == 1, result.stdout
        assert "nothing to do" in result.stdout

    def test_a_failed_query_is_not_an_empty_queue_but_stops_all_the_same(self, tmp_dir):
        """An expired token answers every question with silence. Treating that
        as work would start a session to review nothing."""
        result, _, _ = self._run(self.GATE, tmp_dir, self._stubs(tmp_dir, gh_rc=1))
        assert result.returncode == 1
        assert "Could not query review requests" in result.stderr

    def test_a_run_with_nothing_for_a_model_starts_no_agent(self, tmp_dir):
        """A re-request on a PR whose patch is unchanged since the last review
        is answered by prepare-review.py, select-candidates.py and
        collect-results.py. The agent binary must not run at all."""
        result, _, claude_log = self._run(
            self.JOB,
            tmp_dir,
            self._stubs(tmp_dir, queue="101"),
            {"NEEDS_MODEL": "false"},
        )
        assert result.returncode == 0, result.stderr
        assert claude_log == [], "an unchanged patch started a model session"
        assert len(self._read(os.path.join(tmp_dir, "prepare.log"))) == 1
        assert len(self._read(os.path.join(tmp_dir, "select.log"))) == 1
        assert "--auto" in self._read(os.path.join(tmp_dir, "collect.log"))[0]

    def test_a_run_with_something_to_read_still_starts_the_agent(self, tmp_dir):
        for env in ({"NEEDS_MODEL": "true"}, {}):
            result, _, claude_log = self._run(
                self.JOB, tmp_dir, self._stubs(tmp_dir, queue="101"), env
            )
            assert result.returncode == 0, result.stderr
            assert len(claude_log) >= 1, env

    def test_a_queued_request_opens_the_gate_and_names_its_prs(self, tmp_dir):
        result, gh_log, _ = self._run(self.GATE, tmp_dir, self._stubs(tmp_dir))
        assert result.returncode == 0, result.stderr
        assert "101 102 103" in result.stdout
        assert f"review-requested:{self._configured_bot()}" in gh_log[0]
        assert "--state open" in gh_log[0]

    def test_the_job_reviews_every_queued_pr_in_its_own_session(self, tmp_dir):
        """A review is most of a context window on its own. One session per PR,
        and the skill fans out to subagents inside each one."""
        result, _, claude_log = self._run(
            self.JOB, tmp_dir, self._stubs(tmp_dir), {"REVIEW_REQUESTED_MAX_PRS": "3"}
        )
        assert result.returncode == 0, result.stderr
        assert len(claude_log) == 3
        prepared = self._read(os.path.join(tmp_dir, "prepare.log"))
        collected = self._read(os.path.join(tmp_dir, "collect.log"))
        for pr, prep, invocation, collect in zip(
            (101, 102, 103), prepared, claude_log, collected
        ):
            assert prep == f"#{pr} open --auto"
            assert "-p /review-prs --work-dir " in invocation
            assert f"pr#{pr}-" in invocation
            assert f"pr#{pr}-" in collect and collect.endswith("--auto")

    @staticmethod
    def _read(path):
        with open(path) as f:
            return [line.strip() for line in f if line.strip()]

    def test_the_session_may_launch_subagents(self, tmp_dir):
        """Without Task the skill's whole file-based pipeline runs in the one
        session it was trying to keep small."""
        _, _, claude_log = self._run(self.JOB, tmp_dir, self._stubs(tmp_dir))
        assert '"Agent"' in claude_log[0] and '"Task"' in claude_log[0]

    def test_the_session_holds_no_github_credential(self, tmp_dir):
        """The session reads untrusted text and the reviewer can write to the
        repository. Prepare and collect run outside it, so it needs no token,
        and gh's default config directory would hand it the bot's."""
        _, _, claude_log = self._run(
            self.JOB,
            tmp_dir,
            self._stubs(tmp_dir),
            {"GH_TOKEN": "reviewer-token", "GITHUB_TOKEN": "bot-token"},
        )
        assert "GH_TOKEN=unset GITHUB_TOKEN=unset" in claude_log[0]
        assert "GH_CONFIG_DIR=0-entries" in claude_log[0]
        assert "reviewer-token" not in claude_log[0]

    def test_the_session_refuses_what_its_rules_do_not_allow(self, tmp_dir):
        """dontAsk turns every unlisted tool call into a refusal; under -p alone
        the --allowedTools list was every tool the skill could want."""
        _, _, claude_log = self._run(self.JOB, tmp_dir, self._stubs(tmp_dir))
        assert "--permission-mode dontAsk" in claude_log[0]
        assert "--allowedTools" not in claude_log[0]
        assert f"cwd={os.path.realpath(REPO_ROOT)}" in claude_log[0]

    def test_a_failed_session_is_still_collected(self, tmp_dir):
        """The collector removes the run's worktrees and leaves out a PR whose
        validator wrote nothing, so it runs whatever the session did."""
        result, _, claude_log = self._run(
            self.JOB,
            tmp_dir,
            self._stubs(tmp_dir, queue="101", failing_pr=101),
            {"REVIEW_REQUESTED_MAX_PRS": "1"},
        )
        assert len(claude_log) == 1
        assert len(self._read(os.path.join(tmp_dir, "collect.log"))) == 1
        assert "exited 2" in result.stderr

    def test_the_session_loads_no_mcp_server(self, tmp_dir):
        """The PR under review is untrusted text, and a server in the operator's
        ~/.claude.json can hold their own accounts."""
        _, _, claude_log = self._run(self.JOB, tmp_dir, self._stubs(tmp_dir))
        assert "--strict-mcp-config" in claude_log[0]

    def test_the_session_runs_on_the_review_model(self, tmp_dir):
        """The session runs scripts and launches subagents that choose their own
        model; on the default model its turns cost several times as much."""
        _, _, claude_log = self._run(
            self.JOB, tmp_dir, self._stubs(tmp_dir), {"BOT_REVIEW_MODEL": ""}
        )
        assert "--model sonnet" in claude_log[0]

    def test_the_cap_leaves_the_rest_of_the_queue_for_the_next_poll(self, tmp_dir):
        """Five minutes later there is another poll, and it does not wait for
        this one. Reviewing the whole queue in one job is how a backlog turns
        into a job that never ends."""
        result, _, claude_log = self._run(
            self.JOB, tmp_dir, self._stubs(tmp_dir), {"REVIEW_REQUESTED_MAX_PRS": "2"}
        )
        assert result.returncode == 0, result.stderr
        assert len(claude_log) == 2
        assert "#103" in result.stdout

    def test_five_prs_a_run_by_default(self, tmp_dir):
        """The cap and the poll interval together are the answer-within time
        for a request at the back of the queue. Two per run left eight queued
        requests draining for over two hours."""
        result, _, claude_log = self._run(
            self.JOB,
            tmp_dir,
            self._stubs(tmp_dir, queue="\n".join(str(n) for n in range(101, 108))),
        )
        assert result.returncode == 0, result.stderr
        assert len(claude_log) == 5
        assert "#106 #107" in result.stdout

    def test_a_pr_another_run_is_reviewing_is_walked_past(self, tmp_dir):
        """GitHub only drops a PR from the queue once the review is submitted,
        so an overlapping run sees the PR being reviewed right now at the front
        of its own queue. Without the per-PR lock every run would start on the
        same PR and post the same review."""
        held = self._hold_pr_lock(tmp_dir, 101)
        try:
            result, _, claude_log = self._run(
                self.JOB,
                tmp_dir,
                self._stubs(tmp_dir),
                {"REVIEW_REQUESTED_MAX_PRS": "2"},
            )
        finally:
            held.kill()
            held.wait()
        assert result.returncode == 0, result.stderr
        reviewed = [pr for pr in (101, 102, 103) if f"pr#{pr}-" in " ".join(claude_log)]
        assert reviewed == [102, 103], claude_log
        assert "skipped #101" in result.stdout

    def test_a_pr_held_by_another_run_does_not_use_up_the_cap(self, tmp_dir):
        """Counting a PR this run never reviewed would make an overlapping run
        shrink the one behind it: two runs, and the second does one review."""
        held = self._hold_pr_lock(tmp_dir, 101)
        try:
            _, _, claude_log = self._run(
                self.JOB,
                tmp_dir,
                self._stubs(tmp_dir),
                {"REVIEW_REQUESTED_MAX_PRS": "2"},
            )
        finally:
            held.kill()
            held.wait()
        assert len(claude_log) == 2, claude_log

    def test_a_skipped_pr_is_not_reported_as_a_failed_review(self, tmp_dir):
        """The subshell signals "someone else has this" with an exit code. A
        code the agent could also return would make every overlap look like a
        broken review and fail the cron job."""
        held = self._hold_pr_lock(tmp_dir, 101)
        try:
            result, _, _ = self._run(
                self.JOB,
                tmp_dir,
                self._stubs(tmp_dir),
                {"REVIEW_REQUESTED_MAX_PRS": "3"},
            )
        finally:
            held.kill()
            held.wait()
        assert result.returncode == 0, result.stdout + result.stderr
        assert "failed" not in result.stderr

    def test_the_pr_lock_is_released_when_the_session_ends(self, tmp_dir):
        """Held for the life of the session and no longer. A lock that outlived
        its session would be indistinguishable from a review in progress, and
        no later poll would ever pick that PR up again — the queue would go
        quiet one PR at a time with nothing in the log to say why."""
        first, _, first_log = self._run(self.JOB, tmp_dir, self._stubs(tmp_dir))
        assert first.returncode == 0, first.stderr
        assert len(first_log) == 3
        os.remove(os.path.join(tmp_dir, "claude.log"))

        again, _, claude_log = self._run(self.JOB, tmp_dir, self._stubs(tmp_dir))
        assert again.returncode == 0, again.stderr
        assert len(claude_log) == 3, "a leaked lock would have skipped every PR"
        assert "skipped" not in again.stdout

    def test_one_failed_review_does_not_take_the_queue_down_with_it(self, tmp_dir):
        result, _, claude_log = self._run(
            self.JOB,
            tmp_dir,
            self._stubs(tmp_dir, failing_pr=102),
            {"REVIEW_REQUESTED_MAX_PRS": "3"},
        )
        assert len(claude_log) == 3
        assert result.returncode != 0
        assert "Review of #102 failed" in result.stderr

    def test_the_job_asks_again_because_another_machine_may_have_answered(
        self, tmp_dir
    ):
        """Between the gate and the job is a git fetch, a checkout and a target
        repo sync. The queue is shared, so it can empty in that window."""
        result, gh_log, claude_log = self._run(
            self.JOB, tmp_dir, self._stubs(tmp_dir, queue="")
        )
        assert gh_log, "the job took the gate's word for it"
        assert claude_log == []
        assert result.returncode == 0
        assert "nothing to do" in result.stdout

    @pytest.fixture
    def attempts(self):
        """The attempt count of PR 901, which lives in the repo's .ignore beside
        the PR locks. Removed afterwards so no run on this checkout skips it."""
        path = os.path.join(REPO_ROOT, ".ignore", ".review-pr-901.attempts")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.remove(path)
        yield path
        if os.path.exists(path):
            os.remove(path)

    def test_a_pr_left_unanswered_is_given_up_on_at_the_cap(self, tmp_dir, attempts):
        """A PR the skill cannot review stays in the queue, and the poll comes
        back every five minutes. #39947 started over a thousand sessions that
        way before anyone noticed."""
        bindir = self._stubs(
            tmp_dir, queue="901", head="abc123", requested=self._configured_bot()
        )
        for _ in range(5):
            result, _, _ = self._run(self.JOB, tmp_dir, bindir)
            assert "still waiting on a review" in result.stderr
        result, _, claude_log = self._run(self.JOB, tmp_dir, bindir)
        assert len(claude_log) == 5, "the sixth poll started another session"
        assert result.returncode == 0, "giving up is not a failed review"
        assert "Gave up" in result.stdout and "#901" in result.stdout

    def test_the_cap_is_configurable(self, tmp_dir, attempts):
        bindir = self._stubs(
            tmp_dir, queue="901", head="abc123", requested=self._configured_bot()
        )
        env = {"REVIEW_REQUESTED_MAX_ATTEMPTS": "1"}
        self._run(self.JOB, tmp_dir, bindir, env)
        _, _, claude_log = self._run(self.JOB, tmp_dir, bindir, env)
        assert len(claude_log) == 1

    def test_a_push_starts_the_count_over(self, tmp_dir, attempts):
        """The author pushing is the likeliest thing to make the next attempt
        go differently, so it earns a fresh set."""
        with open(attempts, "w") as f:
            f.write("abc123 3\n")
        bindir = self._stubs(
            tmp_dir, queue="901", head="def456", requested=self._configured_bot()
        )
        _, _, claude_log = self._run(self.JOB, tmp_dir, bindir)
        assert len(claude_log) == 1
        with open(attempts) as f:
            assert f.read().split() == ["def456", "1"]

    def test_a_submitted_review_clears_the_count(self, tmp_dir, attempts):
        """A later re-request is a new question, not a fourth try at the old one."""
        with open(attempts, "w") as f:
            f.write("abc123 2\n")
        bindir = self._stubs(tmp_dir, queue="901", head="abc123", requested="")
        _, _, claude_log = self._run(self.JOB, tmp_dir, bindir)
        assert len(claude_log) == 1
        assert not os.path.exists(attempts)

    def test_a_request_github_has_not_yet_dropped_is_not_an_attempt(
        self, tmp_dir, attempts
    ):
        """Asked the second after the review is submitted, GitHub still lists
        the request. Taken at its word, every review the job made counted as an
        attempt, and a PR re-requested at the same head was given up on after
        three reviews that had all landed."""
        with open(attempts, "w") as f:
            f.write("abc123 2\n")
        bindir = self._stubs(
            tmp_dir,
            queue="901",
            head="abc123",
            requested=self._configured_bot(),
            lagging=True,
        )
        result, _, claude_log = self._run(self.JOB, tmp_dir, bindir)
        assert len(claude_log) == 1
        assert "still waiting" not in result.stderr
        assert not os.path.exists(attempts)

    def test_a_failed_exit_on_an_answered_pr_is_not_an_attempt(self, tmp_dir, attempts):
        """What counts is whether the review was submitted, not how the
        session exited: one that posted and then crashed did its job."""
        bindir = self._stubs(
            tmp_dir, queue="901", failing_pr=901, head="abc123", requested=""
        )
        result, _, _ = self._run(self.JOB, tmp_dir, bindir)
        assert "Review of #901 failed" in result.stderr
        assert not os.path.exists(attempts)
