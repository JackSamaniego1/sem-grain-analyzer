# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-25 (Feedback fixes complete; v3.0.0 retag pending)
**Branch:** `v3-dev` | **Last commit:** `009530d` (Excel: per-image lot/part, Lot Summary sheet, Remove fix) | **Phase:** Release — waiting for user to test install locally.
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed
**v3.0.0 complete**: All Phase 0–6 work delivered + user feedback fixes (FB-01..03, 009530d). UX batch (UX-01..16) finished overnight: pixel LRU, results columns, DPI aware; multi-lot report (lot summaries + TOST matrix), editable charts, custom palettes, lazy-load images, overlay opacity export; pre-analysis gate, settings order, scale/image scope toggles, image removal with restore, nav tooltips, CPU chip label, spinner fix, cancel <1 s. **Feedback fixes: per-image lot/part on Overview (FB-01), new Lot Summary sheet (FB-02, default on), Remove menu fix (FB-03).** **839 tests passed**. **v3.0.0 tag** needs retagging locally (was 709fd7d, needs to move to 009530d). **GrainAnalyzer_Setup.exe** needs rebuild at 009530d; bundle 1.3 GB.

## Ready to ship
- **main**: fast-forwarded to v3-dev at 709fd7d (before feedback fixes)
- **v3-dev**: all UX, FIX, INN, feedback (FB-01..03) work complete at 009530d
- **Test suite**: 839 passed / 0 failed
- **Offline audit**: passed (FND-07)
- **Tag v3.0.0**: needs retagging to 009530d (was 709fd7d, local only)
- **Installer**: GrainAnalyzer_Setup.exe needs rebuild at 009530d (644 MB target)

## Handoff to user
1. **TODO: rebuild installer at 009530d** and locally retag v3.0.0 (was at 709fd7d)
2. Install test: run new GrainAnalyzer_Setup.exe on dev machine (UAC required; silent install untested)
3. If bug found: fix on v3-dev, ff main, retag v3.0.0 locally (safe only before push)
4. On approval: `git push origin main v3-dev --tags` (auth verified as JackSamaniego1)
5. CI builds release; user downloads to flash drive

## Blocked / needs user
- **GitHub push**: awaiting user confirmation (auth verified FND-04)
- **D-13 real SEM images** (user will supply later; image quality tuning deferred to v3.1+)

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
```
