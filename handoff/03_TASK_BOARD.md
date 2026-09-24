# Task Board

Status values: `todo` · `doing` · `review` · `done` · `blocked`. Keep IDs stable; add new tasks at the end of their phase. Owner = agent name.

## Phase 0 — Foundation
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| FND-01 | Create branch `v3-dev`; add `version.py` (`3.0.0-dev`) and `CHANGELOG.md`; wire version into main.py, main_window, settings_panel, spec, NSIS, README | build-engineer | todo | |
| FND-02 | Add `xlsxwriter`, `python-pptx`, `qtawesome` to requirements/build scripts/CI/spec hiddenimports (+ `collect_data_files('qtawesome')`); delete `.github/workflows/build.ymlresources/`; CPU-only torch index in build scripts | build-engineer | todo | |
| FND-03 | Get user decision on D-03 (PyQt6 licensing) and D-10/D-11 | coordinator | done | D-03 → PySide6; D-14 offline added; D-10..13 defaulted |
| FND-04 | User fixes GitHub auth; push `v3-dev` | user | blocked | creds cached for `Harvey-FS` |
| FND-05 | Baseline: `/run-tests` + `/smoke-app`; record results in progress log | qa-engineer | todo | expect 2 failing tests |
| FND-06 | Migrate PyQt6 → PySide6 across main.py + ui/; requirements, spec, build scripts, CI; LICENSE.txt + THIRD_PARTY_LICENSES.txt | build-engineer | todo | D-03 |
| FND-07 | `core/offline_guard.py` network kill-switch installed first in main.py; env hardening; `tests/test_offline.py` (AST scan + runtime socket spy); remove download URL from SAM error; installer must bundle SAM checkpoint (fail build if missing) | build-engineer | todo | D-14 |

## Phase 1 — Detection correctness
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| DET-01 | Valid-pixel mask through all pipelines; SAM post-filter (mean, std, valid-fraction); `_measure_grains` intensity filter; `DetectionParams.invalid_intensity_threshold`; make `tests/test_black_regions.py` pass | detection-engineer | todo | R1 |
| DET-02 | Coverage % / `total_analyzed_area` over valid area; add `valid_area_um2`, `invalid_area_pct` to `AnalysisResult`; scan-border discard must go through core | detection-engineer | todo | R2, B3 |
| DET-03 | Move stats recompute into `core/metrics.py` (single function); fix binary-view overflow (B2) | detection-engineer | todo | B4 |
| DET-04 | ASTM E112 G-number (planimetric + intercept) in results and reports | detection-engineer | todo | B15, D-12 |
| DET-05 | Extend `_auto_crop` to black info bars/borders | detection-engineer | todo | B9 |
| DET-06 | Add `auto` mode to combo; fix reset inconsistency; fix bin spinbox clamp | ui-designer | todo | B10, B16 |
| DET-07 | Per-image calibration and scan rect (model + UI override) | detection-engineer + ui-designer | todo | B7 |
| DET-08 | Dark-grain regression fixture (legit dark grains must survive DET-01) | qa-engineer | todo | |

## Phase 2 — Data layer
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| DATA-01 | `data/models.py` + `data/workspace.py` (hierarchy, sanitising, dedupe) | data-architect | todo | D-04 |
| DATA-02 | `data/session_io.py` save/load: images copy, labels `.npz`, overlay png, grains.json, manifest.json, report.json; atomic writes | data-architect | todo | |
| DATA-03 | `data/catalog.py` sqlite index + `rebuild()`; WAL; lock fallback | data-architect | todo | |
| DATA-04 | QSettings: workspace root, operator, recent sessions, theme | data-architect | todo | D-10 |
| DATA-05 | `import_loose_images()` for existing folders | data-architect | todo | |
| DATA-06 | Projects page: tree Project›Sample›Lot›Session, thumbnails, search/filter, open/duplicate/delete session | ui-designer | todo | R3, R4 |
| DATA-07 | New Session wizard (project/sample/lot/operator/instrument/notes) | ui-designer | todo | |
| DATA-08 | Auto-save session after analysis + on grain edits | data-architect | todo | |

## Phase 3 — Reports
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| REP-01 | `reports/model.py` ReportModel + JSON round-trip + `from_session()` | report-engineer | todo | R5 |
| REP-02 | `reports/charts.py` shared palette, bins, unit scaling | report-engineer | todo | |
| REP-03 | `excel_renderer.py` (xlsxwriter): Overview per-image table, colour tabs, charts with units, raw data last, freeze panes, autofilter, hyperlinks | report-engineer | todo | R6–R8 |
| REP-04 | `pptx_renderer.py` (python-pptx): 16:9 deck, per-image slides, native charts, template support | report-engineer | todo | R9 |
| REP-05 | Reports page: section list, toggles, captions/notes, bins, units, branding; preview | ui-designer | todo | R5 |
| REP-06 | Load existing `report.json` → edit → re-export | report-engineer + ui-designer | todo | R5 |
| REP-07 | Parity test vs legacy exporter, then delete `utils/excel_export.py` | report-engineer | todo | |

## Phase 4 — UI overhaul
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| UI-01 | `ui/design/tokens.py` + `theme.py` (light/dark QSS generator) | ui-designer | todo | R10 |
| UI-02 | `ui/widgets/` component library with animations | ui-designer | todo | |
| UI-03 | `ui/app_shell.py`: rail nav, stacked pages with transitions, breadcrumb, status bar, toasts | ui-designer | todo | |
| UI-04 | Analyze page: parameter redesign, presets, mode cards, inline progress | ui-designer | todo | |
| UI-05 | Review page: canvas upgrades (zoom about cursor, minimap, hover metrics, multi-select, lasso, merge/split, undo/redo) | ui-designer | todo | R11 |
| UI-06 | Results dashboard: animated stat cards, per-image comparison table, charts | ui-designer | todo | |
| UI-07 | Settings page, onboarding/empty states, shortcut overlay (`?`) | ui-designer | todo | |
| UI-08 | Splash, About, icon/branding, DPI checks | ui-designer | todo | D-11 |
| UI-09 | Delete `ui/main_window.py` after parity | ui-designer | todo | |

## Phase 5 — Innovation (populated from 07_IDEAS_BACKLOG.md)
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| INN-run | Run innovator at session start; approve ≥1 idea per session | coordinator | todo | |

## Phase 6 — Release
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| REL-01 | Journey tests (open→calibrate→analyze→edit→save→reload→report→export) | qa-engineer | todo | |
| REL-02 | Update `docs/`, README, GUIDE for v3 | coordinator (haiku agent) | todo | |
| REL-03 | Local PyInstaller build + install/uninstall test; size report | build-engineer | todo | R13 |
| REL-04 | Tag `v3.0.0`, CI, release notes | build-engineer | blocked | needs user go + auth |
