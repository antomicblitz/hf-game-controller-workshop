# Agent requests

Unresolved decisions that need a human, not an agent.

- Provide the public link for the **Super Maga Bros** reference game so
  `docs/02-game-and-stl-viewer.md` can mention it (see section "Controller
  contract").
- Decide CI scope for the sandbox-dependent maintainer suite. GitHub-hosted
  ubuntu runners cannot complete four `tests/test_llm_case_customization.py`
  cases that need a real CAD sandbox, so CI now runs `make validate` only
  (matching the Makefile's "maintainer-only" design and open PR #2, which
  this supersedes). If CI should run `make maintainer-validate`, that needs
  capability-skip handling around `cadkit.case_execution.sandbox_status()`,
  or a self-hosted runner where bubblewrap works.
