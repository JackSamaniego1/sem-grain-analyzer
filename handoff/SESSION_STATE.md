# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-30 (UPDATE 4 batch 2 complete: items 1–3, 6, 9, 10a, 13, 14, 16–19 done; items 4, 10b partial; batch 3 queued)  
**Branch:** `v3-dev` | **Last commit:** `4127728` (items 9+13 done: overlay opacity pill, Review image list grouped) | **Phase:** UPDATE 4 batch 2 complete, batch 3 starting  
**Resume with:** read this file only, then handoff/UPDATE_4.md and handoff/02_TEAM_ROSTER.md.

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

## In-progress tasks (uncommitted, agent running)
- **Item 10b installer page** (build-engineer): GPU option checkbox in create_nsis_script.py, auto-detect pack file in $EXEDIR, Browse fallback, NVIDIA presence check, silent `/GPU=1`, run pack with `/S` and check exit code. Also tests/test_gpu_pack_scripts.py. Will attempt makensis compile check.

## NEXT 3 ACTIONS

1. **Review + commit item 10b installer page**, run full test suite; commit.
2. **Items 4 UI wiring (in Automatic mode, worker thread) + item 11 (metadata auto-fill) + item 10b UI (GPU/CPU modes, greyed out with tooltip)** — ui-designer + detection-engineer.
3. **Items 15 (lot summary bar+trendline charts) + 5 (Resolution Profiles sidebar)** — report-engineer + ui-designer (after item 4 UI).

**Batch 2 status** ✓ complete:
- [x] Item 6: Unit dropdown + pulse (7a70d74)
- [x] Item 14: Display mode persist (7a70d74)
- [x] Item 10a: Remove CPU tile (7a70d74)
- [x] Item 17: PPTX distribution slides (83a864e)
- [~] Item 4: RapidOCR core done (8a059e0 reviewed); UI wiring pending
- [x] Item 9: Overlay opacity slider (4127728)
- [x] Item 13: Review image list grouped (4127728)
- [x] Item 19: PPTX contents page (1c3eec9)
- [~] Item 10b: Core + packaging done (822bfb3, fcf57cc); UI modes + installer page + trial build pending

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
