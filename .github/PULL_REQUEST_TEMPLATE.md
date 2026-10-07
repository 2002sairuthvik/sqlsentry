## What and why

## How it was tested
- [ ] `ruff check .` and `ruff format --check .`
- [ ] `pytest -q`

## Safety checklist (if touching `guard/`, `policy.py` or `execution/`)
- [ ] Layer 1 baseline is still impossible to disable
- [ ] New behaviour has tests for the rejection path, not just the happy path
