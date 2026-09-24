# SESSION STATE â€” read this first when resuming

**Last updated:** 2026-09-24 (Opus coordinator, post-HIER-01 UI)
**Branch:** `v3-dev` Â· **Last code commit:** `92b6093` (demo workspace HIER-01) Â· GitHub push still blocked (FND-04).
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed (details in 03_TASK_BOARD.md / 06_PROGRESS_LOG.md)
Phases 0â€“3 complete; most of Phase 4. PySide6 + 3-layer offline guard; black-region fix; ASTM E112 G (core/astm.py); non-destructive grain filters (core/postfilter.py + UI filter card); data layer (trash/restore, CLEAR sentinel); report engine + report designer (exports honour all edits, 4 palettes, custom text); app shell (Projects, Wizard, Analyze, Review, Reports, Settings); SEM info-bar auto-exclusion + metadata calibration (e6d0e1d); HIER-01 data + UI (a916add, b50751d, 1d6df50, 525fc9f, 92b6093); DET-05 + INN-05 integrated; crash-on-close fix; session trash undo. Full suite: **412 passed**.

## Next
1. Quick HIER-01 follow-ups: (a) wizard relabel on Settings change, (b) wizard date-field arrow clip, (c) Excel/PowerPoint export re-verify vs HIER-01 acceptance (hierarchy labels, export basename).`n   Then **UI-09** (user request): right-click Delete/Move/Rename on folders & images, per-row select checkboxes, multi-select action bar (Delete · Move to…). See task board.
2. UI-05 remainder: lasso select, merge/split grains (INN-04).
3. Innovator features: INN-27 lot stats + 95 % CI, INN-02 spec limits PASS/FAIL, INN-29 calibration check, INN-30 approval/sign-off (specs in handoff/specs/).
4. UI-08: About/icon/branding, DPI 150/200 % check; second innovator pass.
5. Phase 6: journey tests, docs/README for v3, local PyInstaller build + install/uninstall test (spec must include all new modules; firewall rules), merge to main, tag v3.0.0 (needs user go + GitHub auth).

## Blocked / needs user
- FND-04 GitHub auth (credentials cached for Harvey-FS).
- D-13 real SEM images (validate black threshold 12 and info-bar detection).
- Optional logo/company name (D-11).

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
.venv\Scripts\python -m ui.demo --capture scratch/ui     # screenshots
```
