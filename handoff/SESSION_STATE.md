# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-30 (late night: UPDATE 4 fully code-complete with item 5 follow-up done)  
**Branch:** `v3-dev` | **Last commit:** `9ccaf90` (item 5 follow-up: `result_scan` persisted, scan-area reopen survives) | **Phase:** UPDATE 4 code-complete; all 16 items done; GPU trial build pending NSIS; manual spot checks pending user  
**Resume with:** read this file only. All code-complete; GPU trial build & manual spot checks pending user.

## Done and committed

**v3.0.1 shipped** to user (PPTX summary slide + Excel fixes, 862 tests green).

**UPDATE 4 COMPLETE (code & tests; 2026-09-29 to 2026-09-30):**
- **Batch 1 (2026-09-29):**
  - **42e44a2**: Item 1 — New Lot/Part dialog: multiple entry boxes, stays on page.
  - **b343d3f + f42fdab**: Item 2 — Drag images into lot (copy into job folder).
  - **05435d7**: Item 3 — Image checkboxes + "Analyze selected (N)" button.
  - **e09677f**: Item 16 — Units & bins fix (nm²↔µm² rescale, equal-width bins).
  - **fa6c95f + 2ce1e24**: Item 18 — PPTX percentile slide per part & lot.

- **Batch 2 (2026-09-30):**
  - **83a864e**: Item 17 — PPTX per-lot grain distribution slides (area + diameter bars + KDE trendline) + lot-to-lot comparison.
  - **7a70d74**: Items 6, 14, 10a — scale-length unit dropdown + accent pulse; display mode persists; "AI runs on CPU" tile removed.
  - **8a059e0** (reviewed): Item 4 core — RapidOCR offline info-bar OCR (JEOL 100 nm, Thermo 100 µm, vendor logo, beam current parsed).
  - **1c3eec9**: Item 19 — PPTX contents page as slide 2 with page numbers/ranges and clickable links.
  - **822bfb3**: Item 10b core — core/ai_device.py (`ai_devices()`, `resolve_device`, GPU OOM→CPU redo, device notes in results).
  - **fcf57cc**: Item 10b packaging — GPU pack scaffolding (OFF by default), make_gpu_pack.py, create_gpu_pack_nsis.py, requirements-gpu.txt.
  - **4127728**: Items 9, 13 — Overlay opacity pill (top-right image, persisted); Review image list grouped Job › Part › Lot.

## Test suite status
- **1366 passed** (full suite with item 5 follow-up scan persistence & scan-area reopen tests; 2 skipped, 0 failed; `-X faulthandler` confirms no fatal crashes)
- Offline guard clean (no network egress)
- Installer builds locally: GrainAnalyzer_Setup.exe 644 MB
- **NSIS not installed on dev PC** (no makensis on PATH) — GPU trial build remains blocked pending build-engineer environment setup

## Completed follow-ups & deferred items
- **Item 5 follow-ups DONE**: out-of-date results rule (flag + exclude from reports/exports until re-analysed, survive reopen), label cleared by hand changes, card tests added. Pending: scan-area-only reopen flag needs data/session model field (low priority).
- **Item 10b GPU trial build DEFERRED**: code complete (core ai_device modes, installer GPU page, pack scripts); NSIS not installed on dev PC → trial build + manual install test pending user's environment.
- **Item 12 DONE (f440f40)**: overlay_layer rebuilt with per-grain LUT on uint32 view (2048×1536: 69→19 ms; with exclusions 112→39 ms) + overlay_patch() for changed rectangles; grain_canvas patches only changed rect after edits (no 3 full rebuilds); skimage/scipy.stats import lazily on first use; draw_overlay=False in analyze_image; app startup 980→510 ms. Tests: test_ui_overlay_patch (pixel-identical), test_startup_lazy_imports. Full suite 1360 passed, 2 skipped; smoke launch OK.

## NEXT 3 ACTIONS

