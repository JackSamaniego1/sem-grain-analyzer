# Task Board

Status values: `todo` · `doing` · `review` · `done` · `blocked`. Keep IDs stable; add new tasks at the end of their phase. Owner = agent name.

## Phase 0 — Foundation
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| FND-01 | Create branch `v3-dev`; add `version.py` (`3.0.0-dev`) and `CHANGELOG.md`; wire version into main.py, main_window, settings_panel, spec, NSIS, README | build-engineer | done | a0b4d3b |
| FND-02 | Add `xlsxwriter`, `python-pptx`, `qtawesome` to requirements/build scripts/CI/spec hiddenimports (+ `collect_data_files('qtawesome')`); delete `.github/workflows/build.ymlresources/`; CPU-only torch index in build scripts | build-engineer | done | a0b4d3b |
| FND-03 | Get user decision on D-03 (PyQt6 licensing) and D-10/D-11 | coordinator | done | D-03 → PySide6; D-14 offline added; D-10..13 defaulted |
| FND-04 | User fixes GitHub auth; push `v3-dev` | user | blocked | creds cached for `Harvey-FS` |
| FND-05 | Baseline: `/run-tests` + `/smoke-app`; record results in progress log | qa-engineer | done | 47 passed / 0 failed |
| FND-06 | Migrate PyQt6 → PySide6 across main.py + ui/; requirements, spec, build scripts, CI; LICENSE.txt + THIRD_PARTY_LICENSES.txt | build-engineer | done | D-03; a0b4d3b |
| FND-07 | `core/offline_guard.py` network kill-switch installed first in main.py; env hardening; `tests/test_offline.py` (AST scan + runtime socket spy); remove download URL from SAM error; installer must bundle SAM checkpoint (fail build if missing) | build-engineer | done | D-14; a0b4d3b |

## Phase 1 — Detection correctness
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| DET-01 | Valid-pixel mask through all pipelines; SAM post-filter (mean, std, valid-fraction); `_measure_grains` intensity filter; `DetectionParams.invalid_intensity_threshold`; make `tests/test_black_regions.py` pass | detection-engineer | done | fc412c0 |
| DET-02 | Coverage % / `total_analyzed_area` over valid area; add `valid_area_um2`, `invalid_area_pct` to `AnalysisResult`; scan-border discard must go through core | detection-engineer | done | fc412c0 |
| DET-03 | Move stats recompute into `core/metrics.py` (single function); fix binary-view overflow (B2) | detection-engineer | done | core/metrics.compute_statistics; UI overflow fixed 04a65b7 |
| DET-04 | ASTM E112 G-number (planimetric + intercept) in results and reports | detection-engineer | done | b45fb7f |
| DET-05 | Extend `_auto_crop` to black info bars/borders | detection-engineer | done | 1d6df50: info-bar hatch + chip + "Use as scan area" |
| DET-06 | Add `auto` mode to combo; fix reset inconsistency; fix bin spinbox clamp | ui-designer | done | 04a65b7 |
| DET-07 | Per-image calibration and scan rect (model + UI override) | detection-engineer + ui-designer | done | 173a8cf |
| DET-08 | Dark-grain regression fixture (legit dark grains must survive DET-01) | qa-engineer | done | fc412c0 |
| DET-09 | Expose new detection params (invalid_intensity_threshold etc.) + invalid-area overlay in UI; switch worker to core.discard_border_grains | ui-designer | done | 04a65b7 |
| DET-10 | Post-analysis grain filter toggles (border / false grains / size / shape), non-destructive, persisted per session; core `core/postfilter.py` + UI filter card on Analyze & Review | detection-engineer + ui-designer | done | core c54daeb; UI 04a65b7 |

## Phase 2 — Data layer
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| DATA-01 | `data/models.py` + `data/workspace.py` (hierarchy, sanitising, dedupe) | data-architect | done | fa736c5 |
| DATA-02 | `data/session_io.py` save/load: images copy, labels `.npz`, overlay png, grains.json, manifest.json, report.json; atomic writes | data-architect | done | fa736c5 |
| DATA-03 | `data/catalog.py` sqlite index + `rebuild()`; WAL; lock fallback | data-architect | done | fa736c5 |
| DATA-04 | QSettings: workspace root, operator, recent sessions, theme | data-architect | done | fa736c5 |
| DATA-05 | `import_loose_images()` for existing folders | data-architect | done | fa736c5 |
| DATA-06 | Projects page: tree Project›Sample›Lot›Session, thumbnails, search/filter, open/duplicate/delete session | ui-designer | done | 04a65b7 |
| DATA-07 | New Session wizard (project/sample/lot/operator/instrument/notes) | ui-designer | done | 04a65b7 |
| DATA-08 | Auto-save session after analysis + on grain edits | data-architect | done | 55c7f4d |
| DATA-09 | Trash/restore for projects, samples, lots; CLEAR sentinel; filter persistence | data-architect | done | 55c7f4d |

