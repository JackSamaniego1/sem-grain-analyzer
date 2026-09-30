# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-29 (v3.0.0 user-installed; report design phase open — user asked for SUMMARY slide redesign with mockups)
**Branch:** `v3-dev` | **Last commit:** `0470d4e` (handoff: v3.0.0 published; CI build succeeded; Windows installer ready) | **Phase:** Report Design — awaiting user choice on 4 mockup options.
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed
**v3.0.0 released, installed, and tested by user**: All Phase 0–6 work delivered + user feedback fixes (FB-01..03) + security docs. UX batch (UX-01..16) complete: pre-analysis gate, settings order, scale/image scope toggles, image removal+restore, nav tooltips, CPU chip, spinner fix, cancel <1 s; multi-lot report (lot summaries + TOST matrix), editable charts, custom palettes, lazy-load images, overlay opacity export. **839 tests passed**. **v3.0.0 tag on 5e99278, pushed to origin.** **CI Windows build: GrainAnalyzer_Setup.exe 674 MB attached to release.** User installed on work PC and tested — app works offline.

## Released & pushed
- **main**: fast-forwarded to 5e99278
- **v3-dev**: 0470d4e (handoff commit after release)
- **Test suite**: 839 passed / 0 failed
- **Installer**: GrainAnalyzer_Setup.exe 674 MB on GitHub release
- **Security docs**: docs/SECURITY_OVERVIEW.md (offline guard, Windows Firewall, dependencies, build provenance)

## In-progress tasks
- **REP-DESIGN-01 (report redesign)** — report-engineer, v3-dev, mockups published (artifact MiCcw4BV5AgFyfUNEQ4Y52) with 4 options: **A** (Dashboard: KPI + lot table + charts), **B** (Side-by-side lots with CI panels, recommended), **C** (Heat-map table), **D** (Lot scorecards). User picked: awaiting. **Feasibility**: python-pptx no error-bar API → A/B need XML injection; B requires chart-to-table row alignment or falls back to image. No code written pending user choice.

## Next actions
1. **User picks mockup option** (A/B/C/D) or sketches alternative SUMMARY slide design
2. **User answers Excel problems query** — which sheet/column issues matter most for next fix batch
3. **Implement REP-DESIGN-01** — PPTX SUMMARY slide slide 2 + table pagination (max 14 rows/slide, continued headers) + Excel sheet cleanup

## Blocked / needs user
- **User choice: report mockup** (A/B/C/D or custom) and Excel priorities before implementation starts
- **D-13 real SEM images** (user will supply later; image quality tuning deferred to v3.1+)
- **Report long-table pagination rule** (proposed: max 14 rows/slide, continue on next with repeated header, truncate names >14 chars) — user approval pending

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
```
