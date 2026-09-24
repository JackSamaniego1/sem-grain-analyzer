# Progress Log (append-only)

## 2026-09-23 — Planning session (Fable)
- Cloned `JackSamaniego1/sem-grain-analyzer` into `C:\Users\saman\GRAIN ANALYSIS TOOL`; created `.venv` (Python 3.11.9); installed all runtime deps + pytest/pytest-qt. App imports and launches.
- Git configured (Saman Iego / samaniegojack12@gmail.com). **Push blocked**: cached credentials are for `Harvey-FS` (403). See `DEVELOPMENT_STATUS.md` for the fix.
- Read the full codebase; wrote `01_CODEBASE_ANALYSIS.md` with 16 defects/debt items.
- Wrote `tests/conftest.py` (synthetic mosaic generator) and `tests/test_black_regions.py`. Confirmed the reported bug: **217 false grains inside pure-black regions** in boundary mode.
- Two research agents (sonnet) reported on Claude Code tooling and library/licensing options → `04_RESEARCH_FINDINGS.md`. Key flag: PyQt6 GPL/commercial licensing (D-03).
- Defined the agent team (`.claude/agents/`: scribe, detection-engineer, data-architect, ui-designer, report-engineer, innovator, qa-engineer, build-engineer, code-reviewer) and skills (`/run-tests`, `/smoke-app`, `/save-handoff`).
- Wrote the coordinator brief `00_START_HERE.md`, task board, decisions, ideas backlog, release checklist, `CLAUDE.md`.
- Project permissions set to auto-accept (`.claude/settings.json`, `acceptEdits` + full allow list) per user request.

## 2026-09-23 — Opus coordinator session 1
- **Phase 0 completed**: FND-01 (version.py 3.0.0-dev, CHANGELOG.md, wired into main/UI); FND-02 (xlsxwriter, python-pptx, qtawesome deps + offline torch); FND-06 (PyQt6→PySide6 migration); FND-07 (offline_guard.py, test_offline.py, Firewall rules). Baseline FND-05: **47 tests passing / 0 failing**.
- **Phase 1 partial**: DET-01 (valid-pixel mask, test_black_regions passes), DET-02 (coverage %, valid_area fields), DET-08 (dark-grain regression). DET-03 in progress (metrics module done; UI overflow fix pending).
- **Decisions approved**: D-03 PySide6 (user), D-14 fully offline/zero egress (user). D-15 added (Windows Firewall defense-in-depth); D-16 added (agent registration at session start).
- **Innovation run approved**: INN-run complete (06493ff). Innovator rewrote 07_IDEAS_BACKLOG.md (40 ideas, top 25 active) and wrote specs INN-02, INN-26, INN-27, INN-29, INN-30. Added to Phase 5.
- **Phase 2/3/4 launched in parallel**: DATA-01..05, DATA-08; REP-01..04; UI-01, UI-02.
- **Blocked**: FND-04 GitHub auth (Harvey-FS credentials cached; user action needed). DET validaton: D-13 flagged (real SEM images wanted for black-region threshold validation).

## 2026-09-24 — Opus coordinator session 1 (cont.)
- **Phases 0–3 completed**: Full data layer, reports back-end, Phase 1 detection fixes, ASTM E112/E1382 compliance engine (b45fb7f). Test count **257 passing**. Commits: fa736c5 (data models/catalog), af8dea2 (report model/renderers), 7c17b7d (design tokens/theme), 04a65b7 (app shell, Projects/Analyze/Review pages, filter UI), c54daeb (grain filter toggles), b45fb7f (ASTM E112/E1382 + INN-26), 55c7f4d (trash/restore, CLEAR sentinel, autosave), c64d6ea (fix: no crash on close), 173a8cf (report designer with live preview + exports).
- **UI-05 majority done**: zoom, minimap, hover metrics, multi-select working; lasso select + merge/split deferred to INN-04.
- **Stage-1 UI agent (opus) hit API rate limit mid-task**. Work recovered via checkpoint commit 04a65b7; session continued with haiku agents for refinement.
- **New rows created**: REP-08 (renderers + grain notes), DET-05 (auto-detect info bars), INN-05 (SEM metadata calibration), REV-S2 (report designer code review), FIX-01 (crash on close fix).
- **Next**: Review REP-08/DET-05/INN-05, wire into Analyze page; UI-05 lasso/merge/split; Phase 5 features (INN-27, INN-02, INN-29, INN-30); Phase 6 journey tests + release.

## 2026-09-24 — Opus coordinator (cont.), saved before usage limit
- Committed: REP-08 exports honour designer edits (fb09964); DET-05 info-bar + INN-05 metadata calibration core (e6d0e1d); HIER-01 reports (b50751d) and data layer (a916add) — user request: Job # › Part Number › Lot, images in the lot, user-editable naming; session trash undo (19a20be); context/usage discipline rules in CLAUDE.md (17855db).
- User set autoContinueAtUsageLimit=true (user settings) and asked for frequent handoff saves + context clears.
- HIER-01 UI agent hit the usage limit; partial work checkpointed as 93937b1 (tests may fail). Resume per SESSION_STATE.md.

