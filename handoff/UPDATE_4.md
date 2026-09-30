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

**Total ≈ 13–16 working days of agent time**, plus the user's review between batches.

## Feasibility notes (answered to the user)
- **Metadata already exists:** `core/sem_metadata.py` reads Zeiss, FEI/Thermo, TESCAN, Hitachi, JEOL and ImageJ metadata (pixel size, magnification, HV, WD, detector) from original TIFFs and sidecar files, and `ui/app_state.py` already uses it for calibration. For original TIFFs, #11 is mostly wiring to fill the fields.
- **Exported JPG/PNG/screenshots contain no metadata**, so they need text recognition on the info bar. No OCR engine is bundled today (`core/scale_bar.py`: pytesseract hook, never bundled). Plan: bundle an offline OCR engine, either Tesseract (Apache-2.0) or RapidOCR (ONNX, Apache-2.0, + onnxruntime MIT), about 15–40 MB. It runs only on the info-bar strip.
- **Avoiding confusion with WD etc.:** use the **label** tokens ("WD", "Mag", "HV/kV", "Det") to classify the fields, and take the scale value from the text **nearest the detected bar**. Accept only nm/µm/mm units with sane values. Cross-check against metadata when both exist. If unsure, fill it in but flag it for the user to confirm.
- **GPU:** `core/grain_detector.py:1200` already uses CUDA if torch sees it, but the installer ships **CPU-only torch** (`build.yml`). GPU needs an NVIDIA card plus a CUDA build of torch, which adds about 2.5 GB to the installer. Alternative: DirectML (any GPU, less proven with SAM).

## Suggested batches
1. **Quick UX wins (~2.5 d):** 1, 2, 3, 6, 9, and remove the CPU tile.
2. **Calibration automation (~4–5 d):** 11 → 4 → 5 (profiles build on auto scale/scan).
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
