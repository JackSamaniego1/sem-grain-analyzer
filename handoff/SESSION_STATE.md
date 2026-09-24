# SESSION STATE — read this first when resuming

**Last updated:** 2026-09-24 (Opus coordinator, saved right before a usage limit)
**Branch:** `v3-dev` · **Last code commit:** `93937b1` (WIP checkpoint) · GitHub push still blocked (FND-04).
**Resume with:** read this file only, then the files the next task needs (see CLAUDE.md "Context & usage discipline").

## Done and committed (details in 03_TASK_BOARD.md / 06_PROGRESS_LOG.md)
Phases 0–3 complete; most of Phase 4. PySide6 + 3-layer offline guard; black-region fix; ASTM E112 G (core/astm.py); non-destructive grain filters (core/postfilter.py + UI filter card); data layer (trash/restore, CLEAR sentinel); report engine + report designer (exports honour all edits, 4 palettes, custom text); app shell (Projects, Wizard, Analyze, Review, Reports, Settings); SEM info-bar auto-exclusion + metadata calibration in core (e6d0e1d); HIER-01 data layer (a916add) + reports side (b50751d); crash-on-close fix; session trash undo. Last green full suite: 396 passed (before the WIP checkpoint).

## IN PROGRESS — resume this first
**HIER-01 UI + DET-05/INN-05 UI** (spec: `handoff/specs/HIER-01.md`). The ui-designer agent (opus) hit the usage limit mid-task; partial work committed as `93937b1`:
- partly done: `ui/hierarchy_ui.py` (label helpers), `ui/widgets/field_editors.py` (typed field editors), profile plumbing in `ui/app_state.py` / `ui/app_shell.py`, partial migration of `ui/dialogs/new_session_wizard.py` and `ui/pages/projects_page.py`, `tests/test_ui_shell_basics.py` touched.
- NOT done: `ui/pages/report_builder.py` hierarchy/display_name/export_basename wiring (was the next step); Settings → "Folder structure & naming" card + rename-to-template dialog; labels in breadcrumb/Review/Analyze/report designer; in_place saving when images_location=="lot"; Part B (info-bar chip + "Use as scan area", metadata auto-calibration with undo, scale-bar length prefill via `core.scale_bar.find_scale_bar_line`); tests; screenshots (demo workspace Job 24-117 "Acme Aerospace" › Part 7718-A "Turbine disk forging, Alloy 718" › Lot L-44A heat "HT-90211").
- **First action:** run the suite, then relaunch the ui-designer (general-purpose, model opus, prompt starts "ROLE: First Read .claude/agents/ui-designer.md…") with: HIER-01 spec "UI" + "Acceptance" sections, Part B wiring (`core.infobar`, `core.sem_metadata.calibration_from_metadata`/`read_sem_metadata`, `result.info_bar_rect`/`info_bar` are already full-frame from `ui/workers.analyze_image`), "continue from commit 93937b1 and finish the NOT-done list; fix any failing tests". APIs: see HIER-01.md + commit messages a916add, b50751d, e6d0e1d.

## Next after that
1. UI-05 remainder: lasso select, merge/split grains (INN-04).
2. Innovator features: INN-27 lot stats + 95 % CI, INN-02 spec limits PASS/FAIL, INN-29 calibration check, INN-30 approval/sign-off (specs in handoff/specs/).
3. UI-08: About/icon/branding, DPI 150/200 % check; second innovator pass.
4. Phase 6: journey tests, docs/README for v3, local PyInstaller build + install/uninstall test (spec must include all new modules; firewall rules), merge to main, tag v3.0.0 (needs user go + GitHub auth).

## Blocked / needs user
- FND-04 GitHub auth (credentials cached for Harvey-FS).
- D-13 real SEM images (validate black threshold 12 and info-bar detection).
- Optional logo/company name (D-11).

## How to run
```powershell
cd "C:\Users\saman\GRAIN ANALYSIS TOOL"
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python main.py
.venv\Scripts\python -m ui.demo --capture scratch/ui     # screenshots
```