## 2026-09-24 — HIER-01 UI completion (scribe commit)
- **HIER-01 UI + DET-05/INN-05 UI finished** (Opus coordinator continued). Three commits:
  - 1d6df50: hierarchy-aware reports, Settings "Folder structure & naming" card + rename-to-template dialog with undo, level labels in breadcrumb/Analyze/Review/Reports/wizard, in-lot image storage, info-bar hatch + chip + "Use as scan area", metadata auto-calibration with undo toast, scale-bar prefill.
  - 525fc9f: 16 tests in tests/test_ui_hierarchy.py.
  - 92b6093: demo workspace Job 24-117 › Part 7718-A › Lot L-44A; display names in report outline.
- Full test suite verified: **412 passed / 0 failed**.
- Screenshots captured: 15 PNGs in scratch/ui/.
- Three follow-ups identified for next sprint: (a) open wizard doesn't relabel when Settings change, (b) wizard date-field arrow slightly clipped, (c) Excel/PowerPoint export not re-verified against HIER-01 acceptance criteria (hierarchy labels, export basename).

## 2026-09-24 — HIER follow-ups completed (Opus coordinator)
- **HIER-02 done (276d579)**: New Session wizard now relabels dynamically when Settings "Folder structure & naming" changes; entered data preserved. Tests in tests/test_ui_hierarchy.py.
- **HIER-03 done (276d579)**: Date-field dropdown arrow no longer clipped (ui/design/theme.py theme fix).
- **HIER-04 done (276d579)**: Excel/PPTX export re-verified vs HIER-01 spec (hierarchy labels, export basename, renamed levels, filename sanitisation). 3 acceptance tests pass; no defects. Tests in tests/test_hier01_export_acceptance.py.
- Full suite: **417 passed / 0 failed**. SESSION_STATE.md cleaned (UTF-8 encoding, ASCII punctuation).
- Next: UI-09 + UI-11 in parallel (file management and scale-bar calibration), then UI-10.

