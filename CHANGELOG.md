# Changelog

All notable changes to Grain Analyzer are documented here.

## [3.0.0] — 2026-09-24

The v3.0 release is a ground-up rebuild of the app: correct grain detection
on images with black/invalid regions, a real project/sample/lot data
library you can browse and reopen, an editable report model behind much
better Excel and PowerPoint exports, a full visual redesign, a lot
comparison tool, and a hardened offline/private guarantee. 686 automated
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

### Fixed

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
