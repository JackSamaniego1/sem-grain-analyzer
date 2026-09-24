# Codebase Analysis — SEM Grain Analyzer v2.3 (as of commit 8565392)

Written for: engineers/agents who have not seen this repo. All line numbers refer to the v2.3 files on `main`.

## Layout
| Path | Lines | Role |
|------|------:|------|
| `main.py` | 60 | QApplication, dark theme, splash, `MainWindow` |
| `core/grain_detector.py` | 994 | Detection engine: 3 pipelines, measurement, stats, overlay |
| `core/scale_bar.py` | 154 | Auto scale-bar detection (unused by UI; OCR needs pytesseract) |
| `ui/main_window.py` | 716 | `AnalysisWorker` (QThread), `ImageTab`, `MainWindow` (menus, batch loop, export) |
| `ui/settings_panel.py` | 493 | Left panel: open/calibrate/scan-area buttons, mode combo, collapsible params |
| `ui/results_panel.py` | 470 | Stat cards, custom QPainter histograms (area/diameter), grain table, text stats |
| `ui/image_canvas.py` | 166 | Zoom/pan canvas, click-select grain, Delete key |
| `ui/calibration_dialog.py` | 336 | 2-point scale calibration with zoom-about-cursor |
| `ui/scan_area_dialog.py` | 276 | Rectangle draw for analysis region |
| `ui/analysis_progress_dialog.py` | 205 | Batch progress with ETA |
| `ui/theme.py` | 90 | Palette + one big QSS string |
| `utils/excel_export.py` | 368 | openpyxl workbook: Overview, per-image Summary+Data sheets |
| `grain_analyzer.spec`, `BUILD_WINDOWS.bat`, `BUILD_MAC.sh`, `create_nsis_script.py`, `.github/workflows/build.yml` | — | Packaging & CI |
| `docs/` | — | GitBook user + technical docs (v2.3) |
| `tests/` | new | `conftest.py` synthetic mosaic generator; `test_black_regions.py` (fails by design) |

No persistence layer, no settings storage, no tests before this handoff.

## Runtime data flow
1. `MainWindow.open_images` → `cv2.imread` → `ImageTab(image_bgr, path)` per file (all images in RAM).
2. Calibration (`_px_per_um`) and scan rectangle (`_scan_rect`) are **global** to the window and applied to every image.
3. `run_analysis_all` → sequential `AnalysisWorker` per tab on a `QThread`; worker crops to scan rect, calls `GrainDetector.analyze`, discards border-touching grains, pads results back to full-frame coordinates, emits `AnalysisResult`.
4. `ImageTab.set_result` → overlay view; `ResultsPanel.display_results` → cards/histograms/table.
5. Grain deletion mutates `result.grains`/`label_image`, recomputes stats in `ImageTab._recompute_stats` (a duplicate of `GrainDetector._compute_statistics`), redraws overlay.
6. Export → `utils.excel_export.export_multi_to_excel([(result, path, bgr)…])`.

## Detection engine (`core/grain_detector.py`)
`analyze()` (107) → `_auto_crop` (162: only crops **white** borders, >240) → mode select (`auto` → `_auto_detect_mode` 199; UI never sends `auto`) → one of:
- **Boundary** (`_boundary_pipeline` 232): measures texture ratio + groove strength → `_groove_boundary_pipeline` (261) or `_mosaic_boundary_pipeline` (335). Builds a boundary-probability map from DoG/LoG/dark-valley/step/gradient/black-top-hat, seeds `peak_local_max` on `interior = 1 - boosted` (298/417), `watershed` on the boosted map (314/433), size filter, mosaic mode adds `_texture_split` (504) using structure-tensor orientation. **No intensity gate anywhere.**
- **Threshold** (`_threshold_pipeline` 564): CLAHE → blur → Otsu (+offset) → optional adaptive → morphology → `remove_small_objects` → distance-transform watershed. `dark_grains` inverts.
- **SAM + ASTM** (`_sam_astm_pipeline` 649): loads `models/sam_vit_b_01ec64.pth`, downsizes to 1024 (CPU), `SamAutomaticMaskGenerator(points_per_side=32/64, pred_iou_thresh=0.80, stability_score_thresh=0.88)`, filters by area/`predicted_iou`/overlap only (762–783), then `_astm_e112_refine` (801) splits oversized regions using intercept-derived expected area. The intercept count is computed but the **G-number is never reported**.
Then `_measure_grains` (882: regionprops → `GrainResult`; **never sees the gray image**), `_compute_statistics` (928: coverage = Σarea / whole-frame area), `_draw_overlay` (953).

