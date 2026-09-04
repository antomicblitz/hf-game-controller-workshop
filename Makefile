.DEFAULT_GOAL := help

.PHONY: help setup connect disconnect doctor agent editor web web-check check lint markdown typecheck test coverage complexity validate clean

VENV := $(CURDIR)/.venv
VENV_PYTHON := $(VENV)/bin/python
PYTHON := $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),python3)
export PYTHONPATH := $(CURDIR)/tools$(if $(PYTHONPATH),:$(PYTHONPATH))

PYTHON_SOURCES := tools/ examples/ tests/
TEST_FILES := $(wildcard tests/test_*.py)

help:
	@printf '%s\n' \
	  'make setup       Install the workshop Python environment' \
	  'make connect     Add the supplied Zen key using OpenCode' \
	  'make doctor      Check that the editor is ready' \
	  'make agent       Open the coding harness in Plan mode' \
	  'make editor      Start the local case editor (Ctrl+C stops)' \
	  'make web         Start the game/viewer site (Ctrl+C stops)' \
	  'make check       Run the short student checks' \
	  'make validate    Run all maintainer quality gates'

setup:
	python3 -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -e '.[dev]'

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
	$(PYTHON) -m pytest tests/test_firmware_*.py tests/test_case_edits.py -q

lint:
	$(PYTHON) -m ruff format --check $(PYTHON_SOURCES)
	$(PYTHON) -m ruff check $(PYTHON_SOURCES)

markdown:
	$(PYTHON) -m pymarkdown --config pyproject.toml scan --recurse --respect-gitignore .

typecheck:
	$(PYTHON) -m pyright

test:
	@for test_file in $(TEST_FILES); do \
		printf '\n==> %s\n' "$$test_file"; \
		$(PYTHON) -m pytest "$$test_file" -q || exit 1; \
	done

coverage:
	$(PYTHON) -m coverage erase
	rm -f .coverage.*
	@for test_file in $(TEST_FILES); do \
		$(PYTHON) -m coverage run --parallel-mode -m pytest "$$test_file" -q || exit 1; \
	done
	$(PYTHON) -m coverage combine
	$(PYTHON) -m coverage report

complexity:
	$(PYTHON) -m complexipy.main --plain

validate: lint markdown typecheck coverage complexity web-check
	@printf '%s\n' 'All quality gates passed.'

clean:
	find tools examples tests -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf examples/6-button-gamepad/submits examples/6-button-gamepad/out
	rm -f .coverage .coverage.*
