# SESSION STATE - read this first when resuming

**Last updated:** 2026-09-29 (PPTX redesign and Excel fix delivered; UPDATE 4 ready to start)  
**Branch:** `v3-dev` | **Last commit:** `8c5b728` (PPTX part-level summary slide 2 and per-part data table slides) | **Phase:** UPDATE 4 execution (batch 1 ready)  
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

## In-progress tasks
None actively running (awaiting UPDATE 4 dispatch).

## Next 3 actions (UPDATE 4, batch 1)

1. **Read handoff/UPDATE_4.md** (16-item checklist with batches, execution order, and user answers)
2. **Delegate batch 1 items** (1, 2, 3, 6, 9, 13, 14, 16, 10a) to team agents per roster
3. **After each item:** tests → code-reviewer → commit → save handoff → tick box

**Batch 1 items** (handoff/UPDATE_4.md):
- Item 1: Lot filter (Status, Exclude, Baseline)
- Item 2: Grain size chart legend (Part / Lot / None)
- Item 3: Scale-bar snapping tolerance / skip-snap dialog
- Item 6: Calibration UI polish (clarity labels, uncertainty ranges)
- Item 9: Column width persistence (Settings → ui_state.json)
- Item 13: Duplicate images detection (UI + core)
- Item 14: Part tags / free-text field (model + UI + export)
- Item 16: Units/bins default (Median area/ASTM G bug fix)
- Item 10a: Remove CPU tile from Analyze page

**Blocked / awaits user:**
- Item 4 (OCR): awaits user's JEOL/Thermo test images
- Item 15 (Lot summary charts): awaits user approval of mockups before coding
- Item 10b (GPU/CUDA): awaits confirmation that GPU is NVIDIA

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
