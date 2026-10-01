# UPDATE 4 — Batch 4 (pre-release UX overhaul)

User request 2026-09-30 after manual spot checks (all 7 checks passed). Three
workstreams, all required before the UPDATE 4 release. Decisions D-38..D-41.

## A. Analyze page — right sidebar becomes a step wizard  (D-38)

**Problem:** the current sidebar (Run card at top, scale/scan controls, filters,
detection modes, param panel, overlay/excluded sections) plus the "Scan area &
scale" tile *under* the image is confusing for a first-time user.

**New layout, top to bottom, in the right sidebar (380–420 px):**

```
┌ Resolution profile ─────────────────────────┐   (optional; existing
│  [switch]  profile picker / Save current…   │    ResolutionProfilesCard,
└─────────────────────────────────────────────┘    compacted)
            ▼  (arrow connector)
┌ 1  Set scan area ───────────────────────────┐
│  [All images]  [Current image]  [Edit…]      │
└─────────────────────────────────────────────┘
            ▼
┌ 2  Set scale bar ───────────────────────────┐
│  [All images]  [Current image]  [Edit…]      │
└─────────────────────────────────────────────┘
            ▼
┌ 3  Detection mode ──────────────────────────┐
│  4 mode tiles (existing ModeCard/AiModeGroup)│
└─────────────────────────────────────────────┘
            ▼
┌ 4  Start analysis ──────────────────────────┐
│  [Analyze all]  [Analyze current]            │
│  [Analyze selected (N)]                      │
└─────────────────────────────────────────────┘
            ▼
┌ Progress ───────────────────────────────────┐   (existing ring + title +
│  ring  "Ready to analyse" / % complete       │    caption; MOVED to bottom)
└─────────────────────────────────────────────┘
```

**Rules**
- Steps are *tiered*: a step is **active** (accent border, lit buttons) only
  when the previous step is **done**; later steps are greyed (disabled, muted
  text, tooltip "Finish step N first"). Step 1 is active as soon as a session
  with images is open. Done steps get a check badge and stay clickable (user
  can redo them).
- **Resolution profile** (above step 1): selecting a saved profile auto-fills
  steps 1 and 2 (scan area + scale) for the current image set and marks them
  done, so step 3 lights up immediately. Existing out-of-date rules unchanged.
- **Step 1 Set scan area**: `All images` / `Current image` run the existing
  info-bar auto-detect (same code as the old "Auto-find … (all images)" but
  scan-area part only) and mark the step done. `Edit…` appears (enabled) once
  done: opens the existing rectangle-draw scan-area flow on the current image
  with **Apply to current** / **Apply to all** buttons. "Full image" option
  stays reachable inside Edit….
- **Step 2 Set scale bar**: same pattern — `All images` / `Current image` run
  the existing scale-bar auto-find and highlight the found bar on the canvas;
  `Edit…` opens the existing calibration dialog (click the bar on screen, enter
  length + unit dropdown) with **Apply to current** / **Apply to all**.
  "Use metadata scale" stays available inside Edit….
- **Step 3 Detection mode**: the 4 tiles (existing). Choosing one marks the
  step done. Advanced parameters (ParamPanel) move under a collapsed
  "Advanced…" disclosure inside this step — not removed, just hidden.
- **Step 4 Start analysis**: `Analyze all` / `Analyze current` / `Analyze
  selected (N)` (existing actions). Cancel button appears while running.
- **Progress card** moves to the **bottom** of the sidebar (same widget).
- **Remove from the Analyze sidebar**: Excluded-regions section and the
  Overlay opacity slider (the canvas opacity pill from item 9 stays — D-34
  superseded for the sidebar slider only). Filters card (FilterCard) moves into
  the step-3 "Advanced…" disclosure.
- **Under-image tile (SetupTile)**: remove the "Auto-find scan area & scale
  bar" button and every edit control. It becomes a read-only **details strip**:
  scan area (px rect / "full image"), scale (value + unit, px/µm), and the
  IMAGE DETAILS line (mag / instrument / kV / WD) that item 11 fills.
- Step state is **per session**, derived from data (not stored flags): step 1
  done ⇔ every image in scope has a scan area set; step 2 done ⇔ every image
  has a scale; step 3 done ⇔ a mode is chosen (always true after first choice,
  persisted like today). Switching images must not reset the wizard.
