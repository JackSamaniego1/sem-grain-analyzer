# Changelog

All notable changes to Grain Analyzer are documented here.

## [3.0.0] — 2026-09-25

The v3.0 release is a ground-up rebuild of the app: correct grain detection
on images with black/invalid regions, a real project/sample/lot data
library you can browse and reopen, an editable report model behind much
better Excel and PowerPoint exports, a full visual redesign, a lot
comparison tool, and a hardened offline/private guarantee. 820 automated
tests cover the release.

### Added

- **Project library (Projects page).** Analyses are now organized and saved
  on disk as Project › Sample › Lot › Session, the way the Keyence
  microscope software does it. Create, rename, and reopen past work from a
  browsable tree with thumbnails; deleted items go to a **Trash** folder
  and can be restored (an "Undo" appears right after you delete or move
  something).
- **Lot comparison.** Select two or more lots on the Projects page and
  compare them side by side: mean ASTM G ± confidence interval per lot, a
  colour-coded ΔG matrix, an overlaid grain-size distribution chart, and a
  plain-English pass/fail equivalence verdict against a chosen baseline lot
  (Welch ANOVA + TOST equivalence test under the hood, adjustable
  tolerance).
- **Guided workflow.** Five pages in a fixed left-hand order — Projects,
  Wizard, Analyze, Review, Reports — plus an 11-step guided tour for
  first-time users (Help menu to replay).
- **Grain editing on the Review page.** Lasso-select, merge, and cut
  grains directly on the image, with undo and a keyboard-shortcut sheet
  (L/M/C/V); corrections are saved with the session.
- **Three scale-bar calibration modes** — Rectangle, Level (default, with
  snapping and a magnifier loupe), and Free — so you can calibrate
  precisely regardless of how the bar is drawn on the micrograph.
- **Editable report model.** Titles, captions, and section text can be
  edited in-app before export and re-edited later — edits round-trip
  through `report.json` and are honoured by both exporters.
- **Upgraded Excel workbook** — a per-image overview table, colour-coded
  tabs, charts with correct axis units, and raw per-grain data sheets
  placed last.
- **Lot Summary sheet (Excel)** — right after the Overview, one
  grain-size distribution chart per lot, titled with the lot name, with
  that lot's image count, total grains, mean ASTM G and mean grain
  diameter/area beside it. Lots with the same name in different parts are
  kept apart ("Part · Lot"). Can be switched off in the report editor.
- **PowerPoint deck export** — a formatted 16:9 deck with per-image
  slides, calibration and methods details, and native charts, built with
  the same report model as the Excel export.
- **ASTM E112 grain size (G)** reported for every analyzed image, with a
  compliance checklist, alongside area, ECD, and other per-grain stats.
