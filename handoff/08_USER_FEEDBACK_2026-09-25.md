# User feedback after first install of v3.0.0 (2026-09-25)

Release is ON HOLD until these ship. v3.0.0 tag is local only (not pushed) → re-tag after fixes (`git tag -f v3.0.0`, re-ff main), rebuild installer.

IDs UX-01..UX-16. Owner in brackets. Status tracked in 03_TASK_BOARD.md.

## Analyze page (ui-designer)
- **UX-01 Settings panel order** (right side): 1 Detection mode → 2 Calibration → 3 Scale → 4 Grain filters → 5 Excluded black regions → 6 Advanced parameters. Remove the "Automatic" detection option; "AI-assisted" is first and the default. Hide the Advanced parameters section entirely while AI-assisted is selected.
- **UX-02 Pre-analysis gate.** Analysis must not start until scan area (info-bar / valid region) AND magnification/scale are confirmed for every image being analysed. Provide one button, e.g. "Auto-find scan area & scale bar (all images)", that auto-detects both on all images. Show the result in a tile below the image (scan area, scale bar length → µm/px, source: auto/metadata/manual) with a way to edit. If the user clicks Analyze before confirming: highlight the relevant controls exactly like the guided tour does, with text: "This must be set. Check the scan regions and magnifications on each image and edit any that are wrong before starting analysis."
- **UX-03 Scale bar "this image only".** When setting the scale bar, option to apply it to only the current image (vs all images in the analyzer).
- **UX-04 Grain filters button text.** When the "This image" scope is selected, the confirm button says "Apply" (not "Apply to all images").
- **UX-05 Overlay opacity slider** in the analysis settings. The same value is used for the overlay in report exports (ReportModel field `overlay_opacity`, 0–1; see UX-12).
- **UX-06 Remove image from analyzer** (NOT from the lot; files and lot untouched) + an "Add all from lot" button to put removed images back.
- **UX-07 Cancel is slow.** Cancel during analysis must stop within about 1 s. [detection-engineer: cooperative cancel checks inside detector loops / SAM batches; worker honours them]
- **UX-08 Spinner bug.** Clicking "Analyze current" starts the "Analyze all" button's spinner; only the button that was clicked should spin.
- **UX-09 (REVISED 2026-09-25, very important) Load job / part / lot into analyzer.** Selecting a job (project), part (sample) or lot on Projects shows a "Load into analyzer" button next to "Move to" and "Delete" in the selection bar. It loads EVERY image under that node. The analyzer's image list is a nested tree mirroring the folders (Job > Part > Lot > images) with collapsible group nodes showing counts and status. Analyze-all covers everything loaded. The results table has Job/Part/Lot columns and can be grouped or sorted by any level, or shown all together. Loading must be lazy and off-thread for hundreds of images. Supersedes the original text below.
- ~~UX-09 Multi-lot analysis.~~ (original) On Projects, when a part number (sample) is selected, a button on the right, "Analyze all lots", opens every lot's images in the analyzer at once. The Analyze page results table has a Lot column and can be grouped/sorted by lot or shown all together. From the Analyze page, export a single report covering several lots (UX-13).

## Shell (ui-designer)
- **UX-10 Nav tooltip delay.** Left-nav hover labels (e.g. "Analyze") appear almost instantly (≤150 ms), or show labels permanently / on rail expand.
- **UX-11 CPU chip clarity.** The bottom-right chip should read like "AI runs on: CPU" (or GPU name) with a helpful tooltip.

## Reports (report-engineer)
- **UX-12 Report editor Images tab freezes.** Clicking Images is very slow and once made the app stop responding. Load thumbnails lazily and off the GUI thread, cache them, never block the UI.
- **UX-13 Multi-lot report format.** New report style for several lots analysed together: overview with a per-lot summary table (lot, fields, grains, G ± CI, mean ECD, verdict if spec is set), lot comparison (reuse core/lot_compare: ΔG matrix + equivalence vs baseline when set), per-lot sections, combined distribution overlay, and raw data with a Lot column. Excel + PPTX. Single-lot reports keep working.
- **UX-14 Editable charts.** On summary charts, the user can edit bin/bucket size, units (µm/nm, ECD/area/G…), axis ranges, titles, labels, show/hide normal fit, colours, and anything else reasonable. Include "Save as my default", persisted locally in app settings and applied to new reports.
- **UX-15 Custom palettes.** The user creates a palette from 3 colours, picked by hex or colour wheel (QColorDialog). It is saved locally, selectable next to the 4 built-in palettes, and drives the report colours.
- **UX-16 Overlay opacity in export.** Report overlay images use `overlay_opacity` (UX-05).
