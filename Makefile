.PHONY: test lint format check check-reviewdog check-reviewdog-full setup schedules schedules-review remove-schedules remove-schedules-review view-schedules clean

# Prefer .venv when it exists so no target needs an activated shell. PEP 668
# interpreters (Homebrew, recent Debian) refuse a system-wide pytest install, so
# a venv is the usual outcome of `make setup` -- but a system pytest/ruff still
# works, hence the fallback rather than a hard dependency.
VENV := $(CURDIR)/.venv
PYTHON := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
RUFF := $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)

# Run the test suite
test:
	$(PYTHON) -m pytest tests/ -v

# Lint Python files (check only)
lint:
	$(RUFF) check .
	$(RUFF) format --check .

# Lint and auto-fix Python files
format:
	$(RUFF) check --fix .
	$(RUFF) format .

# The whole local pass, in one target: lint, the tests, and the security scan.
check: lint test check-reviewdog

# The security scan that comments on our pull requests, before pushing rather
# than after. It arrives as an organization-level workflow calling
# brave/security-action, so scripts/check-reviewdog.sh clones that repository
# and drives its reviewdog runners against this checkout. First run downloads
# opengrep, reviewdog and the rule set into ~/.cache; later runs re-use them.
#
# check-reviewdog scans what the branch changed (the action's pull request mode);
# check-reviewdog-full scans everything (its workflow_dispatch mode).
check-reviewdog:
	@./scripts/check-reviewdog.sh

check-reviewdog-full:
	@./scripts/check-reviewdog.sh --full

# Install dev dependencies and run project setup
setup:
	@./scripts/install-dev-deps.sh
	@rm -rf *.egg-info
	./scripts/setup.sh

# Install/update the cron schedules for this project: the review sweeps and the
# poll for requested reviews. Also removes the review block brave-dev-loop used
# to install for the same project, so nothing runs twice.
schedules:
	./scripts/sync-schedules.sh

# The same, named for the group (this repo has only the one)
schedules-review:
	./scripts/sync-schedules.sh --group review

# Remove this project's cron schedules, leaving every other crontab line alone
remove-schedules:
	./scripts/remove-schedules.sh

remove-schedules-review:
	./scripts/remove-schedules.sh --group review

# Show schedule summary
view-schedules:
	./scripts/view-schedules.sh

# Clean up generated/temporary files
clean:
	rm -rf .pytest_cache .ruff_cache
