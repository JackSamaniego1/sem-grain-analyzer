# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-25 (v3.0.0 released; CI Windows build succeeded; docs/SECURITY_OVERVIEW.md added; ready for user install test)
**Branch:** `v3-dev` | **Last commit:** `27b336a` (docs: security and data-handling overview for IT review) | **Phase:** Release — awaiting user install test.
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed
**v3.0.0 released and published**: All Phase 0–6 work delivered + user feedback fixes (FB-01..03) + docs (CHANGELOG, README, Excel guide, SECURITY_OVERVIEW). UX batch (UX-01..16) complete: pre-analysis gate, settings order, scale/image scope toggles, image removal+restore, nav tooltips, CPU chip, spinner fix, cancel <1 s; multi-lot report (lot summaries + TOST matrix), editable charts, custom palettes, lazy-load images, overlay opacity export. **839 tests passed**. **v3.0.0 tag (annotated) on 5e99278, pushed to origin.** **CI Windows build succeeded: GrainAnalyzer_Setup.exe 674 MB attached to release.** **macOS DMG build failed (not needed for v3.0.0).**

## Released & pushed
- **main**: fast-forwarded to 5e99278 (all v3-dev work + user feedback + docs)
- **v3-dev**: work complete; 27b336a adds security overview for IT review
- **Test suite**: 839 passed / 0 failed
- **Offline audit**: passed (FND-07)
- **Tag v3.0.0**: annotated, on 5e99278, pushed to origin
- **Installer**: GrainAnalyzer_Setup.exe 674 MB, built by CI, attached to release, dist 1.3 GB, exe launches OK
- **Security docs**: docs/SECURITY_OVERVIEW.md added (offline guard, firewall rules, data locations, dependencies, build provenance, known limits)

## Next actions
1. **User installs v3.0.0**: Downloads GrainAnalyzer_Setup.exe from GitHub release to flash drive and test-installs on work PC
2. **Optional v3.0.1 fixes** (non-blocking): Check app is closed; remove previous install before copying (avoids v2 files left behind)
3. **Optional v3.1+ features**: Lot Summary preview widget in report designer; PowerPoint Lot Summary slide

## Blocked / needs user
- **D-13 real SEM images** (user will supply later; image quality tuning deferred to v3.1+)

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
```
