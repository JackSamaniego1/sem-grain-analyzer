# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-30 (night, update 2: items 8, 11, 15 UI done; item 5 data half done; item 12 first step done; item 7 UI wiring running now)  
**Branch:** `v3-dev` | **Last commit:** `37e6040` (UI pass 2: items 8, 11, 15 UI done + GPU-check review fixes) | **Phase:** UPDATE 4 batch 3: items 8, 11, 15, 5 data, 12 step-1 done; item 7 UI wiring in progress  
**Resume with:** read this file only, then handoff/UPDATE_4.md and handoff/02_TEAM_ROSTER.md. **Note:** User overnight: keep working through UPDATE 4 tonight; ignore context-size warning when it fires.

## Done and committed

**v3.0.1 shipped** to user (PPTX summary slide + Excel fixes, 862 tests green).

**UPDATE 4 batch 1 & 2 complete (2026-09-29 to 2026-09-30):**
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
- **1111 passed** (full suite on committed state 4127728; 1 skipped, 0 failed)
- Offline guard clean (no network egress)
- Installer builds locally: GrainAnalyzer_Setup.exe 644 MB

## In-progress tasks (uncommitted, agents running)
- **Item 7 UI wiring** (ui-designer): Call core/perf.py `configure_threads()` at startup in main.py; AnalysisQueue generation fix; close-while-analysing check; crash log; analysis lock with "Continue anyway"/"Don't warn again" per ITEM_7_STABILITY_PLAN.md.

## NEXT 3 ACTIONS

1. **Review + commit ui/data layer branches** (detection-engineer: items 11, 7 UI + item 8 UI). Code-reviewer on each diff, full test suite after review/commit.
2. **Item 5 Resolution Profiles** (data layer + UI sidebar card) → item 7 UI wiring (thread caps lock during analysis) → item 12 Fable last (speed optimisation).
3. **Final full suite + smoke-app** → summary for user: manual checks needed (installer GPU page, real JEOL/Thermo images, sample Excel/PowerPoint, NSIS build on installer PC).

**Batch 3 status (parallel reports, data, core, UI; 2026-09-30 night):**
- [x] Item 15 DONE & REVIEWED (b4e38a5): Lot Summary in Excel (per-lot distribution chart + subtotals) + PowerPoint (job summary table + bar+trendline charts). Minor follow-up: ASTM G in subtotal rows label as average.
- [x] Item 11 DONE & REVIEWED (37e6040): Auto-fill mag/instrument/kV/WD on image load (core 959ffe0 + UI 37e6040). Fills Analyze card fields on image load.
- [~] Item 5 HALF DONE (data layer: a21da9d): ProfileStore, ResolutionProfile, check_fit, import/export, snapshot in session. UI sidebar card TBD (data layer review fixes in progress).
- [x] Item 8 DONE & REVIEWED (37e6040): Add grain lasso tool on Review canvas (split-tool style; core 273750b + UI 37e6040). Shortcut A.
- [x] Item 12 STEP 1 DONE (b5b1e7b): Thread cap keeps both cores for AI on 1–2 core PCs. Measurements recorded: standard modes 0.6–1.5 s; AI on CPU 31–80 s (thread-independent results); no lossless speed-up exists.
- [ ] Item 7 UI WIRING IN PROGRESS (ui-designer): configure_threads at startup, AnalysisQueue fix, close-while-analysing, crash log, analysis lock with "Continue anyway"/"Don't warn again".

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

## BATCH 3 PREP
- **Item 15** (report-engineer): Lot summary bar+trendline charts per lot + job summary (unblocked D-31).
- **Item 11** (detection-engineer): Auto-fill magnification/instrument/kV/WD from JEOL + Thermo metadata (parallel after item 4 UI).
- **Item 5** (ui-designer + data-architect): Resolution Profiles sidebar (manual selection, after item 15).
- **Item 10b UI** (ui-designer): Detection modes "AI-Assisted (GPU)" / "AI-Assisted (CPU)", GPU greyed with tooltip from `ai_devices()`.
- **Item 7** (opus): Stability lock during analysis on weak CPUs.
- **Item 8** (ui-designer + detection-engineer): Review "Add grain" tool (split-tool style).
- **Item 12** (Fable): Speed & usability optimisation, faster detection (LAST item).

## NIGHT SESSION — 2026-09-30 (update 2: batch 3 continues)
- **a21da9d (item 5 data)**: data/resolution_profiles.py (ProfileStore, ResolutionProfile, check_fit, nm_per_px_from, export/import, read-only mode on newer schema, ProfileError). Session image entry field `resolution_profile` (snapshot dict; CLEAR removes). 37 tests passing.
- **b5b1e7b (item 12 step 1)**: core/perf.py thread cap keeps both cores for AI on 1–2 core PCs. Measurements: standard modes 0.6 s (JEOL 1280×1024) & 1.5–2 s (synthetic 2048×1536); AI on CPU 31–80 s (2, 4, 8, 15 threads) with identical grains; no lossless speed-up via adaptive skip (rejected: -8 grains, +2% diameter). UI-side work pending (overlay copies, grain edits, batch overhead, startup).
- **37e6040 (UI pass 2)**: Item 11 UI (read-only "Image details" under image; ui/image_details.py; details in image_info.json), Item 15 UI (Lot Summary preview in report editor), Item 8 UI (Add grain lasso tool; shortcut A; Review+Analyze). GPU-check review fixes. 1287 tests passed.
- **User hand-checks pending**: Add grain on real image; details line with real JEOL/Thermo files; Lot Summary with many lots; 150/200% scaling; sample deck/workbook in PowerPoint/Excel; trendline = straight-line fit.
- **Agents running** (uncommitted): ui-designer on item 7 UI wiring per ITEM_7_STABILITY_PLAN.md.
- **Upcoming**: review/commit item 7 → full test suite → item 5 sidebar (profiles UI) → item 12 UI-side Fable optimisation → final suite + /smoke-app.