- **Optional spec limits and calibration verification.** A project or
  sample can define spec limits (mean G, %RA, etc.) that results are
  checked against, showing a pass/fail badge on the lot card; a separate,
  independently-optional calibration-check workflow tracks instrument
  verification against a reference standard with a status chip ("Cal ✔ 2
  d ago" / "Cal due" / "Cal failed"). Both are off by default and invisible
  until turned on, so existing workflows are unaffected.
- **Full visual redesign** — new design system, icon set, light/dark
  themes, and an animated navigation shell across every page.
- `THIRD_PARTY_LICENSES.txt` listing the licence and upstream URL for every
  runtime dependency, bundled into the installer.
- **Load a job, part, or lot straight into the Analyzer.** From the
  Projects page, select any level of the tree and press "Load into
  analyzer" to pull in every image under it in one step. The Analyzer's
  image list becomes a collapsible tree (Job › Part › Lot › images)
  showing counts and status per group, and loads lazily in the background
  so a few hundred images stay responsive.
- **Multi-lot report, built and exported straight from Analyze.** Once
  more than one lot is loaded, Reports produces a combined Excel/PowerPoint
  export: a per-lot summary table (grain count, mean G ± CI, mean ECD,
  spec verdict), a lot-comparison section (ΔG matrix and equivalence test
  against a baseline lot), per-lot detail sections, a combined grain-size
  distribution overlay, and raw per-grain data tagged with its Lot.
  Single-lot reports are unchanged.
- **Pre-analysis scan-area & scale confirmation.** Analysis can't start
  until every image's scan area and magnification/scale are confirmed.
  An "Auto-find scan area & scale bar (all images)" button detects both in
  one pass; a result tile under each image shows the scan area, scale-bar
  length, µm/px, and its source (auto/metadata/manual), with a way to
  correct it. Clicking Analyze before everything is confirmed highlights
  exactly what still needs checking.
- Scale bar can be set for "this image only" instead of all loaded images,
  for fixing one image's calibration without touching the rest of the lot.
- **Overlay opacity slider** in the Analyze settings; the same value is
  used for the grain overlay drawn into exported reports.
- **Remove an image from the Analyzer** without touching its file or the
  lot on disk, plus an "Add all from lot" button to bring removed images
  back.
- **Editable summary charts.** Bin/bucket size, units (µm/nm, ECD/area/G…),
  axis ranges, titles, labels, the normal-fit line, and colours can all be
  adjusted on a chart, with a "Save as my default" option that's
  remembered and applied to new reports.
- **Custom report palettes.** Pick three colours by hex code or colour
  wheel to build your own report palette; it's saved locally and
  selectable next to the four built-in palettes.

### Changed

- **Migrated PyQt6 → PySide6.** PyQt6 is GPL/commercial; PySide6 is LGPL v3
  and free to distribute with no licence purchase required.
- **Black/invalid image regions are no longer detected as grains**, in
  every detection mode, and coverage/statistics are now computed only over
  the valid analysed area — the most common source of inflated grain
  counts on real SEM images.
- Non-destructive post-analysis grain filters, so cleanup no longer
  requires re-running detection.
- `version.py` is the single source of truth for the app version
  (`3.0.0`) and app name; `main.py`, the app shell, Settings page, the
  PyInstaller spec, and the NSIS installer script all read from it.
- CPU-only PyTorch wheel used in build scripts and CI
  (`--index-url https://download.pytorch.org/whl/cpu`) to keep the
  installer smaller; a GPU is never required to run the app.
- **AI-assisted is now the first and default detection mode**; the old
  "Automatic" mode has been removed and the Advanced parameters section is
  hidden while AI-assisted is selected.
- Cancelling an analysis run now stops within about a second, instead of
  continuing to grind through remaining images in the background.
- Large jobs (hundreds of images loaded at once) use less memory: label
  maps for images you're not actively viewing are compressed, and overlay
  images are generated on demand instead of all being held in memory
  together.

### Fixed

- Excel Overview on a report covering several lots showed the same lot
  and part number for every image; each image now shows its own, and the
  report header lists every lot/part included (e.g. "Lot: L-1, L-2").
- "Remove from analyzer" (right-click in the Analyze image list) raised an
  error instead of removing the image; the other right-click entries
  ("Add back", "Remove this lot's images") had the same fault.
- Numerous PowerPoint export layout bugs: title/subtitle overlap, table
  and methods-section text overflow, distribution chart normal-fit line
  rendering, and calibration details not appearing on the methods slide.
- Excel column widths for Lot/Sample/Image names now auto-size; long Lot
  Note text wraps instead of being clipped.
- Percent values no longer display as "-0.0%"; scale-bar detection no
  longer mistakes a user-drawn frame for the scale bar itself.
- Narrow-window layout fixes (toolbars, stat cards, card titles) so the
  app remains usable on smaller/laptop screens.
- `BUILD_WINDOWS.bat` no longer refers to the stale `dist\SEMGrainAnalyzer`
  path; it always matches the spec's `GrainAnalyzer` output folder.
- The report editor's Images tab no longer freezes the app — thumbnails
  load lazily in the background and are cached.
- "Analyze current" no longer spins the "Analyze all" button's spinner
  (and vice versa); only the button you clicked animates.
- Nav rail hover labels (e.g. "Analyze") now appear almost instantly
  instead of after a noticeable delay.
- The bottom-right status chip now reads clearly, e.g. "AI runs on: CPU"
  (or the GPU name), with a tooltip explaining it.
- The grain filters "Apply" button no longer says "Apply to all images"
  when its scope is set to "This image".

### Security / privacy

- **Hardened offline guarantee.** `core/offline_guard.py` is installed
  before any other import in `main.py`, blocking any non-loopback network
  call the process makes and recording the attempt locally. `tests/test_offline.py`
  scans all first-party source for network-capable imports and asserts
  zero network activity during a full detection + export run. There is no
  update checker, telemetry, crash reporting, or cloud sync anywhere in
  the app, and no data you load or export ever leaves the machine.
- The SAM AI-detection checkpoint is downloaded and hash-verified once at
  **build time only** and bundled into the installer — never fetched at
  runtime. A missing bundled asset tells you to reinstall; the app never
  shows a download link.

### Removed

- Stray `.github/workflows/build.ymlresources/` directory (leftover from
  a past mistake).
- PyQt6, PyQt6-Qt6, PyQt6-sip — no longer installed or referenced anywhere
  in the app.
- The "Automatic" detection mode (superseded by AI-assisted, now the
  default).