- Keep all `objectName`s the tour uses (see B); add new ones:
  `wizard_profile`, `wizard_step_scan`, `wizard_step_scale`,
  `wizard_step_mode`, `wizard_step_run`, `wizard_progress`, plus per-button
  names `scan_all`, `scan_current`, `scan_edit`, `scale_all`, `scale_current`,
  `scale_edit`, `run_all`, `run_current`, `run_selected`.
- Keyboard shortcuts and the Review page are unchanged.

**Tests:** pytest-qt — step gating (buttons disabled until previous done),
profile selection lights step 3, Edit… appears only after done, progress card
is last child of the sidebar, SetupTile has no buttons, old objectNames for
removed widgets are gone, headless render at 1366×768 shows no clipped text.

## B. Tutorial rebuild — action-driven, with a bundled sample job  (D-39)

- Ship `assets/tutorial/` with **3 synthetic SEM images** (generated once by a
  script `tools/make_tutorial_images.py` using the conftest synthetic grain
  generator + a fake JEOL-style info bar with a 10 µm scale bar so auto-find
  works). Bundled in `grain_analyzer.spec` datas. Offline rule applies.
- On tour start, the app creates (or reuses) a job **"Tutorial"** › part
  **"Sample part"** › lot **"Lot 1"** in the user's workspace and copies the 3
  images in. Removing the job later is just deleting it like any other.
- Steps (each one **points at exactly one control** and **advances
  automatically** when that action happens — no Next/Back buttons; only
  **Skip tour** remains, plus the "don't show again" checkbox on the welcome
  card):
  1. Welcome (centred; advances on the single "Start" button).
  2. Open the Tutorial job from Projects → advances when the session opens.
  3. Step 1 `Set scan area › All images` → advances when scan areas are set.
  4. Step 2 `Set scale bar › All images` → advances when scales are set.
  5. Step 3 choose a detection mode → advances on mode chosen.
  6. Step 4 `Analyze all` → advances when the run finishes (progress card is
     spotlighted meanwhile; text explains % complete).
  7. Review page: click a grain → advances on selection.
  8. Reports page: `Export report to PowerPoint` → advances when the file is
     written (the save dialog is the user's own choice of path).
  9. Finish (centred; "Done" button).
- Advancement is driven by **AppState / shell signals** (session opened,
  scan/scale changed, mode changed, analysis finished, selection changed,
  export finished) — not by timers. If the user does the action some other
  way (keyboard, menu) the step still advances.
- Skip at any point leaves the Tutorial job in place.
- `ui/tour/steps.py` gets a `TourStep.advance_on` hook (callable that connects
  a signal and returns a disconnect). Controller: remove `next`/`back` UI,
  keep `skip`, `finish`.

**Tests:** headless tour runs end-to-end by emitting the signals in order;
tour has no Next/Back buttons; Tutorial job created once (idempotent); images
bundled and loadable; offline guard passes.

## C. Launcher splash — animated grain rendering  (D-40)

- Replace the static `QSplashScreen` in `main.py` with a frameless
  `QWidget`-based splash (same size/position behaviour) that paints an
  **animated grain microstructure**: a Voronoi-style polycrystal (seeded, ~60
  grains) with boundaries drawing in over ~1.2 s, subtle per-grain shading
  from the design tokens, app name/version, and a thin progress line. Pure
  QPainter, no media files, no network. Respect `GRAIN_REDUCED_MOTION`
  (static final frame). Must not delay startup: main window loads while the
  animation runs; splash closes when the window is ready and the animation
  has finished at least one pass.
- Keep `_create_splash()` name so `main.py` structure stays readable.

**Tests:** splash widget renders headlessly to a PNG without exceptions;
reduced-motion path; `python main.py` smoke still OK.

## Order & ownership
1. A — ui-designer (opus), then code-reviewer, then qa-engineer verify.
2. B — ui-designer (tour + steps) after A merges (object names depend on it);
   build-engineer adds `assets/tutorial` to the spec; qa-engineer tests.
3. C — ui-designer; build-engineer confirms PyInstaller bundle still boots.
Commit each separately on `v3-dev`; `/save-handoff` after every commit.