### Root cause of "black regions detected as grains" (R1)
- Boundary pipelines: a uniform black region has zero contrast → `boosted≈0` → `interior≈1` → dense seeds → watershed floods it → survives size filter. Reproduced: **217 false grains** on the synthetic fixture.
- Threshold with `dark_grains=True`: black = foreground → giant grain.
- SAM: uniform regions get high stability scores; no mean-intensity/std filter.
- Nothing downstream can catch it because measurement/stats have no access to intensities.
**Fix direction (DET-01/02):** compute a `valid_mask = gray > t_black` (t_black ≈ 8–15 on uint8, then `cv2.morphologyEx(OPEN)` + small erosion), pass it as `mask=` to every watershed, exclude seeds outside it, post-filter SAM masks by mean intensity / std-dev / valid-fraction, filter regions in `_measure_grains` by mean intensity, and compute coverage/total area over `valid_mask.sum()` only. Expose `DetectionParams.invalid_intensity_threshold` in the UI.

## Confirmed defects and debt
| # | Where | Issue |
|---|-------|-------|
| B1 | detector (above) | Black regions become grains — R1 |
| B2 | `ui/main_window.py:206` | `binary_image * 255` on an already-0/255 uint8 array overflows → "Binary Mask" view renders nearly black |
| B3 | `grain_detector.py:942-946`; `main_window.py:62-80` | Coverage % divides by full frame (incl. black/legend/scan-border-discarded area) |
| B4 | `main_window.py:175-193` vs `grain_detector.py:928` | Duplicate statistics code; drift risk |
| B5 | `main.py`, `main_window.py:223,692`, `settings_panel.py:120`, spec, NSIS | Version "2.3" hard-coded in 5+ places |
| B6 | `.github/workflows/build.ymlresources/.gitkeep` | Stray directory from a bad path |
| B7 | `main_window.py:227-228` | Calibration and scan rect are global; per-image override exists only as an unused `ImageTab.scan_rect` |
| B8 | `utils/excel_export.py` | Sheets interleaved Summary/Data; no per-image table on Overview; no tab colours; openpyxl chart styling limits |
| B9 | `grain_detector.py:162-197` | Auto-crop handles only white borders; black SEM info bars require manual scan area |
| B10 | `settings_panel.py:417` | Reset selects `sam_astm`, while `DetectionParams.detection_mode` default is `auto`; `auto` not offered in the combo |
| B11 | `main_window.py:395-404` | Closing a tab during batch analysis desyncs index-based `_pending_indices` |
| B12 | `image_canvas.py:119-123` | Main canvas zooms about centre, dialogs zoom about cursor (inconsistent) |
| B13 | `core/scale_bar.py` | Auto-detection implemented but never wired to the UI; OCR dependency not bundled |
| B14 | whole app | No persistence: no QSettings, no recent files, no saved sessions, results lost on close |
| B15 | `_astm_e112_refine` | Intercept length computed, ASTM G never exposed |
| B16 | `results_panel.py:307,318` | `QSpinBox.setValue(0)` below `setRange(3,100)` minimum — silently clamps |

## What is good and must be preserved
- The two-pass boundary design (contrast first, texture only adds splits) — keep the philosophy when adding the valid mask.
- Worker-thread pattern with progress signal; the SAM progress monkey-patch (727–738) is clever and users like the feedback.
- Whole-number histogram bins from 0 and nm/µm auto-unit scaling (`smart_unit`, `_unit`) — users see readable numbers.
- Collapsible parameter sections hidden until first analysis (progressive disclosure).
- Batch progress dialog with ETA.
- Border-touching grain discard when a scan area is set.

## Dependency snapshot (installed in `.venv`)
PyQt6 6.11.0, opencv-python 5.0.0, scikit-image 0.26.0, scipy 1.17.1, numpy 2.4.6, torch 2.14.0, torchvision 0.29.0, segment-anything 1.0, openpyxl 3.1.5, Pillow 12.3.0, pytest, pytest-qt. Planned additions: xlsxwriter, python-pptx, qtawesome.
