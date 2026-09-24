---
name: run-tests
description: Run the Grain Analyzer pytest suite (headless Qt) and summarise results. Use before committing and whenever an agent claims tests pass.
allowed-tools: PowerShell, Bash, Read
---

# Run the test suite

1. From the repo root run (PowerShell):
   ```powershell
   .venv\Scripts\python -m pytest tests -q -p no:cacheprovider --no-header -rA
   ```
   (`QT_QPA_PLATFORM=offscreen` is set automatically in `tests/conftest.py`.)
2. If a dependency is missing: `.venv\Scripts\pip install -r requirements-dev.txt`.
3. Report: total / passed / failed / skipped, and for each failure the test id and the assertion message. Do not paste full tracebacks unless asked.
4. Known state at handoff (2026-09-23): `tests/test_black_regions.py::test_black_regions_are_not_grains[boundary]` and `[threshold]` FAIL by design until task DET-01 lands (baseline: 2 failed, 2 passed).
