# SESSION STATE - read this first when resuming

**Last updated:** 2026-10-02 (v3.1.1 released to GitHub; v3.1.0 & v3.1.1 releases both published with Setup.exe + untested .dmg)  
**Branch:** `v3-dev` | **Last commit:** `ce5e47f` (release: v3.1.1) | **Phase:** v3.1.1 published on GitHub; awaiting user smoke test of Setup.exe; decision on .dmg asset; GPU pack rebuild + legal review
**Resume with:** read this file only. All code committed. Full suite 1452 passed, 1 skipped, 2 failed (timing budget flakes in test_detect_cancel.py on 19-min full run; pass when run alone). Next: (1) user downloads & tests v3.1.1 Setup.exe on work PC; (2) user decides: keep .dmg or remove from v3.1.0/v3.1.1 releases; (3) ask before deleting ~11 GB C:\ga_gpu_stage staging folder; (4) GPU pack rebuild from final source + NVIDIA CUDA/cuDNN legal review; (5) merge v3-dev→main user decision; (6) real high-DPI 150/200% test.

## v3.1.1 Release PUBLISHED (2026-10-02)

**v3.1.1 release commit (ce5e47f)**: version.py 3.1.1, CHANGELOG entry (two fixes from 3a6e450: lot double-click opens the lot, add-images wizard scrolls). Annotated tag `v3.1.1` created and PUSHED to origin.

**GitHub Actions build succeeded** (verified 2026-10-02 via public API): Both jobs passed (build-windows AND build-macos). Windows build OK. macOS .dmg built (unexpected but consistent with v3.1.0).

**Release v3.1.1 published** (https://github.com/JackSamaniego1/sem-grain-analyzer/releases/tag/v3.1.1): Not a draft. Assets: **GrainAnalyzer_Setup.exe 674 MB** (CPU-only, Windows) and **GrainAnalyzer.dmg 764 MB** (untested on Mac). No GPU pack (D-48 internal-only). **Status**: CI-built installer not yet tested by user (download from Release page and install on work PC to verify fixes). Local repo root .exe is still v3.1.0 build.

**Open question**: User decides: keep untested .dmg on v3.1.0/v3.1.1 releases or remove for Windows-only releases?

## v3.1.0 Release PUBLISHED (2026-10-01)

**v3.1.0 release commit (e3a40fa)**: version.py 3.1.0, CHANGELOG entry. Annotated tag `v3.1.0` created and PUSHED to origin.

**GitHub Actions build succeeded** (2026-10-01, verified via public API): Both jobs passed (build-windows AND build-macos).

**Release v3.1.0 published** (https://github.com/JackSamaniego1/sem-grain-analyzer/releases/tag/v3.1.0): Assets: GrainAnalyzer_Setup.exe 674 MB and GrainAnalyzer.dmg 765 MB (untested). No GPU pack (D-48 respected).

**Work since last handoff (commits 1ac2f96 → 813e4b4)**: Quick UI fixes, PowerPoint all-lots slides, per-job scale/scan isolation, Tutorial scoping, display scaling 150/200% offscreen fixes, 1446 tests on f8b004f.

## Environment & Blockers

**GPU Pack Status (D-47, D-48 follow-up)**:
- Trial pack staged: C:\ga_gpu_stage\GrainAnalyzer_GPU_Pack.exe 1.39 GiB (torch 2.14.0+cu126, CUDA 12.6, cuDNN 9.10); built from Sep 25 source tree, NOT final source.
- **D-48 (active)**: Pack stays INTERNAL ONLY. NOT published to GitHub Release until NVIDIA CUDA/cuDNN redistribution terms reviewed by legal. Main v3.1.0 release ships GrainAnalyzer_Setup.exe (CPU torch with runtime fallback) alone.
- **D-47 (blocker)**: Pack MUST be rebuilt from final source (BUILD_WINDOWS.bat gpu at e3a40fa) before any on-device testing.
- ~11 GB staging folder C:\ga_gpu_stage flagged for deletion pending user decision (holds the only trial copy; rebuild required before reuse).
- **Open**: Real high-DPI test at 150/200% on actual hardware (offscreen-verified only; wizard step display, image canvas scaling, table wrapping need live-PC confirmation).

## NEXT 3 ACTIONS

1. **v3.1.1 user smoke test**: Download GrainAnalyzer_Setup.exe from v3.1.1 Release page, install on work PC, confirm both fixes (lot double-click opens lot view, add-images wizard scrolls). If bugs found: fix on v3-dev, re-tag v3.1.1 (safe before user installs; tag -f if already pushed), push → CI rebuilds.
2. **User decides on untested .dmg**: Keep GrainAnalyzer.dmg on v3.1.0/v3.1.1 Releases or remove for Windows-only distribution?
3. **GPU pack rebuild + NVIDIA legal review**: Rebuild from final source (BUILD_WINDOWS.bat gpu at ce5e47f), trial test with NVIDIA driver on lab PC, legal review CUDA/cuDNN redistribution compliance. Ask user before deleting ~11 GB C:\ga_gpu_stage staging folder (trial pack, rebuild required before reuse).

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

