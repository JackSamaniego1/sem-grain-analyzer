# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-25 (Phase 6 Release: UX batch in progress overnight; GitHub auth fixed)
**Branch:** `v3-dev` | **Last commit:** `b06e51a` (UX-12: fix report editor Images tab freeze) | **Phase:** Release — UX items (UX-01..16) in progress, expected finish by morning.
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed
Phase 0–5 + Phase 6 partial: PySide6 + 3-layer offline guard; black-region fix; ASTM E112 G; non-destructive grain filters; data layer (trash/restore, CLEAR sentinel); report engine + designer (exports honour edits, 4 palettes, custom text); app shell (Projects/Wizard/Analyze/Review/Reports/Settings); SEM info-bar auto-exclusion + metadata calibration; HIER-01 data + UI; UI-09 file management; UI-10 guided tour; UI-11 scale-bar modes; **INN-02, INN-29, UI-05, UI-08 complete**; **FIX-02–17 done**; **INN-43 complete** (lot comparison matrix + TOST); **SAM checkpoint** download at build; **REL-01 done** (3 journeys); **REL-03 done** (PyInstaller 1.3 GB, NSIS 644 MB, exe launches). **UX-07 done (b0437c1)**: cooperative cancel ≤~1 s, threading.Event → GrainDetector.analyze(cancel=), SAM batch 64→16, 23 tests. Full suite: **686 passed**. v3.0.0 tag local only.

## In progress (overnight sprint)
- **ui-designer (UX-01..06, UX-08..11)** — settings panel order, pre-analysis gate, scale-bar scope, overlay opacity, image removal, nav tooltips, CPU chip clarity, spinner fix
- **report-engineer (UX-12..16)** — Images tab lazy load, multi-lot report format, editable charts, custom palettes, overlay opacity in export
- (UX-07 already complete)

## Next 3 actions
1. **Complete all UX items** (ui-designer & report-engineer; morning target).
2. **Full suite pass** → rebuild GrainAnalyzer_Setup.exe locally.
3. **main fast-forward** (8f8753c→b06e51a and beyond), **re-tag `v3.0.0` locally** (git tag -f), user tests install, pushes when ready.

## Blocked / needs user
- **D-13 real SEM images** (user will supply later; image quality tuning deferred to v3.1+).

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
```
