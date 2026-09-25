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

## 2026-09-24 — Demo deck + boss overview PPTX delivered; FIX batch initiated
- **Boss deck delivered** (C:\Users\saman\Documents\SEM_Grain_Analyzer_Overview.pptx, 17 slides, speaker notes). Demo workspace built in scratch/demo/ (git-ignored): gen_workspace.py (26 lots, 128 synthetic micrographs, 4 operators, real analysis pipeline, classical mode since SAM checkpoint absent), capture.py, charts.py, deck/build_deck.js — reusable for future screenshots/docs.
- **Bugs found during deck build** (high priority for PPTX report layout, Excel/UI truncation): 
  - PPTX: title overlaps subtitle; summary table and Methods overflow past footer; normal-fit curve renders as bars instead of line → FIX-11, FIX-12, FIX-13 (report-engineer)
  - Excel: "Lot" column truncated → FIX-14 (report-engineer)
  - Lot result table: Note column clips exclusion reason → FIX-15 (ui-designer)
  - Calibration: check shows "-0.00 %" (negative zero) → FIX-16 (detection-engineer)
  - Scale-bar: finder fooled by rectangular frame around bar → FIX-17 (detection-engineer)
  - Review toolbar: overlap reproduced <1700px width → FIX-09 threshold updated
- FIX items 11–17 added to task board (todo status). Next: batch FIX assignments; second innovator pass; Phase 6.

## 2026-09-24 — REL-01 journey tests completed
- **REL-01 done (5845451)**: tests/test_journey.py with 3 end-to-end journeys via real app shell. Journey 1: threshold mode calibration → analyze → grain editing (lasso/merge) → trash/restore → save/reopen session. Journey 2: boundary mode, FEI metadata auto-calibration, lot sample statistics, verdict badge. Journey 3: both XLSX and PPTX export with exports honour designer edits, verify Methods "Calibrated: No" for uncalibrated images. All journeys pass; no bugs found. SAM detection mode not covered (checkpoint not bundled locally).
- Full suite: **641 passed / 0 failed**.
- Next: User triage of INN-41..51; README v3 review; PPTX FIX-11/12/13 resume decision; Phase 6 release (NSIS test, merge to main, tag v3.0.0).

## 2026-09-24 — FIX batch (non-PPTX) completed
- **FIX-06 done (91a1d65)**: BUILD_WINDOWS.bat + .github/workflows/build.yml generate icon.ico via `python -m ui.design.branding resources\icon.ico`. macOS icon step unchanged.
- **FIX-07 Excel part done (790afdb)**: ReportModel.calibration field added (from metadata["calibration"]), JSON round-trip with backward compat, rendered in Excel Methods sheet only. PPTX render deferred with FIX-11/12/13.
- **FIX-08 done (4488f9a)**: CalStatusChip mounted in app_shell.status_bar, wired apply_to_session, PASS/FAIL/INC verdict badges on lot tree nodes.
- **FIX-09 done (4488f9a)**: DPI/layout polish: toolbars wrap <1700px, stat cards 2×2, card titles wrap. New ui/widgets/layout.py module.
- **FIX-10 done (91a1d65)**: Cleanup: removed unused core/grain_edit.replay_edits/apply_edit functions; THIRD_PARTY_LICENSES.txt removed "CLAUDE.md" mention.
- **FIX-14 done (790afdb)**: Excel auto-sizes Lot/Sample/Image columns (excel_renderer.py).
- **FIX-15 done (4488f9a)**: Lot result field table Note column wraps to show full exclusion reason (UI layout CSS padding fix).
- **FIX-16 done (9242703)**: Calibration check formatting fixed to never show "-0.00 %" (negative-zero handling in cal_verify.py format_percent).
- **FIX-17 done (9242703)**: Scale-bar finder robustness: rejects rectangular drawn frames, only detects actual scale bars (core/scale_bar.py endpoint logic). Added tests/test_scale_bar_frame.py.
- **FIX-11/12/13 parked**: PPTX report layout issues (title overlap, table overflow, chart rendering). User request 2026-09-24: do later. Added D-25 decision.
- **Second innovator pass**: 11 new ideas appended to 07_IDEAS_BACKLOG.md (INN-41..51) with specs; top 3 are INN-41 (control chart), INN-44 (filename template auto-filing), INN-46 (field-outlier check). Full suite: **638 passed / 0 failed**.

