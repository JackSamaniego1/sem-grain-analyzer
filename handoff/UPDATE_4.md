# UPDATE 4 — user change list (2026-09-29)

Status: **STORED, NOT STARTED.** Don't start until the user says go and has answered the open questions below.
The user numbered two items "8"; they are renumbered 1–12 here.

| # | Item (user's words, condensed) | Owner / model | Est. | Batch |
|---|---|---|---|---|
| 1 | New Lot / New Part: each click adds another entry box. Filled boxes keep their data. "Create" adds them all to the job and **stays on the page**. | ui-designer (sonnet) | 0.5 d | 1 |
| 2 | Drag image files from a folder into a lot's "Images in this lot" area (empty or not) to add them. | ui-designer + data-architect | 0.5 d | 1 |
| 3 | Analyzer: a checkbox on each image. When 2 or more are ticked, show an **Analyze selected** button under "Analyze current". | ui-designer | 0.5 d | 1 |
| 4 | Automatic mode: read the scale-bar label (e.g. "100 nm", "2 µm") and fill in the length and unit. Must not confuse it with WD, magnification or other text. | detection-engineer (opus) | 1.5–2 d | 2 |
| 5 | New right-sidebar card **Resolution Profiles**, disabled by default. Dropdown of saved profiles (name + pixel-to-length ratio + scan area). **New profile** auto-finds scan area and scale bar and opens the scale-bar length window; the user adjusts and saves. Picking a profile moves the image to "Not analysed" (ready). The panel under the image shows which profile is in use. With no profile, the existing "find scan area & scale bar" box shows as today. | ui-designer + data-architect (opus) | 1.5–2 d | 2 |
| 6 | Scale-length entry: units in a **separate dropdown** (nm, µm, mm). After "Find scan area & scale bar", animate/highlight the length box so users find it. | ui-designer | 0.5 d | 1 |
| 7 | Stability on weak CPUs: interacting with grains during analysis can crash the app. Profile and fix. Offer a lock during analysis with a "continue anyway" bypass and a warning about low-performance PCs. | opus, high effort | 1.5–2 d | 3 |
| 8 | Review page: **Add grain** tool with the same drawing style as the split tool. | ui-designer + detection-engineer | 1 d | 3 |
| 9 | Overlay opacity **slider** in the top-right corner of the image (Analyze and Review, Overlay tab). | ui-designer | 0.5 d | 1 |
| 10 | Remove the "AI runs on CPU" tile. Add detection modes **AI-Assisted (GPU)** and **AI-Assisted (CPU)**. | detection-engineer + build-engineer | 1 d (+ installer decision) | 3 |
| 11 | On image load, auto-detect magnification, instrument, accelerating voltage and working distance (same machinery as #4). | detection-engineer | 0.5 d (metadata) + 1 d (on-image text) | 2 |
| 12 | Optimise speed and usability; speed up grain detection without losing accuracy. **User asked for the Fable model.** | Fable (see question Q3) | 2–3 d | 3 |

| 13 | Review page image list: use the **same grouped layout as the Analyze image tree** (Job › Part › Lot folders with the images under them) instead of the current flat, disorganised list. Reuse `ui/pages/image_tree.py` (ImageTree). | ui-designer | 0.5 d | 1 |
| 14 | Analyze & Review: the display mode (Original / Overlay / …) **persists when switching images**. Today it resets to Overlay on every new image. | ui-designer | 0.25 d | 1 |
| 15 | **Lot Summary page is blank** in the report (known gap: the report editor has no preview widget for the `lot_summary` section; also verify the exported Excel/PPTX). Make lot-vs-lot charts really good: every summary chart = **bars per lot with an overlaid trendline** (combo bar+line), plus an **overall job summary** chart/table. Mock up first, like the PPTX slide 2 process. | report-engineer (opus for design) | 1.5–2 d | 2 |
| 16 | **Units & bins bug:** switching area nm² → µm² (e.g. 20,000 nm²) dumps everything into one 0–1 µm² bin, and raising the bin count does nothing. Unit change must only rescale values (move the decimal); bin edges must be recomputed from the data range in the new unit; more bins = narrower bins (smaller step), never needing huge counts. Add tests for nm²↔µm² and diameter nm↔µm with bin-count changes. | report-engineer | 0.5–1 d | 1 |
| 17 | **PPTX distribution slides:** one slide **per lot** with grain **area** distribution and grain **size (diameter)** distribution, each as bars + a smooth trendline curve; plus a **lot-to-lot comparison** slide with all lots' curves stacked on the same axes (bars + trendlines, one colour per lot). User approved the current deck style; show a sample deck after. | report-engineer | 1–1.5 d | 1 |
| 18 | **PPTX percentile slide** (asked for by the person who requested the report; do NOT use the word "requestor"): title e.g. "Grain Size Percentiles (D10 / D50 / D90)". Table per part and lot: D10, D50 (median), D90 of grain diameter (+ median area), with a one-line plain definition on the slide (D10 = 10 % of grains are smaller than this, D90 = 90 % are smaller). Number-based percentiles by default; same table style, 14-row pagination. | report-engineer | 0.5 d | 1 |
| 19 | **PPTX contents page as slide 2** (the user called it a "glossary"): lists every page/section of the deck with its page number and a **clickable link** to that slide. Runs of similar slides collapse to one line with a range and a link to the first slide, e.g. "Pages 11–30 · Image results". The Grain Size Summary moves to slide 3. Page numbers must be computed after the whole deck is laid out; paginate the contents itself if it gets long. | report-engineer | 0.5 d | 1 |

**Total ≈ 13.5–16.5 working days of agent time**, plus the user's review between batches.

## Feasibility notes (answered to the user)
- **Metadata already exists:** `core/sem_metadata.py` reads Zeiss, FEI/Thermo, TESCAN, Hitachi, JEOL and ImageJ metadata (pixel size, magnification, HV, WD, detector) from original TIFFs and sidecar files, and `ui/app_state.py` already uses it for calibration. For original TIFFs, #11 is mostly wiring to fill the fields.
- **Exported JPG/PNG/screenshots contain no metadata**, so they need text recognition on the info bar. No OCR engine is bundled today (`core/scale_bar.py`: pytesseract hook, never bundled). Plan: bundle an offline OCR engine, either Tesseract (Apache-2.0) or RapidOCR (ONNX, Apache-2.0, + onnxruntime MIT), about 15–40 MB. It runs only on the info-bar strip.
- **Avoiding confusion with WD etc.:** use the **label** tokens ("WD", "Mag", "HV/kV", "Det") to classify the fields, and take the scale value from the text **nearest the detected bar**. Accept only nm/µm/mm units with sane values. Cross-check against metadata when both exist. If unsure, fill it in but flag it for the user to confirm.
- **GPU:** `core/grain_detector.py:1200` already uses CUDA if torch sees it, but the installer ships **CPU-only torch** (`build.yml`). GPU needs an NVIDIA card plus a CUDA build of torch, which adds about 2.5 GB to the installer. Alternative: DirectML (any GPU, less proven with SAM).

## Suggested batches
1. **Quick UX wins (~3 d):** 1, 2, 3, 6, 9, 13, 14, 16, and remove the CPU tile.
2. **Calibration automation (~4–5 d):** 11 → 4 → 5, 15 (profiles build on auto scale/scan).
3. **Performance & tools (~5–6 d):** 7 + 12 together (same profiling work), 8, 10 GPU.

## User answers (2026-09-29)
- A1. SEMs: **JEOL and Thermo Fisher**. Both are already supported by `core/sem_metadata.py`: JEOL through the `<stem>.txt` sidecar (must sit next to the image), Thermo through TIFF tag 34682/34680. The user will supply real images for testing; OCR tuning waits for them.
- A2. The work PC **has a graphics card**; a bigger installer is fine. *To confirm when starting #10: NVIDIA, since CUDA needs it (if Intel/AMD, use DirectML).*
- A3. **Fable is allowed ONE time, for #12 only** (optimisation + clean-up). The "never Fable agents" rule stays in force otherwise.
- A4. Dragged-in images are **copied into the job folder** (into the lot's folder inside the job).
- A5. Resolution profiles are **always picked by hand**; no auto-suggest.
- #7 lock: not asked in chat. Default: always lock grain editing during analysis, with "Continue anyway" + "don't warn again".

## Open questions for the user (original)
- Q1. SEM brand and file type: original TIFFs straight from the SEM, or exported JPG/PNG? Can the user supply 2–3 real images (also D-13) to tune/test text reading?
- Q2. Does the work PC have an **NVIDIA** GPU? Is an installer about 2.5 GB larger (CUDA) OK on the flash drive? If there's no GPU, grey out the GPU mode.
- Q3. The standing rule is "never run agents on Fable" (Fable plans, Opus coordinates). Is item 12 an exception (Fable implements), or should Fable profile and plan while Opus implements?
- Q4. #2: should dropped images be **copied** into the lot folder (suggested) or only linked?
- Q5. #7: lock during analysis **always**, or only on low-spec PCs (auto-detected cores/RAM)? Suggest: always lock grain editing during analysis, with "Continue anyway" + "don't warn again".
- Q6. #5: is a profile tied to instrument + magnification (auto-suggest a matching profile), or chosen purely by hand?

## Execution checklist (coordinator keeps this current; user gets a copy after every finished item)
Rules: Fable coordinates only and implements nothing except #12 (one-time exception, last). Every item = agent on its pinned model → tests → code-reviewer → commit → `/save-handoff` → checklist to user.

- [ ] 1  New Lot/Part: multiple boxes, stay on page, keep data
- [ ] 2  Drag images from a folder into a lot (copied into the job folder)
- [ ] 3  Image checkboxes + "Analyze selected"
- [ ] 6  Unit dropdown for scale length + highlight the length box after auto-find
- [ ] 9  Overlay opacity slider (top-right of image, Analyze + Review)
- [ ] 13 Review image list grouped Job › Part › Lot like Analyze
- [ ] 14 Display mode (Original/Overlay) persists across images
- [ ] 16 Units & bins bug (nm² → µm² rescales; more bins = narrower bins)
- [ ] 18 PPTX percentile slide: D10 / D50 (median) / D90 per part and lot
- [ ] 17 PPTX distribution slides: per-lot area + size bars with trendline; lot-to-lot stacked comparison
- [ ] 19 PPTX contents page as slide 2: every section with page numbers/ranges and links (do after 17 + 18)
- [ ] 10a Remove the "AI runs on CPU" tile
- [ ] 11 Auto-fill magnification / instrument / kV / WD from JEOL + Thermo metadata
- [ ] 4  Read scale-bar label (value + unit) with bundled offline OCR — needs user's test images
- [ ] 5  Resolution Profiles sidebar card (manual selection)
- [ ] 15 Lot Summary page: fix blank preview; mock-ups → bar+trendline lot charts + job summary
- [ ] 7  Stability during analysis on weak CPUs; lock with "Continue anyway"
- [ ] 8  Review: "Add grain" tool (split-tool drawing style)
- [ ] 10b AI-Assisted (GPU) / (CPU) modes + CUDA installer
- [ ] 12 Fable: speed & usability optimisation, faster detection without accuracy loss — LAST
