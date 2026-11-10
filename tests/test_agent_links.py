"""Tests for agents/setup.py, which links agents/ into each tool's discovery directories
and writes the TOML files Codex reads its subagents from.

setup.py is loaded by path and pointed at a temporary checkout, so nothing here touches the
real `.claude/`, `.agents/` and the rest.
"""

import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

try:
    import tomllib
except ImportError:  # Python < 3.11, which the CI floor job runs
    tomllib = None

needs_toml = pytest.mark.skipif(tomllib is None, reason="tomllib needs Python 3.11")
not_windows = pytest.mark.skipif(
    os.name == "nt", reason="Windows copies instead of linking"
)

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "agents_setup_under_test", REPO / "agents" / "setup.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

ALPHA = """\
---
name: alpha
description:
  'Does the alpha work. It said: use it for
  every case, and it''s done.'
kind: worker
tools: read_file, search
---

You do the alpha work. See [the guide](../../docs/guide.md#steps), [the rules](../AGENTS.md),
[the site](https://example.com/a/../b) and [below](#report).

## Report

Say what you did.
"""

BETA = """\
---
name: beta
description: Does the beta work.
---

You do the beta work.
"""

SKILL = "---\nname: {name}\ndescription: A skill.\n---\n\nDo it.\n"


@contextlib.contextmanager
def repo(definitions=None, skills=(), files=()):
    """A checkout holding the given files in agents/agents/, with setup.py pointed at it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "agents" / "agents").mkdir(parents=True)
        for filename, text in (definitions or {}).items():
            (root / "agents" / "agents" / filename).write_text(text, encoding="utf-8")
        for name in skills:
            (root / "agents" / "skills" / name).mkdir(parents=True)
            (root / "agents" / "skills" / name / "SKILL.md").write_text(
                SKILL.format(name=name), encoding="utf-8"
            )
        for name in files:
            (root / "agents" / name).write_text("x\n", encoding="utf-8")
        with (
            mock.patch.object(setup, "_ROOT", root),
            mock.patch.object(setup, "_SRC", root / "agents"),
        ):
            yield root


def codex(root, name):
    return tomllib.loads(
        (root / ".codex" / "agents" / f"{name}.toml").read_text(encoding="utf-8")
    )


def listing():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        setup.show(None)
    return out.getvalue()


class TestFrontmatter:
    def test_a_value_wrapped_over_lines_is_joined_with_spaces_and_unquoted(self):
        declared, _ = setup._frontmatter(ALPHA)
        assert (
            declared["description"]
            == "Does the alpha work. It said: use it for every case, and it's done."
        )

    def test_a_continuation_line_holding_a_colon_is_not_a_key(self):
        declared, _ = setup._frontmatter(ALPHA)
        assert sorted(declared) == ["description", "kind", "name", "tools"]

    def test_a_folded_block_joins_its_lines_and_a_literal_block_keeps_them(self):
        text = "---\nname: n\ndescription: >\n  one\n  two\nnotes: |\n\n  first\n  second\n\n---\nbody\n"
        declared, body = setup._frontmatter(text)
        assert declared["description"] == "one two"
        assert declared["notes"] == "first\nsecond"
        assert body == "body\n"

    def test_text_without_a_closed_block_is_not_frontmatter(self):
        assert setup._frontmatter("name: n\n---\nbody\n") is None
        assert setup._frontmatter("---\nname: n\nbody never closed\n") is None


@needs_toml
class TestRendering:
    def test_a_definition_becomes_the_three_keys_codex_requires(self):
        with repo({"alpha.md": ALPHA}) as root:
            assert setup.link(None)
            found = codex(root, "alpha")
        assert sorted(found) == ["description", "developer_instructions", "name"]
        assert found["name"] == "alpha"
        assert (
            found["description"]
            == "Does the alpha work. It said: use it for every case, and it's done."
        )
        assert found["developer_instructions"].startswith("You do the alpha work.")
        assert found["developer_instructions"].endswith("Say what you did.\n")

    def test_two_definitions_keep_their_own_name_description_and_instructions(self):
        with repo({"alpha.md": ALPHA, "beta.md": BETA}) as root:
            setup.link(None)
            alpha, beta = codex(root, "alpha"), codex(root, "beta")
        assert (beta["name"], beta["description"]) == ("beta", "Does the beta work.")
        assert beta["developer_instructions"] == "You do the beta work.\n"
        assert alpha["developer_instructions"] != beta["developer_instructions"]

    def test_text_that_is_special_in_toml_survives_unchanged(self):
        description = 'Path a\\b with "quotes", a run """ of them and a tab\t.'
        awkward = 'A backslash \\ and \\n, a quote " and a run """ of them, a tab\there, \x01 a control, é.\n'
        text = f"---\nname: awkward\ndescription: '{description}'\n---\n\n{awkward}\n```\n'''\n```\n"
        with repo({"awkward.md": text}) as root:
            setup.link(None)
            found = codex(root, "awkward")
        assert found["description"] == description
        assert found["developer_instructions"] == awkward + "\n```\n'''\n```\n"

    def test_a_relative_link_is_made_relative_to_the_repository_root(self):
        with repo({"alpha.md": ALPHA}) as root:
            setup.link(None)
            instructions = codex(root, "alpha")["developer_instructions"]
        assert "[the guide](docs/guide.md#steps)" in instructions
        assert "[the rules](agents/AGENTS.md)" in instructions

    def test_a_url_an_anchor_and_a_link_out_of_the_repository_are_left_as_written(self):
        text = (
            "---\nname: n\ndescription: d\n---\n"
            "[a](https://example.com/a/../b) [b](#report) [c](/abs/path.md) [d](../../../outside.md) "
            "[e](mailto:x@example.com)\n"
        )
        with repo({"n.md": text}) as root:
            setup.link(None)
            instructions = codex(root, "n")["developer_instructions"]
        assert instructions == text.split("---\n", 2)[2]

    def test_a_definition_missing_what_codex_requires_is_reported_and_not_written(
        self, caplog
    ):
        cases = {
            "noname.md": ("---\ndescription: d\n---\nbody\n", "has no name"),
            "nodescription.md": ("---\nname: n\n---\nbody\n", "has no description"),
            "nobody.md": (
                "---\nname: n\ndescription: d\n---\n\n",
                "has no instructions",
            ),
            "nofrontmatter.md": ("just prose\n", "has no frontmatter block"),
        }
        with repo({"good.md": BETA, **{k: v[0] for k, v in cases.items()}}) as root:
            with caplog.at_level("ERROR"):
                ok = setup.link(None)
            written = sorted(p.name for p in (root / ".codex" / "agents").iterdir())
        assert not ok
        assert written == ["good.toml"]
        for filename, (_, reason) in cases.items():
            assert f"agents/agents/{filename} {reason}" in caplog.text, filename


