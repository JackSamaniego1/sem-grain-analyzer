# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-24 (user decisions, PPTX fixes, SAM build fetch, INN-43 core + idea triage)
**Branch:** `v3-dev` | **Last code commit:** `21e6d67` (FIX-11/12/13 PPTX) | GitHub push blocked until user fixes auth (FND-04).
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed (details in 03_TASK_BOARD.md / 06_PROGRESS_LOG.md)
Phases 0-5 starting (Phase 0–4 complete). PySide6 + 3-layer offline guard; black-region fix; ASTM E112 G; non-destructive grain filters; data layer (trash/restore, CLEAR sentinel); report engine + designer (exports honour edits, 4 palettes, custom text); app shell (Projects/Wizard/Analyze/Review/Reports/Settings); SEM info-bar auto-exclusion + metadata calibration; HIER-01 data + UI; UI-09 file management (right-click/checkboxes/multi-select, trash+undo); UI-10 guided tour (11 steps, never auto-start under pytest); UI-11 scale-bar modes (Rectangle/Level/Free, snap, persistent); **INN-02 complete** (spec editor in project settings, verdict badge on Lot card, optional default off); **INN-29 complete** (calibration check dialog, Settings toggle, status chip); **UI-05 complete** (grain editing: lasso/merge/split, undo, persisted); **UI-08 complete** (About dialog, code-drawn icon, offline statement, licence viewer, shortcut sheet L/M/C/V); **FIX-02/03/04/05/06/07/08/09/10/11/12/13/14/15/16/17 done** (build icon gen, calibration field Excel + PPTX render, status bar chip, narrow-width layout, cleanup, negative-zero format, scale-bar robustness, PPTX layout fixes); **INN-43 core done** (lot comparison matrix, Welch ANOVA, TOST equivalence, 19 tests); **SAM checkpoint build-time download** (560d2fe, hash-verified); **REL-01 done** (3 journey tests: threshold+scale-bar lifecycle, boundary+FEI+merge, XLSX+PPTX export verify). Full suite: **686 passed**.

## In progress
- **INN-43 UI** (lot_compare_page + LotMeta.is_baseline): ui-designer in progress; 2 files staged (ui/pages/lot_compare_page.py, tests/test_ui_lot_compare.py).

## Next 3 actions
1. Finish INN-43 UI (lot compare page, baseline picker) → merge core + UI → code review.
2. Phase 6 release: local PyInstaller build + NSIS test, merge to main, tag v3.0.0 locally.
3. User fixes GitHub auth (FND-04); push v3-dev + tag to origin; CI builds GrainAnalyzer_Setup.exe and .dmg.

## Blocked / needs user
- **FND-04 GitHub auth** (user will fix tonight 2026-09-24; credentials cached for Harvey-FS).
- **D-13 real SEM images** (user will supply later; needed for threshold 12 + image quality gate tuning).
- **INN-30 CANCELLED** (user request 2026-09-24; report approval workflow not needed for v3.0.0).
- **INN-41,42,44-51 DECLINED** (user request 2026-09-24; archived, not pursuing in v3.0.0).

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
```