## Phase 3 — Reports
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| REP-01 | `reports/model.py` ReportModel + JSON round-trip + `from_session()` | report-engineer | done | af8dea2 |
| REP-02 | `reports/charts.py` shared palette, bins, unit scaling | report-engineer | done | af8dea2 |
| REP-03 | `excel_renderer.py` (xlsxwriter): Overview per-image table, colour tabs, charts with units, raw data last, freeze panes, autofilter, hyperlinks | report-engineer | done | af8dea2 |
| REP-04 | `pptx_renderer.py` (python-pptx): 16:9 deck, per-image slides, native charts, template support | report-engineer | done | af8dea2 |
| REP-05 | Reports page: section list, toggles, captions/notes, bins, units, branding; preview | ui-designer | done | 173a8cf |
| REP-06 | Load existing `report.json` → edit → re-export | report-engineer + ui-designer | done | 173a8cf |
| REP-07 | Parity test vs legacy exporter, then delete `utils/excel_export.py` | report-engineer | done | 173a8cf |
| REP-08 | Renderers honour section order/titles/cover+overview toggles/palette/custom text; grain notes in Raw sheet | report-engineer | doing | |

## Phase 4 — UI overhaul
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| UI-01 | `ui/design/tokens.py` + `theme.py` (light/dark QSS generator) | ui-designer | done | 7c17b7d |
| UI-02 | `ui/widgets/` component library with animations | ui-designer | done | 7c17b7d |
| UI-03 | `ui/app_shell.py`: rail nav, stacked pages with transitions, breadcrumb, status bar, toasts | ui-designer | done | 04a65b7 |
| UI-04 | Analyze page: parameter redesign, presets, mode cards, inline progress | ui-designer | done | 04a65b7 |
| UI-05 | Review page: canvas upgrades (zoom about cursor, minimap, hover metrics, multi-select, lasso, merge/split, undo/redo) | ui-designer | done | 5a1cd42: lasso (L), merge (M), split (C), undo, persisted grain_edits, exported+audit |
| UI-06 | Results dashboard: animated stat cards, per-image comparison table, charts | ui-designer | done | 04a65b7 |
| UI-07 | Settings page, onboarding/empty states, shortcut overlay (`?`) | ui-designer | doing | settings page + shortcut overlay done; onboarding polish remains |
| UI-08 | Splash, About, icon/branding, DPI checks | ui-designer | done | 5a1cd42: About dialog, code-drawn icon (branding.py), shortcut sheet, min window height 640 |
| UI-09 | Delete `ui/main_window.py` after parity | ui-designer | done | 173a8cf — legacy UI modules deleted |
| HIER-02 | Fix: New Session wizard doesn't relabel when Settings "Folder structure" change | ui-designer | done | 276d579 |
| HIER-03 | Fix: New Session wizard date-field dropdown arrow slightly clipped | ui-designer | done | 276d579 |
| HIER-04 | Acceptance check: Excel/PowerPoint export hierarchy labels + export basename vs HIER-01 spec | qa-engineer | done | 276d579 |
| UI-09 | Projects browser file management (user request 2026-09-24): right-click menu on every folder/image (Delete → trash with undo, Move to…, Rename, Open); per-row checkbox to select items; multi-select (checkbox, Ctrl/Shift-click) with a selection action bar "N selected — Delete · Move to… · Clear"; Move to… opens a picker of valid destination folders (same level only, e.g. images → another lot). Deleting must be obvious, not hidden. | ui-designer + data-architect | done | c0e8e53; New: data/file_ops.py (Qt-free), ui/widgets/selection_bar.py, ui/dialogs/{move_to,rename}.py; 20 tests. |
| UI-10 | First-run guided tour of a basic SAM analysis: animated spotlight + dimmed overlay, callout cards, Skip, "Don't show on startup", Help → Show tour to replay. Spec: handoff/specs/UI-10.md | ui-designer (opus) | done | 932e184; 11 steps, never auto-starts under pytest/offscreen/GRAIN_NO_TOUR=1. Known: Analyze/Review steps point at nav icons on fresh install. |
| UI-11 | Scale-bar calibration modes: Rectangle (editable box, width only, snap to bar ends), Level line (horizontal-locked, loupe, snap; default), Free line (with tilt warning). Spec: handoff/specs/UI-11.md | detection-engineer (core snap) + ui-designer | done | c0e8e53; core/scale_bar_snap.py, ui/canvas/calibration_canvas.py, ui/calibration_dialog.py rebuilt; 29 tests. |

