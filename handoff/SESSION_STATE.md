# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-24 (Phase 6 Release: v3.0.0 built & tagged locally; awaiting GitHub auth)
**Branch:** `v3-dev` | **Last commit:** `8f8753c` (build: ignore generated installer) | **Phase:** Release (main fast-forwarded to 8f8753c; v3.0.0 tag local only).
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed
Phase 0–5 + **Phase 6 starting** (Release build complete): PySide6 + 3-layer offline guard; black-region fix; ASTM E112 G; non-destructive grain filters; data layer (trash/restore, CLEAR sentinel); report engine + designer (exports honour edits, 4 palettes, custom text); app shell (Projects/Wizard/Analyze/Review/Reports/Settings); SEM info-bar auto-exclusion + metadata calibration; HIER-01 data + UI; UI-09 file management (right-click/checkboxes/multi-select, trash+undo); UI-10 guided tour (11 steps, never auto-start under pytest); UI-11 scale-bar modes (Rectangle/Level/Free, snap, persistent); **INN-02 complete** (spec editor, verdict badge); **INN-29 complete** (calibration check, status chip); **UI-05 complete** (grain editing: lasso/merge/split, undo, persisted); **UI-08 complete** (About, branding, licence viewer); **FIX-02–17 done** (icon gen, calibration field Excel + PPTX, layout, negative-zero format, scale-bar robustness, PPTX layout); **INN-43 complete** (lot comparison matrix, Welch ANOVA, TOST, 9 UI tests); **SAM checkpoint** build-time download; **REL-01 done** (3 journey tests); **REL-03 done** (PyInstaller dist 1.3 GB, exe launches, offline guard clean; NSIS 644 MB built, gitignored; install test awaits user UAC). Full suite: **686 passed**. v3.0.0 tag created locally.

## In progress
- None (release prep only; code complete).

## Next 3 actions
1. User signs into GitHub (replace Harvey-FS cached credential) → `git push origin main v3-dev --tags`.
2. Watch CI build GrainAnalyzer_Setup.exe + .dmg; user test-installs exe locally.
3. If install test finds bug: fix on v3-dev, re-ff main, re-tag `v3.0.0` (safe only before push), then push again.

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