## 2026-09-24 — UI-09 + UI-11 completed (Opus coordinator)
- **UI-09 done (c0e8e53)**: Projects browser file management. New: data/file_ops.py (Qt-free trash/restore entry point), ui/widgets/selection_bar.py (multi-select action bar), ui/dialogs/move_to_dialog.py (same-level folder picker), ui/dialogs/rename_dialog.py. Right-click context menu on folders and images (Open/Rename/Move to…/Delete), per-row select checkboxes with Ctrl/Shift multi-select, action bar shows "N selected — Select all · Move to… · Delete · Clear". Trash with undo (data layer integration). Tests: test_data_file_ops.py (11), test_ui_file_management.py (9).
- **UI-11 done (c0e8e53)**: Scale-bar calibration modes. New: core/scale_bar_snap.py (snap-to-endpoints, endpoint detection, tilt warning), ui/canvas/calibration_canvas.py (pan + snap interaction), ui/calibration_dialog.py rebuilt (3 modes: Rectangle editable box, Level line horizontal-locked + 4x loupe, Free line + tilt warning; pan via middle/right-drag; Alt disables snap). Last-used mode persisted in calibration_ui.json. core/scale_bar.py kernel width 20→21 (odd, fixes 1 px right offset). Tests: test_scale_bar_snap.py (13), test_ui_calibration_modes.py (16).
- Follow-ups added to task board: FIX-02 (image card open doesn't jump), FIX-03 (Delete key routing), FIX-04 (Catalog batching).
- Full suite: **466 passed / 0 failed**. Code-reviewer verdict: APPROVE.
- Next: UI-10 (guided tour), then UI-05 remainder (lasso, merge/split), then Phase 5 innovator features.

## 2026-09-24 — INN-27 backend + UI-10 completed
- **INN-27 backend done (ce833e5)**: Lot sample statistics with 95% CI per ASTM E112/E1382. New: core/metrics.sample_statistics (mean G, 95% CI Student t, %RA, fields_needed, outlier_flag), ImageRecord.included + exclusion_reason + per-session audit_log, Catalog.fields_for_lot. Settings: required_fields=5, target_RA_pct=10. Exports: Excel Overview lot block (G ± CI, %RA, count); PPTX "G ± CI" tile (opt-in via sample_statistics param). 11 tests in tests/test_sample_statistics.py.
- **UI-10 done (932e184)**: First-run guided tour of basic SAM analysis. New: ui/tour/ (steps, overlay, controller), 11 steps with animated spotlight + dimmed overlay, callout cards, Skip, Don't show on startup toggle (ui_state["tour"]), Help › Show tour to replay. Never auto-starts under pytest, offscreen render, or GRAIN_NO_TOUR=1. 12 tests in tests/test_ui_tour.py. **Known**: tour Analyze/Review steps point at nav icons on fresh install (no session open yet).
- Full suite: **489 passed / 0 failed**.
- In progress (5 agents): INN-02 backend, INN-29 backend, INN-30 backend, INN-27 UI Lot result card, PyInstaller dry run.
- Next: land in-progress work, then UI pieces for INN-02/29/30.

## 2026-09-24 — INN-02 + INN-29 + INN-27 UI + FIX backend + overlay export done
- **Overlay export fix (6bf41ff)**: core/overlay_compose.py compose_full_overlay now returns full original image with dashed outline around measured region; SEM info bar and unscanned areas untouched. Root cause: v2 only padded when manual scan area was set; auto info-bar crop stayed cropped. User request 2026-09-24: exported overlays must show full original frame incl. SEM info bar.
- **INN-02 backend done (924f47f)**: data/specs.py ILAC-G8 verdict logic (PASS/FAIL/INCONCLUSIVE per spec limits). Report badges only when a spec exists (default: none). Spec limits optional, default off, no effect on analysis or nags.
- **INN-29 backend done (924f47f)**: core/cal_verify.py FFT pitch verification + data/cal_records.py record storage. AppSettings.calibration_verification_enabled=False (optional, default off). Newer failed check supersedes older pass.
- **INN-27 UI done (924f47f)**: Lot result card with Include checkbox + exclusion reason dropdown. Exports include lot block. Stale saved results refiltered. Connects INN-27 backend to reports workflow.
- **FIX-02/03/04 done (924f47f)**: Image card open jumps to that image (FIX-02); Delete key routed via context menu not app_shell (FIX-03); trash_node batched delete uses single Catalog instance (FIX-04).
- Full suite: **544 passed / 0 failed**. Code review: APPROVE. PyInstaller local build dry run: PASS (exe 51.6 MB, dist 922 MB); NSIS untested (makensis not installed locally); SAM checkpoint absent locally (release build requires it).
- **User decisions 2026-09-24** (recorded in 05_DECISIONS.md as D-20 through D-23): Spec limits & calibration check optional default off; INN-30 cancelled; overlay shows full original image; newer failed supersedes older pass.
- Next: UI for INN-02/29 (spec editor, calibration dialog, Settings controls); lasso select/merge/split (INN-04); UI-08 About/DPI; journey tests; Phase 6 release.

## 2026-09-24 — UI-05 grain editing, INN-02/29 UI, UI-08 About/branding done (5a1cd42)
- **UI-05 grain editing complete (5a1cd42)**: Lasso select (L), merge touching grains (M), cut/split grains (C), undo/redo, persisted to grain_edits.json, honoured by exports/lot results. core/grain_edit.py handles replay; ui/canvas/edit_actions.py wires keybindings. Re-analysing discards edits (same as manual removals). Re-analysing has "Remove grain edits?" prompt (UX D-24).
- **INN-02 UI complete (5a1cd42)**: Spec editor in project/part settings (project ⋮ menu "Spec limits (optional)…"). PASS/FAIL/INCONCLUSIVE verdict badge rendered only on Lot result card when a spec applies; verdict passed to Excel/PPTX exports. Settings: default off (no effect on analysis).
- **INN-29 UI complete (5a1cd42)**: Calibration check dialog (project settings "Check calibration…"), status chip in app_shell.status_bar (CalStatusChip), Settings toggle "Calibration verification" default off. Per-instrument check records after first check. Settings required: 5 fields, 10% target %RA.
- **UI-08 complete (5a1cd42)**: About dialog (offline statement, licence viewer), code-drawn icon (ui/design/branding.py, ORGANIZATION_NAME constant for D-11), shortcut cheat sheet (L/M/C/V grain edits), min window height 640 enforced.
- **FIX-05 done (6bf41ff)**: Overlay export shows full original frame incl. SEM info bar, thin dashed outline around measured region.
- Full suite: **595 passed / 0 failed**. Code review: APPROVE.
- **Follow-ups identified**: Build resources icon generation (icon.ico via ui.design.branding), ReportModel calibration field (metadata), CalStatusChip mount in status bar + wire apply_to_session, DPI/layout (toolbar overlap <1400px, Projects card truncation, Analyze stat labels clipped at 1100px), cleanup (grain_edit.replay_edits unused, THIRD_PARTY_LICENSES.txt remove "CLAUDE.md" mention).
- **In progress**: Demo-workspace + PowerPoint overview deck for user's boss (agent-built, scratch/demo).
- **Next**: Finish boss deck; address follow-ups above; second innovator pass; Phase 6 (journey tests, README v3, NSIS test, merge to main, tag v3.0.0).