## 2026-09-24 — User decisions, PPTX fixes, SAM build fetch, INN-43 core
- **Commits since last handoff**:
  - 560d2fe: BUILD SAM checkpoint download at build time (hash-verified ec2df627...c912); 7 tests pass.
  - 189ee45: INN-43 core done (core/lot_compare.py: delta matrix, Welch ANOVA, TOST equivalence testing); 19 tests added.
  - 21e6d67: FIX-11/12/13 + FIX-07 PPTX (title/subtitle overlap fix, table overflow fix, normal-fit curve renders as line, calibration field render); 10 tests.
- **User decisions (D-26, D-25 resumed)**:
  - Idea triage: **ONLY INN-43 approved** (lot comparison matrix + TOST). **Declined:** INN-41,42,44-51 (control chart, result fingerprint, filename template, auto-worklist, field outliers, image quality, conditions ledger, spot check, uncertainty budget). Mark archived in backlog.
  - **PPTX work resumed** (was parked in D-25 morning) — completed in 21e6d67. v3.0.0 ships with full PPTX support incl. calibration field.
  - **SAM checkpoint**: download at build time (not in repo); fail build if missing.
  - **User timeline**: will fix GitHub auth tonight (FND-04); will supply real SEM images later (D-13); wants release ASAP.
- **Full test suite: 686 passed / 0 failed**. Next: finish INN-43 UI → code review → Phase 6 release.
- **Task board updated**: FIX-11/12/13 marked done (21e6d67); FIX-07 PPTX part marked done; INN-43 core marked done.
- **Idea backlog updated**: INN-41..51 marked as declined by user 2026-09-24.
- **SESSION_STATE.md updated**: in-progress = INN-43 UI; next 3 actions clarified; blocked section cleaned (FND-04 user action, D-13 user supply, INN-30 cancelled, INN-41..51 declined).

## 2026-09-24 — Phase 6 Release: v3.0.0 built and tagged locally
- **INN-43 UI code review complete (00a73df)**: lot_compare_page.py + test_ui_lot_compare.py (9 tests). Projects page multi-select lots → View › Compare lots; LotMeta.is_baseline picker; live delta matrix + TOST verdict. Code review: APPROVE, no blockers (2 minor nits: lot_compare.py:314 sd zeroing heuristic comment; BUILD_WINDOWS.bat:102 explicit errorlevel after Get-FileHash).
- **REL-03 PyInstaller + NSIS build complete (8f8753c, .gitignore update 400fc60)**:
  - PyInstaller: `dist\GrainAnalyzer` 1.3 GB; exe launches; offline guard clean; zero egress (audit pass).
  - NSIS: portable NSIS 3.12 (session scratchpad); built `GrainAnalyzer_Setup.exe` 644 MB (gitignored, repo root); requires UAC/admin for silent install test — deferred to user.
  - **Size report**: dist 1.3 GB (Python + PySide6 + libraries); Setup.exe 644 MB (self-contained, no .NET/VC++ runtime deps).
- **main fast-forwarded to 8f8753c**; **v3.0.0 tag created locally** (annotated, not yet pushed).
- **Full test suite: 686 passed / 0 failed** (unchanged since INN-43 core).
- **RELEASE_CHECKLIST.md**: ticked items 1–7, 10. Pending: install test (user UAC), screenshots review (user), push + CI (awaiting FND-04 GitHub auth fix).
- **Next steps** (when user returns): (1) sign into GitHub as JackSamaniego1 (replace Harvey-FS cached cred); (2) `git push origin main v3-dev --tags`; (3) watch CI build GrainAnalyzer_Setup.exe and .dmg; (4) user test-installs exe locally. If install test finds bug: fix on v3-dev, re-ff main, re-tag v3.0.0 (safe only before push), push again.

