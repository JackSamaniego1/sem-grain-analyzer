# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-24 (INN-02/29 + INN-27 UI + FIX backend done; 544 tests; overlay export fix)
**Branch:** `v3-dev` | **Last code commit:** `924f47f` | GitHub push still blocked (FND-04).
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed (details in 03_TASK_BOARD.md / 06_PROGRESS_LOG.md)
Phases 0-4 complete except UI-08 (onboarding polish). Phase 5 in progress. PySide6 + 3-layer offline guard; black-region fix; ASTM E112 G; non-destructive grain filters; data layer (trash/restore, CLEAR sentinel); report engine + designer (exports honour edits, 4 palettes, custom text); app shell (Projects/Wizard/Analyze/Review/Reports/Settings); SEM info-bar auto-exclusion + metadata calibration; HIER-01 data + UI; UI-09 file management (right-click/checkboxes/multi-select, trash+undo); UI-10 guided tour (11 steps, never auto-start under pytest); UI-11 scale-bar modes (Rectangle/Level/Free, snap, persistent); **INN-27 complete** (lot statistics, mean G ± 95% CI, %RA per ASTM, audit_log, Lot result card with Include checkbox + reason); **INN-02 backend** (data/specs.py, ILAC-G8 verdict, report badges only when spec exists); **INN-29 backend** (core/cal_verify.py FFT pitch, AppSettings.calibration_verification_enabled=False); **FIX-02/03/04 done** (image card open jump, Delete key routing, Catalog batching); **overlay export** (full original frame incl. SEM info bar, thin dashed outline around measured region). Full suite: **544 passed**.

## In progress
None. All committed work landed.

## Next 3 actions
1. UI for INN-02 (spec editor in project/part settings, verdict badge on Lot card) + INN-29 (calibration check dialog, status chip, Settings toggle default off).
2. Settings controls: required_fields, target_RA_pct, spec limits, calibration check toggle (all default to minimal/off, no nags).
3. UI-05 remainder: lasso select, merge/split grains (INN-04); then UI-08 About/DPI check; second innovator pass.

## Blocked / needs user
- FND-04 GitHub auth (credentials cached for Harvey-FS).
- D-13 real SEM images (validate black threshold 12 and info-bar detection).
- INN-30 CANCELLED (user request 2026-09-24; report approval not needed).

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
```