class TestLinking:
    def test_a_second_run_with_nothing_to_do_says_nothing_and_changes_nothing(
        self, caplog
    ):
        with repo({"alpha.md": ALPHA}, skills=["s"]) as root:
            setup.link(None)
            path = root / ".codex" / "agents" / "alpha.toml"
            first = path.stat().st_mtime_ns
            with caplog.at_level("INFO"):
                caplog.clear()
                assert setup.link(None)
            assert caplog.records == []
            assert path.stat().st_mtime_ns == first

    def test_an_edited_definition_is_written_again(self):
        with repo({"alpha.md": ALPHA}) as root:
            setup.link(None)
            (root / "agents" / "agents" / "alpha.md").write_text(
                ALPHA.replace("alpha work.", "alpha chore."), encoding="utf-8"
            )
            setup.link(None)
            assert "alpha chore." in (
                root / ".codex" / "agents" / "alpha.toml"
            ).read_text(encoding="utf-8")

    def test_a_file_written_by_hand_under_the_same_name_is_left_alone_with_a_warning(
        self, caplog
    ):
        with repo({"alpha.md": ALPHA}) as root:
            mine = root / ".codex" / "agents" / "alpha.toml"
            mine.parent.mkdir(parents=True)
            mine.write_text('name = "mine"\n', encoding="utf-8")
            with caplog.at_level("WARNING"):
                assert setup.link(None)
            assert mine.read_text(encoding="utf-8") == 'name = "mine"\n'
        assert "alpha.toml" in caplog.text and "left as-is" in caplog.text

    @not_windows
    def test_a_symlink_where_the_file_belongs_is_not_written_through(self, caplog):
        with repo({"alpha.md": ALPHA}) as root:
            # The target begins with the marker, so only the link itself says it is not ours.
            untouched = setup._GENERATED_PREFIX + " from elsewhere.\nuntouched\n"
            elsewhere = root / "elsewhere.toml"
            elsewhere.write_text(untouched, encoding="utf-8")
            dest = root / ".codex" / "agents" / "alpha.toml"
            dest.parent.mkdir(parents=True)
            dest.symlink_to(elsewhere)
            with caplog.at_level("WARNING"):
                setup.link(None)
            assert elsewhere.read_text(encoding="utf-8") == untouched
            assert dest.is_symlink()

    def test_a_generated_file_whose_definition_is_gone_is_removed_and_a_hand_written_one_is_not(
        self,
    ):
        with repo({"alpha.md": ALPHA, "beta.md": BETA}) as root:
            setup.link(None)
            mine = root / ".codex" / "agents" / "mine.toml"
            mine.write_text('name = "mine"\n', encoding="utf-8")
            (root / "agents" / "agents" / "beta.md").unlink()
            setup.link(None)
            left = sorted(p.name for p in (root / ".codex" / "agents").iterdir())
        assert left == ["alpha.toml", "mine.toml"]

    def test_unlink_removes_the_files_it_wrote_and_nothing_else(self):
        with repo({"alpha.md": ALPHA}) as root:
            setup.link(None)
            mine = root / ".codex" / "agents" / "mine.toml"
            mine.write_text('name = "mine"\n', encoding="utf-8")
            setup.unlink(None)
            left = sorted(p.name for p in (root / ".codex" / "agents").iterdir())
        assert left == ["mine.toml"]

    @not_windows
    def test_each_tool_gets_the_entries_it_reads_and_codex_gets_no_copy_of_the_markdown(
        self,
    ):
        with repo({"alpha.md": ALPHA}, skills=["s"]) as root:
            setup.link(None)
            agent = root / "agents" / "agents" / "alpha.md"
            for where in (".claude/agents", ".cursor/agents", ".bravebot/agents"):
                link = root / where / "alpha.md"
                assert link.is_symlink(), where
                assert link.resolve() == agent.resolve(), where
            skill = root / "agents" / "skills" / "s"
            for where in (
                ".claude/skills",
                ".agents/skills",
                ".bravebot/skills",
                ".cursor/skills",
            ):
                link = root / where / "s"
                assert link.is_symlink(), where
                assert link.resolve() == skill.resolve(), where
                assert (link / "SKILL.md").is_file(), where
            assert not (root / ".agents" / "agents").exists()
            assert not (root / ".codex" / "agents" / "alpha.md").exists()

    @not_windows
    def test_a_link_is_relative_so_it_survives_the_checkout_moving(self):
        with repo(skills=["s"]) as root:
            setup.link(None)
            assert not os.path.isabs(os.readlink(root / ".claude" / "skills" / "s"))

    def test_a_stray_file_in_skills_and_a_hidden_entry_are_not_linked(self):
        with repo(skills=["s"]) as root:
            (root / "agents" / "skills" / "README.md").write_text(
                "x\n", encoding="utf-8"
            )
            (root / "agents" / "skills" / ".hidden").mkdir()
            (root / "agents" / "skills" / ".hidden" / "SKILL.md").write_text(
                "x\n", encoding="utf-8"
            )
            setup.link(None)
            assert sorted(p.name for p in (root / ".claude" / "skills").iterdir()) == [
                "s"
            ]

    @not_windows
    def test_files_and_commands_are_linked_only_when_they_exist(self):
        with repo(skills=["s"]) as root:
            setup.link(None)
            assert not (root / "AGENTS.md").exists()
            assert not (root / ".claude" / "settings.json").exists()
            assert not (root / ".claude" / "commands").exists()
        with repo(skills=["s"], files=["AGENTS.md", "settings.json"]) as root:
            (root / "agents" / "commands").mkdir()
            (root / "agents" / "commands" / "go.md").write_text("x\n", encoding="utf-8")
            setup.link(None)
            for where in ("AGENTS.md", ".claude/CLAUDE.md"):
                assert (root / where).resolve() == (
                    root / "agents" / "AGENTS.md"
                ).resolve(), where
            for where in (".claude/settings.json", ".bravebot/settings.json"):
                assert (root / where).resolve() == (
                    root / "agents" / "settings.json"
                ).resolve(), where
            assert (root / ".claude" / "commands" / "go.md").is_symlink()

    @not_windows
    def test_a_real_file_or_directory_at_a_destination_is_left_alone_with_a_warning(
        self, caplog
    ):
        with repo({"alpha.md": ALPHA}, skills=["s"]) as root:
            mine = root / ".claude" / "skills" / "s"
            mine.mkdir(parents=True)
            (mine / "SKILL.md").write_text("mine\n", encoding="utf-8")
            theirs = root / ".claude" / "agents" / "alpha.md"
            theirs.parent.mkdir(parents=True)
            theirs.write_text("mine\n", encoding="utf-8")
            with caplog.at_level("WARNING"):
                assert setup.link(None)
                setup.unlink(None)
            assert (mine / "SKILL.md").read_text(encoding="utf-8") == "mine\n"
            assert not mine.is_symlink()
            assert theirs.read_text(encoding="utf-8") == "mine\n"
            assert not theirs.is_symlink()
            # the rest was still linked, and unlink then removed only that
            assert not (root / ".agents" / "skills" / "s").exists()
        assert "SKIP .claude/skills/s" in caplog.text
        assert "SKIP .claude/agents/alpha.md" in caplog.text

    @not_windows
    def test_a_stale_link_is_replaced_and_unlink_leaves_a_link_pointing_elsewhere(self):
        with repo(skills=["s"]) as root:
            dest = root / ".claude" / "skills" / "s"
            dest.parent.mkdir(parents=True)
            dest.symlink_to(root / "nowhere")
            setup.link(None)
            assert dest.resolve() == (root / "agents" / "skills" / "s").resolve()
            dest.unlink()
            other = root / "other"
            other.mkdir()
            dest.symlink_to(other)
            setup.unlink(None)
            assert dest.is_symlink()

    @not_windows
    def test_unlink_removes_the_links_it_made(self):
        with repo({"alpha.md": ALPHA}, skills=["s"]) as root:
            setup.link(None)
            setup.unlink(None)
            found = [p for p in root.rglob("*") if p.is_symlink()]
            assert found == []
            assert not (root / ".codex" / "agents" / "alpha.toml").exists()

    @needs_toml
    def test_list_says_what_each_codex_file_is(self):
        with repo(
            {
                "alpha.md": ALPHA,
                "beta.md": BETA,
                "gamma.md": BETA.replace("beta", "gamma"),
            }
        ) as root:
            setup.link(None)
            agents = root / ".codex" / "agents"
            (root / "agents" / "agents" / "beta.md").write_text(
                BETA.replace("beta work", "beta chore"), encoding="utf-8"
            )
            (agents / "gamma.toml").write_text('name = "mine"\n', encoding="utf-8")
            (root / "agents" / "agents" / "delta.md").write_text(
                BETA.replace("beta", "delta"), encoding="utf-8"
            )
            orphan = agents / "old.toml"
            orphan.write_text(
                setup._GENERATED_PREFIX + " from agents/agents/old.md.\n",
                encoding="utf-8",
            )
            out = listing()
        states = {}
        for line in out.splitlines():
            found = re.match(r"\.codex/agents/(\w+)\.toml\s+(.*)", line)
            if found:
                states[found.group(1)] = found.group(2)
        assert states == {
            "alpha": "generated",
            "beta": "STALE generated",
            "delta": "missing",
            "gamma": "real path (not ours)",
            "old": "orphan (removed by link)",
        }

    @not_windows
    def test_list_marks_a_link_missing_and_a_broken_one(self):
        with repo(skills=["s"]) as root:
            out = listing()
            assert re.search(r"\.claude/skills/s\s+missing", out)
            dest = root / ".agents" / "skills" / "s"
            dest.parent.mkdir(parents=True)
            dest.symlink_to(root / "nowhere")
            assert re.search(r"\.agents/skills/s\s+BROKEN link", listing())


