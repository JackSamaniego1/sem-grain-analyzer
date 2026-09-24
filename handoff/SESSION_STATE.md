# SESSION STATE — read this first when resuming

**Last updated:** 2026-09-23 (Opus coordinator session 1)
**Branch:** `v3-dev`
**Last commit:** `fc412c0` (before scribe update)
**Current phase:** Phase 0 + Phase 1 core complete; Phase 2/3 back-ends and design system in progress.

## In progress
- **DATA-01..05 + DATA-08** (data-architect, sonnet) — building `data/` package (models, workspace, session I/O, catalog, QSettings, auto-save)
- **REP-01..04** (report-engineer, sonnet) — building `reports/` package (model, charts, Excel/PowerPoint renderers)
- **UI-01 + UI-02** (ui-designer, opus) — building `ui/design/` (tokens + theme) and `ui/widgets/` (component library)

## Blocked
- **FND-04**: GitHub push — cached credentials for `Harvey-FS` (403); user action required to fix or change remote config.
- **DET validation**: D-13 flagged — real SEM images still wanted for validating the black-region thresholds.

## Next 3 actions for the coordinator
1. Review + commit `data/`, `reports/`, `ui/design/` + `ui/widgets/` when back-end agents finish (expect ~2 sessions).
2. Launch **DET-04** (ASTM G-number, detection-engineer) and **INN-26** (compliance engine, depends DET-04) in parallel; launch **UI-03** app shell (ui-designer).
3. Wire UI to data layer (DATA-06/07: Projects page + New Session wizard) and reports page (REP-05).

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider     # 47 tests passing
.venv\Scripts\python main.py                                     # GUI
```
