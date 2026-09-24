# SESSION STATE — read this first when resuming

**Last updated:** 2026-09-24 (Opus coordinator session 1 cont.)
**Branch:** `v3-dev`
**Last commit:** `173a8cf` (before scribe update)
**Current phase:** Phases 0–3 complete; Phase 4 UI mostly complete; Phase 5 innovation starting; Phase 6 release not started.

## In progress
- **REP-08** (report-engineer) — Renderers honour section order/titles/cover+overview toggles/palette/custom text; grain notes in Raw sheet
- **DET-05** (detection-engineer) — auto-detect SEM info bar (extend `_auto_crop` to black info bars/borders)
- **INN-05** (detection-engineer) — Calibration from SEM TIFF metadata (Zeiss/FEI/JEOL/Hitachi/TESCAN)
- **REV-S2** (code-reviewer) — Code review of report designer

## Blocked
- **FND-04**: GitHub push — cached credentials for `Harvey-FS` (403); user action required to fix or change remote config.
- **D-13**: Real SEM images still wanted for validating the black-region thresholds.

## Next 3 actions for the coordinator
1. Review + commit REP-08 and DET-05/INN-05; wire info-bar auto-detection + metadata calibration into the Analyze page (ui-designer).
2. UI-05 remainder: lasso select + merge/split (INN-04); then innovator features INN-27 (lot stats + 95% CI), INN-02 (spec limits), INN-29 (cal verification), INN-30 (approval + sign-off).
3. Phase 6: journey tests, docs, local PyInstaller build + install test (verify spec includes all new modules), then release with user go.

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider     # 257 tests passing
.venv\Scripts\python main.py                                     # GUI
python -m ui.demo --capture scratch/ui                           # demo screenshots
```
