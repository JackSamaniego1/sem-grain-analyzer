# SESSION STATE - read this first when resuming

**Last updated:** 2026-10-01 (v3.1.0 release prep complete; 1446 tests green; GitHub auth fixed; GPU pack internal-only)  
**Branch:** `v3-dev` | **Last commit:** `4181af2` (gitignore GPU pack + worktrees) | **Phase:** v3.1.0 built locally; awaiting user PC smoke-test + GitHub push
**Resume with:** read this file only. All code committed. Full suite 1446 passed, 1 skipped. Local tag `v3.1.0` ready. Next: (1) user installs GrainAnalyzer_Setup.exe (~676 MB) on clean PC and tests; (2) push v3-dev + tag to GitHub; (3) GitHub Release with Setup.exe ONLY (GPU pack stays internal pending NVIDIA review); (4) delete ~11 GB staging.

## v3.1.0 Release Prep DONE

**v3.1.0 release commit (e3a40fa)**: version.py bumped to 3.1.0, CHANGELOG entry. Local annotated tag `v3.1.0` created (NOT pushed yet).

**Installer built**: GrainAnalyzer_Setup.exe ~676 MB at repo root, v3.1.0, bundle verified (tutorial PNGs, SAM checkpoint, RapidOCR models, fonts, licences). Pre-bump build (same code) live-tested by user: "looks good". Packaged exe boot-checked.

**Work since last handoff (commits 1ac2f96 → 4181af2)**:
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
- Trial pack built: C:\ga_gpu_stage\GrainAnalyzer_GPU_Pack.exe 1.47 GiB (torch 2.14.0+cu126, CUDA 12.6, cuDNN 9.10).
- Pack NOT yet rebuilt from final source (BUILD_WINDOWS.bat gpu).
- Pack NOT yet installed or tested on-device.
- **Blocker 1 (D-48, new)**: GPU pack NOT published to GitHub Release yet. User decision: stay INTERNAL ONLY until NVIDIA CUDA/cuDNN redistribution terms reviewed by legal. Main installer GrainAnalyzer_Setup.exe (CPU torch, fallback to CPU at runtime) ships alone.
- **Blocker 2**: Fine-grained NVIDIA licence review before any public GPU pack release (check THIRD_PARTY_LICENSES.txt against actual EULA).
- **Blocker 3**: Real high-DPI test at 150/200% on actual hardware (verified offscreen only so far).
- ~11 GB staging folder C:\ga_gpu_stage can be deleted once pack copied to permanent location (if user decides to publish).

## NEXT 3 ACTIONS

1. **User installs GrainAnalyzer_Setup.exe on clean/work PC** and smoke-tests (splash, wizard, Reports, tutorial). Report any issues. Installer itself not yet run on a clean PC; user live-tested the packaged pre-bump build only.
2. **Fix GitHub auth and push**: Push v3-dev + local tag v3.1.0 to GitHub origin. Trigger GitHub Release build for Windows (macOS builder is expected to fail; v3.1.0 Windows-only like v3.0.0).
3. **GitHub Release and public availability**: Attach GrainAnalyzer_Setup.exe ONLY (no GPU pack). GPU pack stays in C:\ga_gpu_stage (internal testing only) pending D-48 NVIDIA legal review. Later: docs/SECURITY_OVERVIEW.md refresh (still says v3.0.0 / 839 tests).

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
```

## Handoff bookmarks
- `handoff/03_TASK_BOARD.md` — task tracking (REL-03 done, REL-04 review, GPU pack blocked internal-only)
- `handoff/05_DECISIONS.md` — ADR-style decision log (D-01..D-48)
- `handoff/06_PROGRESS_LOG.md` — append-only session log
- `handoff/02_TEAM_ROSTER.md` — agent roster and concurrency rules