1. **User manual spot checks on real SEM images**: Add grain; image details (JEOL/Thermo); sample report (Lot Summary, percentile, distribution, contents slides in PPTX + Lot Summary/raw data in Excel); Resolution Profiles end-to-end; editing during analysis lock; crash log after forced error; display scaling 150/200%.
2. **GPU trial build (build-engineer)** once NSIS is on PATH: build GrainAnalyzer_GPU_Pack.exe → manual pack install + GPU device detection on a work PC.
3. **Release prep for v3.1** after spot checks pass (version bump via version.py, CHANGELOG, tag) — only when user asks.

**Batch 3 final status (code & tests complete; 2026-09-30):**
- [x] Item 15 DONE (b4e38a5): Lot Summary in Excel (per-lot distribution chart + subtotals) + PowerPoint (job summary table + bar+trendline charts).
- [x] Item 11 DONE (37e6040 UI, 959ffe0 core): Auto-fill mag/instrument/kV/WD on image load. Fills Analyze card fields on image load.
- [x] Item 5 DONE + FOLLOW-UPS (502d8c8 UI, a21da9d data, 7ec0338 follow-ups): Resolution Profiles data + UI. Out-of-date rule (scale/scan changes flag + exclude from reports, survive reopen); label clear on hand changes; card tests added.
- [x] Item 8 DONE (37e6040 UI, 273750b core): Add grain lasso tool on Review canvas (split-tool style). Shortcut A.
- [x] Item 7 DONE + GC FIX (502d8c8 UI, a51a2d2 core, d880550 GC guard): Stability during analysis on weak CPUs. Thread caps, crash log, analysis lock. GC guard (d880550): Python cycle collector runs only on UI thread → no hard crashes from freed Qt objects.
- [~] Item 10 PARTIAL (code done): GPU/CPU modes, installer page, pack scripts complete (822bfb3, fcf57cc, c4b2584, 455e548); trial build + manual checks deferred (NSIS not installed locally).
- [~] Item 12 PARTIAL (step 1 done): Thread cap optimisation done (b5b1e7b); UI-side work deferred (overlay, grain edits, batch startup).

**Batch 2 status** ✓ complete:
- [x] Item 6: Unit dropdown + pulse (7a70d74)
- [x] Item 14: Display mode persist (7a70d74)
- [x] Item 10a: Remove CPU tile (7a70d74)
- [x] Item 17: PPTX distribution slides (83a864e)
- [x] Item 4: RapidOCR core + UI auto-fill on Auto-find (455e548 reviewed; core 8a059e0)
- [x] Item 9: Overlay opacity slider (4127728)
- [x] Item 13: Review image list grouped (4127728)
- [x] Item 19: PPTX contents page (1c3eec9)
- [x] Item 10b: Core + packaging + UI modes (822bfb3, fcf57cc, c4b2584 installer, 455e548 UI modes). Trial build + manual GPU pack checks pending (D-35, D-36)

**User decisions this session (D-34 through D-37):**
- D-34: Keep BOTH opacity controls (new pill + Analyze side-panel slider).
- D-35: GPU option ships as SEPARATE file on GitHub Release (GrainAnalyzer_GPU_Pack.exe). Main installer auto-detects pack in $EXEDIR; if not found, Browse button.
- D-36: App is "universal": one install, GPU if usable else CPU (auto).
- D-37: JEOL 110–135 mm check stays as is (user has no images at other magnifications).

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
```

## Handoff bookmarks
- `handoff/UPDATE_4.md` — 16-item checklist; read before starting
- `handoff/02_TEAM_ROSTER.md` — agent roster and concurrency rules
- `handoff/03_TASK_BOARD.md` — task tracking (flip status todo→doing→review→done/blocked)
- `handoff/05_DECISIONS.md` — ADR-style decision log (D-01..D-37)
- `handoff/07_IDEAS_BACKLOG.md` — ideas (top 25 active; rest archived)

