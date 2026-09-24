# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-24 (UI-10 + INN-27 backend done; INN-02/29/30/INN-27 UI/PyInstaller in progress)
**Branch:** `v3-dev` | **Last code commit:** `932e184` | GitHub push still blocked (FND-04).
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed (details in 03_TASK_BOARD.md / 06_PROGRESS_LOG.md)
Phases 0-3 complete; Phase 4 done except UI-08. PySide6 + 3-layer offline guard; black-region fix; ASTM E112 G (core/astm.py); non-destructive grain filters (core/postfilter.py + UI filter card); data layer (trash/restore, CLEAR sentinel); report engine + report designer (exports honour all edits, 4 palettes, custom text); app shell (Projects, Wizard, Analyze, Review, Reports, Settings); SEM info-bar auto-exclusion + metadata calibration; HIER-01 data + UI; wizard relabel on Settings change; date-field arrow fix; Excel/PPTX hierarchy labels re-verified; UI-09 Projects browser file management (right-click Delete/Move/Rename/Open, per-row checkboxes, multi-select action bar, trash + undo); UI-11 scale-bar calibration modes (Rectangle/Level/Free, snap to bar ends, ±px uncertainty, persistent); **UI-10 first-run guided tour** (animated spotlight + dimmed overlay, 11 steps, Skip, Don't show on startup, Help › Show tour; never auto-starts under pytest/offscreen/GRAIN_NO_TOUR=1); **INN-27 backend** (core/metrics.sample_statistics: mean G, 95% CI Student t, %RA, fields_needed, outlier_flag per ASTM E112/E1382; ImageRecord.included + exclusion_reason + audit_log; Catalog.fields_for_lot; settings required_fields=5, target_RA_pct=10; Excel Overview lot block + PPTX "G ± CI" tile). Full suite: **489 passed**.

## In progress (5 agents, uncommitted)
- INN-02 backend (data/specs.py + report badges)
- INN-29 backend (core/cal_verify.py + records)
- INN-30 backend (data/approval.py)
- INN-27 UI Lot result card + edited-G fix + FIX follow-ups (projects page Delete key, jump to image, single Catalog)
- PyInstaller local build dry run

## Next
1. Land in-progress work: full suite + review + commit.
2. INN-02/29/30 UI (spec editor/badges, calibration verify dialog/chip, approve dialog + DRAFT/APPROVED marks in reports).
3. UI-05 remainder: lasso select, merge/split grains (INN-04).
4. UI-08: About/icon/branding, DPI 150/200% check; second innovator pass.
5. Phase 6: journey tests, docs/README for v3, local PyInstaller build + install/uninstall test, merge to main, tag v3.0.0 (needs user go + GitHub auth).

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
