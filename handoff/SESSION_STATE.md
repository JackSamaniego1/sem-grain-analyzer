# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-29 (UPDATE 4 batch 1 execution started; item 1 done; 4 agents running)  
**Branch:** `v3-dev` | **Last commit:** `42e44a2` (Projects: New Lot/Part adds multiple entry boxes; one Create adds all and stays on page) | **Phase:** UPDATE 4 batch 1 in progress  
**Resume with:** read this file only, then handoff/UPDATE_4.md and handoff/02_TEAM_ROSTER.md.

## Done and committed

**v3.0.0 released to user** and user reports working offline on lab PC.

**v3.0.1 prep (PPTX redesign, REP-DESIGN-01):**
- **8c5b728**: PPTX slide 2 summary table = one row per part (Part / Lots count / Images total / ASTM G±CI / mean diameter±SD / mean area±SD); three bar charts (G/diameter/area by part number); per-image data tables restyled to match, max 14 rows/slide with "(continued n/m)" header repeat, no red rows. Tests pass; code-reviewer approved.
- **3a7c6e2**: Excel export duplicate sheet names (case-insensitive) no longer crash; truncated "Lot" column auto-widened. Both shipped in v3.0.1 batch.

## Test suite status
- **862 tests passing / 0 failing** (full suite green)
- Offline guard clean (no network egress)
- Installer builds locally: GrainAnalyzer_Setup.exe 644 MB

## In-progress tasks (UPDATE 4 batch 1)
- **Item 16 (Units/bins)**: report-engineer fixed `build_bins` (equal-width, min/max), bin_labels() in reports/charts.py; ui/pages/charts.py wired; tests/test_report_bins_units.py added. Pending: code-reviewer, commit.
- **Item 2 data half**: data-architect added `add_images_to_lot(lot_path, paths, *, catalog=None, image_name_template=None, name_context=None) -> AddImagesResult(.added/.skipped/.rejected, .summary())` + tests/test_data_add_images.py. Pending: code-reviewer, commit. UI half (drop handler + app_state.py wiring) still to do.
- **Item 3 (Image checkboxes + Analyze selected)**: ui-designer running (ui/pages/analyze_page.py, ui/pages/image_tree.py, tests/test_ui_analyze_selected.py).
- **Item 4 core (Offline OCR, RapidOCR, info-bar text)**: detection-engineer running; user supplied 2 real SEM images (JEOL + Thermo, now D-33); tests/sem_infobar_fixtures.py prepped.

## Next 3 actions (UPDATE 4, batch 1 in progress)

1. **Code-review item 16** (reports/charts.py, ui/pages/charts.py, tests/test_report_bins_units.py) → commit → save handoff → tick.
2. **Code-review item 2 data** (data/session_io.py, tests/test_data_add_images.py) → commit → save handoff → UI half ready.
3. **Monitor items 3, 4** (ui-designer, detection-engineer running). When each completes: review → commit → save handoff → tick.

**Batch 1 status** (handoff/UPDATE_4.md):
- [x] Item 1: New Lot/Part: multiple boxes, stay on page, keep data (42e44a2)
- [ ] Item 2: Drag images from a folder into a lot (committed: data half; UI half pending)
- [ ] Item 3: Image checkboxes + "Analyze selected" (running)
- [ ] Item 6: Unit dropdown for scale length + highlight
- [ ] Item 9: Overlay opacity slider
- [ ] Item 13: Review image list grouped Job › Part › Lot
- [ ] Item 14: Display mode persists across images
- [ ] Item 16: Units & bins bug (running: review pending)
- [ ] Item 10a: Remove CPU tile

**Unblocked (decisions D-31/D-32/D-33):**
- Item 15 (Lot summary charts): build now, user reviews during testing (D-31)
- Item 10b (GPU/CUDA): NVIDIA GPU confirmed, use CUDA torch build (D-32)
- Item 4 (OCR): 2 real SEM images supplied to scratch/real_sem/ (D-33)

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
- `handoff/05_DECISIONS.md` — ADR-style decision log (D-01..D-30)
- `handoff/07_IDEAS_BACKLOG.md` — ideas (top 25 active; rest archived)
