# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-24 (Opus coordinator, post-HIER-04 acceptance)
**Branch:** `v3-dev` | **Last code commit:** `276d579` (HIER-02/03/04 done) | GitHub push still blocked (FND-04).
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed (details in 03_TASK_BOARD.md / 06_PROGRESS_LOG.md)
Phases 0-3 complete; most of Phase 4. PySide6 + 3-layer offline guard; black-region fix; ASTM E112 G (core/astm.py); non-destructive grain filters (core/postfilter.py + UI filter card); data layer (trash/restore, CLEAR sentinel); report engine + report designer (exports honour all edits, 4 palettes, custom text); app shell (Projects, Wizard, Analyze, Review, Reports, Settings); SEM info-bar auto-exclusion + metadata calibration; HIER-01 data + UI; wizard relabel on Settings change; date-field arrow fix; Excel/PPTX hierarchy labels re-verified. Full suite: **417 passed**.

## Next
1. **UI-09 + UI-11 in parallel** (different files):
   - **UI-09**: right-click Delete/Move/Rename on folders & images, per-row select checkboxes, multi-select action bar (Delete / Move to...). Tests required.
   - **UI-11**: scale-bar calibration modes (Rectangle w/ editable handles, Level line locked horizontal + loupe [default], Free line w/ tilt warning). Spec: handoff/specs/UI-11.md.
2. **UI-10** (after above): first-run guided tour of a basic SAM analysis. Spec: handoff/specs/UI-10.md.
3. UI-05 remainder: lasso select, merge/split grains (INN-04).
4. Innovator features: INN-27 lot stats + 95% CI, INN-02 spec limits PASS/FAIL, INN-29 calibration check, INN-30 approval/sign-off.
5. UI-08: About/icon/branding, DPI 150/200% check; second innovator pass.
6. Phase 6: journey tests, docs/README for v3, local PyInstaller build + install/uninstall test, merge to main, tag v3.0.0 (needs user go + GitHub auth).

## Blocked / needs user
- FND-04 GitHub auth (credentials cached for Harvey-FS).
- D-13 real SEM images (validate black threshold 12 and info-bar detection).
- Optional logo/company name (D-11).

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
.venv\Scripts\python -m ui.demo --capture scratch/ui
```
