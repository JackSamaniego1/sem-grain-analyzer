# Ideas Backlog (owned by the `innovator` agent)

Score = impact (1–5) × confidence (0–1) ÷ effort (S=1, M=2, L=3). Innovator re-ranks each session and keeps the top 25 active. Seeded by the planning session; the innovator must deepen these and add its own.

| ID | Idea | User story | Why it matters in a corporate lab | Effort | Score | Depends on | Acceptance |
|----|------|------------|------------------------------------|--------|-------|------------|------------|
| INN-01 | **Lot-to-lot comparison dashboard** | As a QA engineer I select two or more lots and see side-by-side distributions and a t-test/KS p-value | Incoming-material acceptance, supplier disputes | M | 2.0 | DATA-03 | Compare view with overlaid histograms + stats table + export |
| INN-02 | **Spec limits & pass/fail** | I set min/max mean grain size (or ASTM G range) per project; sessions show a green/red badge | Turns analysis into a decision; audit-ready | S | 4.0 | DATA-01, DET-04 | Badge + reason in report cover |
| INN-03 | **Confidence per grain + review queue** | Grains with low boundary confidence are listed for one-click accept/reject | Cuts review time; defensible results | M | 2.0 | DET-01 | Queue UI; confidence column in raw data |
| INN-04 | **Manual grain tools: draw / split / merge** | I fix a mis-segmented grain with a brush or a split line, with undo | Every lab tool has this; currently delete-only | M | 2.25 | UI-05 | Tools + undo/redo + tests |
| INN-05 | **Auto-calibration from SEM TIFF metadata** | Opening a Zeiss/JEOL/FEI/Hitachi TIFF reads pixel size automatically | Removes the #1 manual step | M | 2.25 | — | ≥3 vendor tag formats parsed; fallback to manual |
| INN-06 | **Trend over time per sample/project** | Chart of mean size / G-number across sessions with dates | SPC-style monitoring | S | 3.0 | DATA-03 | Trend chart on project page + in PPTX |
| INN-07 | **Audit trail & operator sign-off** | Every edit (delete/merge/param change) is logged with operator and time; session can be "approved" | ISO 17025 / 9001 traceability | M | 2.0 | DATA-02 | `audit.jsonl`; approval lock; shown in report |
| INN-08 | **Methods statement auto-generated** | Report includes a paragraph describing the exact algorithm + parameters used | Customers ask "how was this measured?" | S | 3.0 | REP-01 | Parameters → prose; in XLSX/PPTX |
| INN-09 | **One-click PDF report** | Export a PDF alongside XLSX/PPTX | Email-able, read-only deliverable | S | 2.5 | REP-04 | QPdfWriter or pptx→pdf path |
| INN-10 | **Report templates library + branding** | Save a report configuration as a template; company logo/colours | Consistency across operators | S | 3.0 | REP-01 | Templates page; logo on cover |
| INN-11 | **Side-by-side image compare** | View two images (or original vs overlay) with synced zoom/pan | Standard in microscope software | S | 2.5 | UI-05 | Split view with sync toggle |
| INN-12 | **Session diff ("what changed")** | Compare two sessions of the same sample: params and result deltas | Reproducibility checks | M | 1.5 | DATA-02 | Diff view + export |
| INN-13 | **Phase fraction / porosity / inclusion modes** | Beyond grains: measure area fraction of dark phase, pore count | Broadens tool use in the lab | L | 1.3 | DET-01 | New mode + report section |
| INN-14 | **Batch presets per material** | Save detection parameter presets ("Ni-superalloy etched", "Cu polished") | Fewer wrong analyses | S | 3.5 | DATA-04 | Preset CRUD + apply |
| INN-15 | **Duplicate / near-duplicate image detection** | Warn if an image was already analysed in this project | Data hygiene | S | 2.0 | DATA-03 | Perceptual hash check on import |
| INN-16 | **Workspace backup & restore** | Zip a project with all sessions; restore on another PC | Lab PCs get reimaged | S | 2.5 | DATA-01 | Export/import project archive |
| INN-17 | **GPU/CPU awareness + ETA model** | Show device, estimate SAM time before starting, offer CPU downscale option | Sets expectations for 30–90 s runs | S | 2.0 | — | Status-bar device chip; ETA within 30 % |
| INN-18 | **Keyboard-first power mode + command palette** | `Ctrl+Shift+P` opens a command palette; every action has a shortcut | Speed for daily users | S | 2.5 | UI-03 | Palette with fuzzy search |
| INN-19 | **Presentation / kiosk mode** | Full-screen review view for showing results in a meeting | Managers review on the lab screen | S | 1.5 | UI-05 | F11 toggles; hides chrome |
| INN-20 | **LIMS/CSV export & watched folder** | Drop images in a watched folder → auto-session; CSV summary for LIMS import | Integration with lab systems | M | 1.5 | DATA-02 | Watcher + CSV schema |
| INN-21 | **Grain-size classification bands** | Colour grains by size band (fine/medium/coarse) with legend; band % in report | Visual storytelling for PPTX | S | 3.0 | REP-02 | Overlay mode + report chart |
| INN-22 | **Interactive histogram brushing** | Select a bin in the histogram → grains highlight on the canvas | Instant outlier investigation | S | 3.0 | UI-06 | Linked selection |
| INN-23 | **Sample photo & notes attachments** | Attach macro photos, PDFs, notes to a sample/lot | Complete sample record | S | 2.0 | DATA-01 | Attachments list in browser |
| INN-24 | **Localisation + unit preferences** | µm/nm/mils, decimal separators, date formats | Multinational labs | S | 1.5 | — | Settings page options |
| INN-25 | **Self-check on startup** | Verify SAM checkpoint, GPU, workspace reachable; show a health panel | Fewer support calls | S | 2.5 | — | Health panel in Settings |

## Archive
(none yet)
