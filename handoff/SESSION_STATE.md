# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-24 (REL-01 journey tests done; 641 tests)
**Branch:** `v3-dev` | **Last code commit:** `5845451` | GitHub push still blocked (FND-04).
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed (details in 03_TASK_BOARD.md / 06_PROGRESS_LOG.md)
Phases 0-5 complete; Phase 6 started. PySide6 + 3-layer offline guard; black-region fix; ASTM E112 G; non-destructive grain filters; data layer (trash/restore, CLEAR sentinel); report engine + designer (exports honour edits, 4 palettes, custom text); app shell (Projects/Wizard/Analyze/Review/Reports/Settings); SEM info-bar auto-exclusion + metadata calibration; HIER-01 data + UI; UI-09 file management (right-click/checkboxes/multi-select, trash+undo); UI-10 guided tour (11 steps, never auto-start under pytest); UI-11 scale-bar modes (Rectangle/Level/Free, snap, persistent); **INN-02 complete** (spec editor in project settings, verdict badge on Lot card, optional default off); **INN-29 complete** (calibration check dialog, Settings toggle, status chip); **UI-05 complete** (grain editing: lasso/merge/split, undo, persisted); **UI-08 complete** (About dialog, code-drawn icon, offline statement, licence viewer, shortcut sheet L/M/C/V); **FIX-02/03/04/05/06/07(Excel)/08/09/10/14/15/16/17 done** (build icon gen, calibration field Excel render, status bar chip, narrow-width layout, cleanup, negative-zero format, scale-bar robustness); **REL-01 done** (3 journey tests: threshold+scale-bar lifecycle incl. trash/restore, boundary+FEI+merge, XLSX+PPTX export verify; no bugs found). Full suite: **641 passed**.

## In progress
- None. Demo deck delivered to boss.

## Next 3 actions
1. User triage of INN-41..51 (11 new ideas: control chart, filename template, field-outlier check, spot-check count, result fingerprint, image quality gate, acquisition ledger, lot comparison, supplier scorecard, auto-worklist, uncertainty budget) + review README v3.
2. PPTX FIX-11/12/13 + FIX-07 PPTX calibration render — decide resume or defer.
3. Phase 6 release: README v3 finalize, local NSIS build test, merge to main, tag v3.0.0 (needs user go + GitHub auth fix + SAM checkpoint bundled).

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
