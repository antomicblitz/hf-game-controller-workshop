.DEFAULT_GOAL := help

.PHONY: help setup venv connect disconnect doctor agent editor web web-check check lint markdown typecheck test coverage complexity validate maintainer-validate clean

VENV := $(CURDIR)/.venv
VENV_PYTHON := $(VENV)/bin/python
PYTHON := $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),python3)
export PYTHONPATH := $(CURDIR)/tools$(if $(PYTHONPATH),:$(PYTHONPATH))

PYTHON_SOURCES := tools/ examples/ tests/
TEST_FILES := $(wildcard tests/test_*.py)
FAST_TEST_FILES := $(wildcard tests/test_firmware_*.py) tests/test_case_edits.py tests/test_security.py
COMPLEXIPY := $(if $(wildcard $(VENV)/bin/complexipy),$(VENV)/bin/complexipy,complexipy)

help:
	@printf '%s\n' \
	  'make setup       Install the workshop Python environment' \
	  'make connect     Add the supplied Zen key using OpenCode' \
	  'make doctor      Check that the editor is ready' \
	  'make agent       Open the coding harness in Plan mode' \
	  'make editor      Start the local case editor (Ctrl+C stops)' \
	  'make web         Start the game/viewer site (Ctrl+C stops)' \
	  'make check       Run the short student checks' \
	  'make validate    Run the fast local quality gates'

setup: venv
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -e '.[dev]'

venv:
	@set -eu; \
	if [ -x "$(VENV_PYTHON)" ] && \
		"$(VENV_PYTHON)" -c 'import ssl, sys; raise SystemExit(sys.version_info[:2] != (3, 12))' >/dev/null 2>&1; then \
		exit 0; \
	fi; \
	python=''; \
	for candidate in python3.12 /opt/homebrew/opt/python@3.12/bin/python3.12 /usr/local/opt/python@3.12/bin/python3.12; do \
		if command -v "$$candidate" >/dev/null 2>&1 && \
			"$$candidate" -c 'import ssl, sys; raise SystemExit(sys.version_info[:2] != (3, 12))' >/dev/null 2>&1; then \
			python="$$candidate"; \
			break; \
		fi; \
	done; \
	if [ -z "$$python" ]; then \
		printf '%s\n' 'Python 3.12 with SSL support is required. Follow docs/01-setup.md, then run make setup again.' >&2; \
		exit 1; \
	fi; \
	printf 'Creating .venv with %s\n' "$$python"; \
	"$$python" -m venv --clear "$(VENV)"

connect:
	opencode auth login --provider opencode

disconnect:
	opencode auth logout

doctor:
	$(PYTHON) -m tools.doctor

agent:
	opencode --agent plan .

# Both servers intentionally stay attached to this terminal. Press Ctrl+C to stop.
editor:
	$(PYTHON) -m tools.editor.server

web:
	@printf '%s\n' 'Student project running at http://127.0.0.1:8000'
	$(PYTHON) -m http.server 8000 --bind 127.0.0.1 --directory student-project

web-check:
	$(PYTHON) -m tools.web_check

check: web-check
	$(PYTHON) -m pytest $(FAST_TEST_FILES) -q

lint:
	$(PYTHON) -m ruff format --check $(PYTHON_SOURCES)
	$(PYTHON) -m ruff check $(PYTHON_SOURCES)

markdown:
	$(PYTHON) -m pymarkdown --config pyproject.toml scan --recurse --respect-gitignore .

typecheck:
	$(PYTHON) -m pyright --pythonpath $(PYTHON)

test:
	$(PYTHON) -m pytest $(FAST_TEST_FILES) -q

coverage:
	$(PYTHON) -m coverage erase
	rm -f .coverage.*
	@for test_file in $(TEST_FILES); do \
		$(PYTHON) -m coverage run --parallel-mode -m pytest "$$test_file" -q || exit 1; \
	done
	$(PYTHON) -m coverage combine
	$(PYTHON) -m coverage report

complexity:
	$(COMPLEXIPY) --plain

validate: lint markdown typecheck test complexity web-check
	@printf '%s\n' 'Fast local quality gates passed.'

# Full editor/CAD coverage is intentionally CI/maintainer-only. OpenCascade
# tests run one module per process and are too slow for ordinary student edits.
maintainer-validate: validate coverage
	@printf '%s\n' 'All maintainer quality gates passed.'

clean:
	find tools examples tests -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf examples/6-button-gamepad/submits examples/6-button-gamepad/out
	rm -f .coverage .coverage.*