## 2026-09-25 — User feedback post-install + overnight sprint plan
- **User installed v3.0.0 and reported 16 change requests** (UX-01..16 in handoff/08_USER_FEEDBACK_2026-09-25.md). Priority areas: (1) Analyze page settings order + pre-analysis gate + image removal, (2) Report editor lazy load (UX-12) + multi-lot format (UX-13) + editable charts (UX-14) + custom palettes (UX-15), (3) UI polish (tooltips, spinners, CPU chip). Analysis: all changes are backwards-compatible, no spec/model breakage, can ship as part of v3.0.0.
- **UX-07 (Cancel is slow) DONE (b0437c1)**: Implemented cooperative cancellation via threading.Event → GrainDetector.analyze(cancel=), AnalysisCancelled exception, core/cancel.py, ui/workers.py; reduced SAM CPU batch size 64→16; cancel completes ≤~1 s in all detection modes; 23 new tests added.
- **FND-04 RESOLVED**: GitHub credential updated from Harvey-FS to JackSamaniego1 (verified with dry-run push; no commits pushed yet).
- **Release hold decision (D-27)**: Release will be held for completion of all UX items. Plan: ui-designer handles UX-01..06, UX-08..11 overnight; report-engineer handles UX-12..16 overnight. By morning: all changes complete + full test suite green + GrainAnalyzer_Setup.exe rebuilt locally. Then: main fast-forward (past b06e51a and subsequent UX commits), re-tag v3.0.0 locally (git tag -f), user tests install locally, and pushes when ready.
- **Overnight sprint**: ui-designer and report-engineer teams in parallel; all UX items targeted for "done" status by morning review.

## 2026-09-25 (overnight) — UX batch complete; v3.0.0 rebuilt and tagged
- **UX batch (UX-01..16) COMPLETE**: All user feedback items shipped. Commits:
  - **UI designer (UX-01..06, UX-08..11)**: b0437c1 (UX-07 cancel cooperative), de99be0 (pre-gate, image removal), e01f8d0 (scale scope toggle), bcf1a82 (scope-aware button text, spinner fix), 3e9a3bb (overlay opacity, tooltips, CPU chip). Settings panel reordered; pre-analysis gate enforces scan area + scale; scope toggles for scale and filters; image removal with restore from lot; nav tooltips ≤150 ms; CPU chip shows device type.
  - **Report engineer (UX-12..16)**: b06e51a (Images tab lazy-load + pixel LRU), 52556a4 (multi-lot report model + TOST matrix), 560deed (custom palettes + palette editor), 435ba01 (editable charts with persist), 1e0a895 (overlay opacity export). Multi-lot report with per-lot summaries, lot comparison (TOST), combined distribution, per-lot sections. Editable charts (bins/units/ranges/titles/fit). Custom palettes (hex/wheel editor, saved locally). Lazy-load Images tab (6 images / 500 MB pixel cache); report export respects opacity.
  - **Results table**: Job/Part/Lot hierarchy + Grains column; sortable/groupable (2523b26).
- **Full suite: 820 passed / 0 failed**. Offline guard clean. Installer rebuilt: GrainAnalyzer_Setup.exe 644 MB, dist 1.3 GB, exe launches OK.
- **v3.0.0 tag**: Annotated, 709fd7d, local only (main fast-forwarded to v3-dev).
- **Ready for user**: Install test → fix any bugs (safe on v3-dev before push) → push main/v3-dev/tags → CI builds release → user downloads to flash drive.

## 2026-09-25 — User feedback fixes: FB-01, FB-02, FB-03 complete
- **Feedback from user review of v3.0.0 GUI** (post-install testing):
  - **FB-01 (bug) DONE (009530d)**: Excel Overview on multi-lot projects stamped all images with the same lot/part number (collect_inputs() used session's single sample/lot). Fix: per-image hierarchy levels from image_levels(); report header aggregates distinct values ("L-1, L-2"); blank hierarchy headers fall back to per-image values (ReportModel.hierarchy_value).
  - **FB-02 (feature) DONE (009530d)**: New Excel "Lot Summary" sheet after Overview — one grain-diameter distribution chart per lot (chart title = lot name), stats block (images, total grains, mean ASTM G, mean diameter/area). Grouped by part+lot. Toggleable report section `lot_summary`; old report.json backfilled. **Decision: Lot Summary default ON, Excel-only for v3.0.0** (D-28).
  - **FB-03 (bug) DONE (009530d)**: "Remove from analyzer" right-click menu threw an error — QAction.triggered(bool) overwrote menu lambdas' uid list. Fixed: ui/pages/image_tree.py new build_menu() structure.
- **Full suite: 839 passed / 0 failed**. Offline guard clean.
- **Next**: Rebuild installer at 009530d; locally retag v3.0.0 (move from 709fd7d); user test-installs exe locally; on approval, push main/v3-dev/tags to GitHub (auth ready).
