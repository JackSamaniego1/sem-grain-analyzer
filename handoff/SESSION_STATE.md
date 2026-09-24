# SESSION STATE — read this first when resuming

**Last updated:** 2026-09-23 (planning session, Fable)
**Branch:** `main` (v3 work has not started; coordinator creates `v3-dev` in FND-01)
**Last commit:** see `git log --oneline -1` (handoff artefacts committed at end of planning)
**Current phase:** Phase 0 not started. Planning complete.

## In progress
Nothing in progress. No source code has been modified beyond adding `tests/`, `requirements-dev.txt`, `.gitignore`, docs and `.claude/`.

## Blocked
- FND-04 GitHub push: credential manager holds `Harvey-FS`; remote needs `JackSamaniego1`. User action required (see `DEVELOPMENT_STATUS.md` → "GitHub Authentication Issue").
- FND-03 user decisions D-03, D-10, D-11, D-12, D-13 (see `00_START_HERE.md` §8).

## Next 3 actions for the coordinator
1. `git checkout -b v3-dev`; run `/run-tests` (baseline: 2 failed — both `test_black_regions_are_not_grains` params — 2 passed) and `/smoke-app`; delegate FND-01 + FND-02 to `build-engineer`.
2. Delegate DET-01 to `detection-engineer` with `tests/test_black_regions.py` as the acceptance test (runs in parallel with Phase 0 — disjoint files).
3. Delegate UI-01 + UI-02 (design tokens + widget library) to `ui-designer`; run `innovator` once to expand `07_IDEAS_BACKLOG.md`. Then `/save-handoff`.

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider     # tests
.venv\Scripts\python main.py                                     # GUI
```