class TestCommandLine:
    def run(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO / "agents" / "setup.py"), *args],
            capture_output=True,
            text=True,
            cwd=str(REPO),
        )

    def test_the_default_command_is_link_and_quiet_is_accepted(self):
        result = self.run("-q")
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    def test_list_names_the_source_and_exits_zero(self):
        result = self.run("list")
        assert result.returncode == 0
        assert "DESTINATION" in result.stdout

    def test_an_unknown_command_is_refused(self):
        assert self.run("explode").returncode != 0


class TestCheckedIn:
    def test_the_pipeline_skills_are_checked_in_where_the_links_point(self):
        for name in ("review-prs", "reply-to-ping"):
            assert (REPO / "agents" / "skills" / name / "SKILL.md").is_file(), name
        for name in ("review-prs-detect", "review-prs-validate"):
            assert (REPO / "agents" / "agents" / f"{name}.md").is_file(), name

    def test_nothing_under_the_discovery_directories_is_tracked(self):
        tracked = subprocess.run(
            ["git", "ls-files", ".claude", ".agents", ".bravebot", ".cursor", ".codex"],
            capture_output=True,
            text=True,
            cwd=str(REPO),
        )
        if tracked.returncode != 0:
            pytest.skip("not a git checkout")
        assert tracked.stdout.strip() == ""

    @needs_toml
    def test_every_checked_in_definition_converts_to_toml_codex_accepts(self):
        pairs = setup.planned_codex_agents()
        assert pairs, "agents/agents/ holds no definition"
        for src, dest in pairs:
            found = tomllib.loads(setup.render_codex_agent(src))
            for key in ("name", "description", "developer_instructions"):
                assert found.get(key, "").strip(), f"{src.name}: {key}"
            assert dest.name == f"{src.stem}.toml"