## Phase 5 — Innovation (populated from 07_IDEAS_BACKLOG.md)
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| INN-run | Run innovator at session start; approve ≥1 idea per session | coordinator | done | 06493ff: 07_IDEAS_BACKLOG.md rewritten (40 ideas, top 25 active); INN-02, INN-26, INN-27, INN-29, INN-30 specs approved |
| INN-02 | Spec limits PASS/FAIL/INCONCLUSIVE | data-architect + ui-designer | done | 5a1cd42: spec editor in project settings, verdict badge on Lot card, optional default off |
| INN-05 | Calibration from SEM TIFF metadata (Zeiss/FEI/JEOL/Hitachi/TESCAN) | detection-engineer | done | 1d6df50: metadata auto-calibration with undo toast |
| INN-26 | ASTM E112/E1382 compliance engine (detection-engineer, depends DET-04) | detection-engineer | done | b45fb7f |
| INN-27 | Lot statistics + 95% CI + fields-needed (report-engineer+data-architect, depends DATA-03, INN-26) | report-engineer + data-architect | done | 924f47f: UI Lot result card (Include checkbox + reason); backend ce833e5. 544 tests. |
| INN-29 | Calibration verification vs reference standard | detection-engineer | done | 5a1cd42: check dialog, status chip, Settings toggle, required fields/target %RA |
| INN-30 | Approval + SHA-256 sealed sign-off, local only | data-architect | cancelled | User request 2026-09-24: not needed for v3.0.0. |

## Fixes
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| FIX-01 | Crash on close while background tasks run | build-engineer | done | c64d6ea |
| FIX-02 | Opening a lot from image card does not jump to that image | ui-designer | done | 924f47f |
| FIX-03 | Delete key still routed via app_shell.py instead of context menu | ui-designer | done | 924f47f |
| FIX-04 | trash_node builds a second Catalog during batched delete; could pass one through | data-architect | done | 924f47f |
| FIX-05 | Overlay export must show full original image incl. SEM info bar | detection-engineer | done | 6bf41ff: core/overlay_compose.py compose_full_overlay; thin dashed outline around measured region |
| FIX-06 | Build: resources/icon.ico generation via `python -m ui.design.branding resources\icon.ico` | build-engineer | todo | hookinto BUILD_WINDOWS.bat + .github/workflows/build.yml |
| FIX-07 | ReportModel: add calibration field (from metadata["calibration"]); render in Excel/PPTX | report-engineer | todo | INN-29 payload sits in metadata only; not shown in exports |
| FIX-08 | Mount CalStatusChip in app_shell.status_bar; wire apply_to_session; verdict badges on tree nodes | ui-designer | todo | INN-29 UI wired to dialog/Settings; chip placement + tree integration remain |
| FIX-09 | DPI/layout review: toolbar overlaps view buttons <1400px; Projects card titles truncated ("Ses...n A"); Analyze stat labels clipped at 1100px | ui-designer | todo | D-24 layout polish |
| FIX-10 | Cleanup: core/grain_edit.replay_edits unused (wire or remove); THIRD_PARTY_LICENSES.txt remove "CLAUDE.md" mention | code-reviewer | todo | minor tech debt |

## Code Review
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| REV-S2 | Code review of report designer | code-reviewer | doing | |

## Phase 6 — Release
| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| REL-01 | Journey tests (open→calibrate→analyze→edit→save→reload→report→export) | qa-engineer | todo | |
| REL-02 | Update `docs/`, README, GUIDE for v3 | coordinator (haiku agent) | todo | |
| REL-03 | Local PyInstaller build + install/uninstall test; size report | build-engineer | todo | R13 |
| REL-04 | Tag `v3.0.0`, CI, release notes | build-engineer | blocked | needs user go + auth |
