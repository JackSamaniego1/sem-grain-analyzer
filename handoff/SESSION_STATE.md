# SESSION STATE - read this first when resuming

**Last updated:** 2026-10-01 (v3.1.0 release tagged & pushed; GitHub Actions build queued; docs refreshed)  
**Branch:** `v3-dev` | **Last commit:** `813e4b4` (security overview v3.1.0) | **Phase:** v3.1.0 pushed to origin; build IN PROGRESS; Release asset verification pending
**Resume with:** read this file only. All code committed. Full suite 1446 passed, 1 skipped. v3.1.0 tag at e3a40fa. Next: (1) verify GitHub Release has ONLY GrainAnalyzer_Setup.exe (no GPU pack); (2) ask user before deleting ~11 GB GPU staging; (3) high-DPI test + NVIDIA review still open.

## v3.1.0 Release PUBLISHED

**v3.1.0 release commit (e3a40fa)**: version.py 3.1.0, CHANGELOG entry. Annotated tag `v3.1.0` created (SHA: e3a40fa) and PUSHED to origin.

**Installer built & verified**: GrainAnalyzer_Setup.exe 676 MB at repo root, bundle verified (tutorial PNGs, SAM checkpoint, RapidOCR models, fonts, licences). Pre-bump build live-tested by user ("looks good"). Packaged exe boot-checked.

**GitHub Actions status**: Build & Release workflow queued (tag v3.1.0 pushed). Windows build expected to succeed; macOS expected to fail (Windows-only release like v3.0.0). Awaiting: asset list verification (must have ONLY GrainAnalyzer_Setup.exe, no GPU pack per D-48).

**Work since last handoff (commits 1ac2f96 → 813e4b4)**:
- Quick UI fixes: Reports inspector no longer clips (long export file names elide); Projects picker tiles toggle individually; Analyzer load ADDS instead of replacing (per-job scale/scan area, no borrowing); Select all / Remove selected in list; removed images vanish with Undo button.
- PowerPoint: three all-lots "by lot" slides after Grain Size Summary (mean diameter / area / density by lot, bars + connecting line, >20 lots continue on next slide).
- Bug fix: each job in mixed Analyzer load keeps its OWN scale and scan area (autosave writes per-job values; detection mode + grain filters shared).
- Tutorial fully scoped to Tutorial job (scan/scale/analyze/filters/edits/report/exports never touch user images/folders).
- Display scaling fixes for 150/200% (window minimum 940×520, scrolling centre columns, wrapping headers, elided labels; verified offscreen only, not real high-DPI).
- New tests: test_ui_analyzer_load_remove.py, test_ui_display_scaling.py, test_ui_report_inspector_fit.py, extended tour + report tests.
- Full suite on f8b004f (before version bump): **1446 passed, 1 skipped, 0 failed**.

**Decisions logged (D-48 onwards)**: Loading into Analyzer is additive; removal never deletes, removed items disappear (Undo); per-job scale/scan area with shared mode/filters; three all-lots slides with connecting line; GPU pack INTERNAL ONLY for now (legal NVIDIA CUDA/cuDNN redistribution analysis pending before publication).

## Environment & Blockers

**GPU Pack Status (D-47, D-48 follow-up)**:
- Trial pack staged: C:\ga_gpu_stage\GrainAnalyzer_GPU_Pack.exe 1.39 GiB (torch 2.14.0+cu126, CUDA 12.6, cuDNN 9.10); built from Sep 25 source tree, NOT final source.
- **D-48 (active)**: Pack stays INTERNAL ONLY. NOT published to GitHub Release until NVIDIA CUDA/cuDNN redistribution terms reviewed by legal. Main v3.1.0 release ships GrainAnalyzer_Setup.exe (CPU torch with runtime fallback) alone.
- **D-47 (blocker)**: Pack MUST be rebuilt from final source (BUILD_WINDOWS.bat gpu at e3a40fa) before any on-device testing.
- ~11 GB staging folder C:\ga_gpu_stage flagged for deletion pending user decision (holds the only trial copy; rebuild required before reuse).
- **Open**: Real high-DPI test at 150/200% on actual hardware (offscreen-verified only; wizard step display, image canvas scaling, table wrapping need live-PC confirmation).

## NEXT 3 ACTIONS

1. **Verify GitHub Release assets** (build may already be done or in progress): Check https://github.com/JackSamaniego1/sem-grain-analyzer/releases/tag/v3.1.0 — must have ONLY GrainAnalyzer_Setup.exe attached. If GPU pack appears, delete it from the release (D-48: internal only).
2. **Confirm user has tested GrainAnalyzer_Setup.exe on a clean/work PC** (smoke-test splash, wizard, Reports, tutorial). Installers on lab PC working offline only; no network egress.
3. **Clean-up decision on ~11 GB C:\ga_gpu_stage**: Ask user before deleting the staging folder (holds only copy of trial GPU pack; pack must be rebuilt from final source before on-device use per D-47). NVIDIA licence review still pending before any GPU pack publication.

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
```

## Handoff bookmarks
- `handoff/03_TASK_BOARD.md` — task tracking (REL-03 done, REL-04 review, REL-05 in progress)
- `handoff/05_DECISIONS.md` — ADR-style decision log (D-01..D-48)
- `handoff/06_PROGRESS_LOG.md` — append-only session log
- `handoff/02_TEAM_ROSTER.md` — agent roster and concurrency rules
- `handoff/UPDATE_4.md` — v3.1.0 feature batch execution checklist (all 16 items code-complete)

