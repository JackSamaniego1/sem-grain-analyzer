# Ideas Backlog (owned by the `innovator` agent)

Last innovator pass: **2026-09-23 (INN-run #1)** — re-scored all 25 seeds, re-scoped network ideas to local-only (D-14), merged overlaps, added 15 new ideas (INN-26…INN-40), archived 15. Specs written for the top 5 (see "Spec'd" below).

**Score** = impact (1–5) × confidence (0–1) ÷ effort (S=1, M=2, L=3). **Spec picks** weigh score *plus* how many other ideas an item unlocks (e.g. INN-26 feeds INN-27 → INN-02 → INN-40), and may only depend on Phase 1–3 (DET / DATA / REP) infrastructure.

Hard rules applied to every idea: works with no network after install, no data leaves the PC, no update checks/telemetry/cloud, PySide6 + permissive deps only, no Qt in `core/`, `data/`, `reports/`.

## Competitive scan (what the market does; how we beat it)
| Product | What it does well (cited) | Our angle |
|---|---|---|
| Keyence VHX | One-click grain number per ASTM E112/E1382; choice of intersection count vs. grain count methods ([Keyence](https://www.keyence.com/products/microscope/digital-microscope/industries/automotive/grain-size.jsp)); album/folder-per-sample workflow | Same one-click G, **plus** CI/%RA and pass/fail verdict per lot; folder hierarchy Project›Sample›Lot (D-04) |
| Zeiss ZEN core | Intercept, planimetric, comparison-chart and AI (Intellesis) methods; supervisor-defined job templates run by operators; GxP data storage ([Zeiss](https://knowledge.zeiss.com/rms/en/zen-core/toolkits-modules/application-and-workflow-toolkits/grain-size-analysis)) | Material presets + SOP checklists (INN-14/28) give the supervisor/operator split without a server |
| Evident PRECiV / Stream | Intercept with circle/cross/line patterns + planimetric, auto G and mean intercept; guided workflows to ISO/ASTM/JIS reports ([Evident](https://evidentscientific.com/en/applications/grain-size-analysis)) | Pattern overlays drawn on the evidence image (INN-26/31) so the customer *sees* the count |
| Clemex | E112/E930/E1382; outputs G, D10/D50/D90, **relative accuracy and confidence intervals**; twin-vs-grain boundary separation; audit-ready PDF/Excel ([Clemex](https://www.clemex.com/applications/grain-cell-size)) | Match CI/%RA (INN-27), D-values (INN-37), ALA/duplex flags (INN-33); beat on price (free) and on offline privacy |
| MIPAR | Recipe-based pipelines, batch over data sets, deep-learning option ([MIPAR](https://www.manula.com/manuals/mipar/user-manual/v4.5/en/topic/intercepts)) | Presets = recipes (INN-14); SAM already bundled |
| ImageJ/Fiji | Free, scriptable (MorphoLibJ), but manual intercept macros and no data management ([image.sc](https://forum.image.sc/t/intercept-count-method-macro-for-grain-size-analysis/106758)) | Everything Fiji users glue together, in one audited workflow |

Standards to anchor on: ASTM E112 (planimetric/intercept, ≥5 fields, 95 % CI, %RA ≤ 10 % typical target — [PACE summary](https://www.metallographic.com/tools/grain-size-calculator)), E1382 (automatic image analysis), E930 (ALA largest grain), E1181 (duplex), ASTM E766 / ISO 16700 (SEM magnification calibration), ISO/IEC 17025 §6.4–6.5 & §7.8 (traceability, reporting), ILAC-G8 (decision rules for conformity statements).

## Active top 25 (ranked)
| Rank | ID | Idea | User story | Why it matters in a corporate lab | Effort | I × C | Score | Depends on | Risk | Acceptance |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | INN-27 | **Sample statistics & E112 uncertainty** — every image is a field; lot/sample result = mean G ± 95 % CI, %RA, field count vs required | As a technician I see "G 7.4 ± 0.2 (95 % CI), %RA 3.1 %, 6/5 fields ✔" for the lot, not six unrelated numbers | E112 demands multi-field CIs; Clemex sells this; customers challenge single-field numbers | S | 5 × 0.9 | **4.5** | DET-04, DATA-03, REP-01 | Low | Spec `specs/INN-27.md` |
| 2 | INN-02 | **Spec limits & ILAC-G8 verdict** — per project/material spec (G range, max ALA, max %RA, min fields); verdict PASS / FAIL / INCONCLUSIVE when CI straddles a limit | As QA I set "ASTM G 6.0–8.0" once and every lot shows a green/red/amber badge in app, XLSX and PPTX | Turns measurement into a decision; decision rule stated = ISO 17025 §7.8.6 | S | 5 × 0.9 | **4.5** | INN-27, DATA-01, REP-01 | Low | Spec `specs/INN-02.md` |
| 3 | INN-40 | **Lot certificate (CoA-style one-pager)** — single page per lot: sample/lot/spec, G ± CI, verdict, calibration status, approval block, evidence thumbnail | As a lab manager I hand the customer one sheet, not a 12-tab workbook | Customer-facing deliverable; replaces hand-typed Word certificates | S | 5 × 0.9 | 4.5 | INN-02, INN-27, INN-30, REP-03/04 | Low | First XLSX sheet "Certificate" + PPTX slide 1; fields asserted in tests |
| 4 | INN-08 | **Methods statement auto-generated** — deepened: prose built from `DetectionParams`, mode, calibration source, E112 method, fields, software version, decision rule | Customer asks "how was this measured?" — it is already in the report | Required content of ISO 17025 §7.8.2 reports | S | 4 × 1.0 | 4.0 | REP-01, DET-04 | Low | Changing any param changes the text; snapshot test per mode |
| 5 | INN-29 | **Calibration verification against a reference standard** — measure a certified grating/micrometer image, log error %, expiry; reports stamp "verified" or amber "not verified" | As a technician I verify the SEM scale weekly in 30 s and the report proves it | Metrological traceability (17025 §6.5); ASTM E766 / ISO 16700 | S | 5 × 0.8 | 4.0 | DET-07, DATA-01, DATA-04, REP-01 | Med (pitch detection on noisy images) | Spec `specs/INN-29.md` |
| 6 | INN-31 | **Evidence images** — export overlay PNG/TIFF with burned-in scale bar, data bar (sample · lot · mag · operator · date · G) and optional intercept pattern | As an engineer I paste a self-describing micrograph into any email or slide | Images get separated from reports; a data bar keeps them traceable | S | 4 × 1.0 | 4.0 | DET-02, REP-02 | Low | Rendered bar length matches µm within 1 px; used by PPTX |
| 7 | INN-32 | **Exclusion brush / ROI mask** — paint out scratches, pores, debris, info bars; mask ANDs into DET-01 valid mask and area | As a technician I remove a scratch in 3 s instead of re-cropping | Honest statistics; auditors see what was excluded (area % logged) | S | 4 × 0.9 | 3.6 | DET-01, DATA-02, UI-05 | Low | Excluded pixels never in any grain; `invalid_area_pct` rises accordingly |
| 8 | INN-14 | **Material method presets** — deepened: preset = params + E112 method + required fields + spec link; supervisor can lock a preset | As a supervisor I publish "Ni-718 etched" and operators cannot drift | Zeiss-style supervisor/operator split without a server | S | 4 × 0.9 | 3.6 | DATA-04, UI-04 | Low | Locked preset greys params; preset name + hash in methods statement |
| 9 | INN-06 | **Trend & lot-comparison charts** (absorbs INN-01) — G over time per sample with spec band + control limits; pick 2–n lots → overlaid distributions, KS / Welch-t p-values | As QA I spot a drifting supplier before it fails | SPC and supplier disputes | S | 4 × 0.9 | 3.6 | DATA-03, INN-27, REP-02 | Low | Trend + compare views; charts in XLSX/PPTX; p-values match scipy |
| 10 | INN-38 | **Customer deliverable pack** (absorbs INN-20 CSV) — one "Export pack" folder: PDF, XLSX, PPTX, evidence images, flat CSV for LIMS import, `SHA256SUMS.txt`, readme | As a lab manager I drag one folder onto the customer share | One click, nothing forgotten, tamper-evident | S | 4 × 0.9 | 3.6 | REP-03/04, INN-09 | Low | Folder contents + checksums verified in test |
| 11 | INN-09 | **PDF report** — rendered locally (reportlab BSD in `reports/`, or QPdfWriter from `ui/`); no Office needed | As an engineer I send a read-only PDF | Universal, read-only deliverable | S | 4 × 0.8 | 3.2 | REP-01 | Low (licence check) | PDF opens; page count & text asserted |
| 12 | INN-39 | **Live adequacy meter** — ring gauge on Review page: grains counted, fields vs required, live %RA; turns green when E112 adequacy met | As an operator I know when I have imaged enough fields | Stops under-sampling at the microscope, not after | S | 4 × 0.8 | 3.2 | INN-27, UI-06 | Low | Gauge state matches `sample_statistics()` |
| 13 | INN-21 | **Grain-size classification bands** — colour grains by band (fine/medium/coarse or by G) with legend; band % in reports | As an engineer I show a manager where the coarse grains are | Visual storytelling for PPTX | S | 3 × 1.0 | 3.0 | REP-02 | Low | Overlay mode + band chart |
| 14 | INN-37 | **Distribution descriptors** — D10/D50/D90, log-normal fit (µ, σ), skew; bimodality coefficient | As a researcher I compare distributions, not just means | Clemex parity; better supplier comparisons | S | 3 × 1.0 | 3.0 | DET-03 | Low | Values match numpy/scipy reference |
| 15 | INN-33 | **Duplex & ALA flags (E1181 / E930)** — largest grain as ALA G; bimodality → "possible duplex" warning in report | As QA I catch abnormal grain growth automatically | Aerospace specs frequently call out ALA | S | 4 × 0.7 | 2.8 | INN-37, DET-04 | Med (false flags) | Synthetic duplex mosaic flagged; uniform one not |
| 16 | INN-10 | **Report templates & branding** — saved section sets, logo, colours, footer text; templates stored locally | As a lab manager every operator's report looks the same | Consistent reports across operators | S | 3 × 0.9 | 2.7 | REP-01, REP-04 | Low | Template round-trip; logo on cover |
| 17 | INN-15 | **Duplicate-image guard** — perceptual hash on import; warn if already analysed anywhere in workspace | As a technician I am warned before counting the same field twice | Stops double-counting a field | S | 3 × 0.9 | 2.7 | DATA-03 | Low | Same image in two lots → warning |
| 18 | INN-16 | **Workspace backup & restore** — zip a project (files are source of truth), restore + catalog rebuild | As IT-support I move a project to a new PC in one file | Lab PCs get reimaged | S | 3 × 0.9 | 2.7 | DATA-01, DATA-03 | Low | Round-trip archive test |
| 19 | INN-22 | **Histogram brushing** — select bins → grains highlight on canvas and table | As an engineer I click the tail of the histogram and see those grains | Instant outlier investigation | S | 3 × 0.9 | 2.7 | UI-06 | Low | Linked selection test |
| 20 | INN-25 | **Local health panel** — checks SAM checkpoint hash, GPU/CPU, workspace writable, disk space, catalog integrity, offline guard active; writes local crash log | As a technician I see at a glance that the install is complete and offline | Fewer support calls; proves offline guard is on | S | 3 × 0.9 | 2.7 | FND-07 | Low | Panel shows all checks; missing asset → "reinstall" text, never a URL |
| 21 | INN-36 | **Synthetic E112 comparison plate** — generate a Voronoi "plate" at the image's magnification for the measured G ± 1 and show side-by-side | As a reviewer I sanity-check the number by eye, like Plate I–IV (without copying ASTM plates) | Fast plausibility check; great slide visual | S | 3 × 0.8 | 2.4 | DET-04 | Low | Generated plate re-measures within ±0.3 G |
| 22 | INN-26 | **E112 / E1382 compliance engine** — rule-exact intercept (lines & 3-circle patterns, ½-hit rules), Jeffries planimetric with edge-grain halves, adequacy checklist | As a technician my G is the one an auditor would get by hand | Every competitor advertises it; the core of a defensible number | M | 5 × 0.9 | 2.25 (picked: unlocks 27/02/40/33/36) | DET-01…04 | Med | Spec `specs/INN-26.md` |
| 23 | INN-30 | **Report approval & sealed sign-off** — Draft → Reviewed → Approved; SHA-256 seal over report + results; revisions supersede; verify button | As a reviewer I approve a lot and anyone can later check it was not edited | ISO 17025 §7.5/§7.8 record integrity; replaces paper sign-off | M | 5 × 0.9 | 2.25 (picked: unlocks 40/38) | DATA-02, REP-01, REP-06 | Med | Spec `specs/INN-30.md` |
| 24 | INN-05 | **Auto-calibration from SEM metadata** — deepened: Zeiss `CZ_SEM` tag 34118, FEI/Thermo tags 34680/34682, Hitachi/JEOL `.txt` sidecars, Tescan header; also auto-detect & crop the black data bar | As a technician I never type a pixel size again | Removes the #1 manual error source | M | 5 × 0.9 | 2.25 | DET-05, DET-07 | Med (vendor variants) | ≥4 vendor formats parsed from synthetic TIFFs; fallback manual |
| 25 | INN-07 | **Audit trail** — append-only `audit.jsonl` per session: param changes, grain edits, exclusions, re-analysis, exports, with operator + time; shown in review | As an auditor I see who changed what and when | Traceability for ISO 9001/17025; prerequisite for strong INN-30 | M | 5 × 0.9 | 2.25 | DATA-02, DATA-08 | Low | Every mutating action writes one line; viewer lists them |

### Spec'd this pass (Phase 1–3 dependencies only)
INN-27, INN-02, INN-26, INN-29, INN-30 → `handoff/specs/INN-nn.md`. Next in line: INN-08, INN-31, INN-40.

### Re-scoping notes (D-14)
- INN-20 "LIMS / watched folder" → **local CSV drop folder** only (folded into INN-38); no network share polling assumptions, no LIMS API.
- INN-25 "self-check" → local-only checks; missing asset message says "reinstall", never links.
- INN-09 PDF → no cloud converter, no dependency on installed Office.
- Ops seeds "auto-update check" and "crash reporting" are **rejected**: replaced by About page (version + "get installers from your IT/GitHub Release on another PC") and a local crash log (INN-25).

## Archive
| ID | Idea | Why archived |
|---|---|---|
| INN-01 | Lot-to-lot comparison dashboard | Merged into INN-06 |
| INN-03 | Confidence per grain + review queue | 4 × 0.6 ÷ M = 1.2; revisit after SAM post-filter (DET-01) exposes a usable score |
| INN-04 | Manual draw/split/merge | Absorbed by task UI-05 |
| INN-11 | Side-by-side compare | 2.4; partly covered by INN-36 and UI-05 minimap |
| INN-12 | Session diff | 1.05; audit trail (INN-07) covers most needs |
| INN-13 | Phase fraction / porosity / inclusions | 0.8 (L); strong v3.1 candidate once the valid-mask work settles |
| INN-17 | GPU/CPU awareness + ETA | 1.6; device chip can ride with INN-25 |
| INN-18 | Command palette | Absorbed by UI-07 shortcut overlay |
| INN-19 | Presentation/kiosk mode | 1.8 |
| INN-20 | LIMS/CSV watched folder | Re-scoped to local CSV, folded into INN-38 |
| INN-23 | Attachments per sample | 2.4; small, pick up opportunistically with DATA-06 |
| INN-24 | Localisation + units | 1.8 |
| INN-28 | SOP workflow checklists (supervisor-defined steps: calibrate → verify → ≥5 fields → review → approve) | 1.6 (M); INN-14 locked presets + INN-39 adequacy meter deliver 80 % |
| INN-34 | Twin-boundary handling (straight-segment detector; exclude twins per E112) | 0.67 (L, low confidence on SEM contrast) |
| INN-35 | Method validation pack (golden set, repeatability, Gage R&R-lite) | 1.6; needs real images (D-13) |
