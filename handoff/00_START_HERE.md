# SEM Grain Analyzer v3 — Coordinator Brief

**Written for:** the Claude **Opus** coordinator session that will run the upgrade.
**Prepared by:** the Fable planning session on 2026-09-23.
**Read order:** this file → `SESSION_STATE.md` → `03_TASK_BOARD.md` → `01_CODEBASE_ANALYSIS.md`. Everything else is reference.

---

## 1. Mission

Turn the existing SEM Grain Analyzer (v2.3, PyQt6 desktop app, ~2 000 lines of Python) into **v3.0 — a corporate-lab-grade materials analysis workstation**:

1. **Correct** — black/invalid regions must never be counted as grains; coverage and statistics must be computed only over valid analysed area.
2. **Organised** — analyses are stored locally in a labelled hierarchy (Project › Sample › Lot › Session) the way Keyence microscope software does, browsable and searchable inside the app, re-openable, re-editable.
3. **Reportable** — an in-app editable report model that renders to a much better Excel workbook (per-image overview table, colour-coded tabs, real charts with units, raw data last) and to a visual PowerPoint deck.
4. **Beautiful** — a full visual overhaul: design system, animated navigation shell, modern widgets, polished canvas interactions, light/dark themes. "Fancy but appropriate for a corporate lab."
5. **Innovative** — a dedicated innovator agent continuously proposes and specs new capabilities without waiting for the user. At least three innovator ideas ship in v3.0.
6. **Shippable** — the GitHub Actions release pipeline keeps producing a Windows installer (and macOS DMG) that colleagues can download.

The user's own words that must not be lost: *"Go crazy and make big changes. If I hate something I can change it."* Bias toward bold, complete, well-tested changes over cautious increments.

---

## 2. Operating rules for the coordinator

- **You coordinate; agents implement.** Your context is the scarce resource. Delegate every implementation task to the agent that owns that area (see `02_TEAM_ROSTER.md`). Read agent *reports*, not their diffs, unless reviewing.
- **Never spawn a Fable-model agent.** The user reserves Fable for planning sessions only. All agents have their model pinned in `.claude/agents/*.md` (opus/sonnet/haiku). Do not override to fable.
- **Review → commit loop:** feature agent finishes → `code-reviewer` (sonnet) reviews → `qa-engineer` runs `/run-tests` → you commit on branch `v3-dev` with a conventional message → `/save-handoff`.
- **Context discipline (user request 2026-09-24):** save the handoff after every commit and prompt the user to `/clear` after every 2–3 committed tasks once no agent is running — see "Context & usage discipline" in CLAUDE.md. On resume read only `SESSION_STATE.md` + what the next task needs.
- **Handoff discipline:** invoke `/save-handoff` (scribe, haiku) after **every** completed task and **always** before you stop. The user explicitly asked that handoff files be saved regularly. `SESSION_STATE.md` must let a cold session resume in under two minutes.
- **Token discipline:** self-contained prompts (agents have no memory of this conversation); include file paths and line numbers; ask for reports under a word cap; run at most 3 agents concurrently; use haiku for mechanical work.
- **Do not push to GitHub** until the user confirms authentication is fixed (see §9). Commit locally and often.
- **Ask the user only for the decisions in §8.** Everything else is yours to decide — record it in `05_DECISIONS.md`.

### First 15 minutes
1. `git status`, `git log --oneline -5`, confirm you are on `v3-dev` (create from `main` if missing).
2. `/run-tests` — expect exactly two failures: `test_black_regions_are_not_grains[boundary]` and `[threshold]` (2 failed, 2 passed). Those failing tests are the acceptance criterion for DET-01. (A `skimage.remove_small_objects(min_size=…)` deprecation warning at `grain_detector.py:622` should be cleaned up in DET-03.)
3. `/smoke-app` — confirm the v2.3 UI still launches headless.
4. Read `SESSION_STATE.md`; pick up "Next 3 actions".
5. Kick off Phase 0 (build-engineer) and DET-01 (detection-engineer) in parallel — they touch disjoint files.

---

## 3. Product requirements (traceable)

| ID | Requirement (user's intent) | Owner agent | Acceptance |
|----|------------------------------|-------------|------------|
| R1 | Black regions are not detected as grains, in every detection mode | detection-engineer | `tests/test_black_regions.py` green; validated on the user's real images if provided |
| R2 | Statistics/coverage computed over valid area only | detection-engineer | `AnalysisResult.valid_area_um2` + tests |
| R3 | Data storage & recall inside the app: folders by **sample** and **lot number**, labelled, browsable | data-architect + ui-designer | Create project/sample/lot, run analysis, close app, reopen, find it via browser, open it |
| R4 | Files visualised in-app based on selected data folder | ui-designer | Project Browser shows thumbnails + metadata for the selected node |
| R5 | Reports editable in-app before export and re-editable after load | report-engineer + ui-designer | Edit title/captions/sections, export, reload `report.json`, edit again |
| R6 | Excel: per-image overview table on the Overview sheet | report-engineer | One row per image with all key stats |
| R7 | Excel: severely upgraded charts with correct units & axis labels | report-engineer | Axis titles carry units; number formats; gridlines; palette |
| R8 | Excel: raw data sheets last; tabs colour-coded | report-engineer | Sheet order + tab colours asserted in tests |
| R9 | PowerPoint report "set for visual images" | report-engineer | 16:9 deck with per-image slides and native charts |
| R10 | Big visual overhaul: animations, buttons, modern UI, corporate-lab appropriate | ui-designer | Design system + shell + all screens migrated; screenshots in `scratch/` |
| R11 | Professional lab usability | ui-designer + innovator | Guided workflow, keyboard shortcuts, undo/redo, no blocking modals |
| R12 | New ideas generated and expanded without user input | innovator | `07_IDEAS_BACKLOG.md` grows every session; ≥3 ideas shipped |
| R13 | Installer remains downloadable | build-engineer | CI green on tag; Release assets present |

---

## 4. Target architecture (v3)

```
main.py                     entry; splash; creates AppShell
version.py                  __version__ single source of truth
core/                       pure algorithms (no Qt)
  grain_detector.py         + valid-pixel mask, ASTM G-number, unified stats
  scale_bar.py
  metrics.py                (new) stats, ASTM E112 planimetric/intercept, units
data/                       (new) persistence layer, no Qt
  models.py                 Project/Sample/Lot/Session/ImageRecord dataclasses
  workspace.py              root discovery, folder creation, name sanitising
  session_io.py             save/load images, labels (.npz), grains.json, report.json
  catalog.py                sqlite index + rebuild
reports/                    (new) reporting, no Qt
  model.py                  ReportModel + JSON round-trip
  charts.py                 shared palette/bins for xlsx + pptx
  excel_renderer.py         xlsxwriter
  pptx_renderer.py          python-pptx
  templates/                default.pptx, logo placeholder
ui/
  app_shell.py              rail nav + stacked pages + top/status bars
  design/tokens.py, theme.py
  widgets/                  reusable animated components
  pages/projects_page.py, analyze_page.py, review_page.py, reports_page.py, settings_page.py
  canvas/image_canvas.py    upgraded interactions + undo stack
  workers.py                AnalysisWorker (moved from main_window)
tests/                      pytest + pytest-qt, synthetic fixtures
handoff/                    this directory
```
Keep `ui/main_window.py` working until `app_shell.py` reaches parity, then delete it.

---

## 5. Phases and gates

Phases can overlap where files are disjoint. Each gate = tests green + reviewer APPROVE + handoff saved.

- **Phase 0 Foundation** (build-engineer, qa) — branch, `version.py`, dependencies (xlsxwriter, python-pptx, qtawesome), spec/CI sync, delete stray `.github/workflows/build.ymlresources/`, baseline test run. *Gate: CI config valid, suite runs.*
- **Phase 1 Detection correctness** (detection-engineer) — DET-01…DET-07. *Gate: black-region tests green, no regression on plain mosaic, perf budget met.*
- **Phase 2 Data layer** (data-architect, then ui-designer for the browser) — DATA-01…DATA-07. *Gate: round-trip test; browser opens a saved session.*
- **Phase 3 Reports** (report-engineer, ui-designer for the editor) — REP-01…REP-07. *Gate: xlsx/pptx open in Office; sheet order + tab colours asserted.*
- **Phase 4 UI overhaul** (ui-designer) — UI-01…UI-08. Start UI-01/UI-02 (design system) as early as Phase 1; pages migrate as their back-ends land. *Gate: every v2.3 capability reachable in the new shell; screenshots reviewed.*
- **Phase 5 Innovation** (innovator → owners) — run the innovator at the start of every session; approve ideas whose score is high and dependencies are met. *Gate: ≥3 shipped with tests.*
- **Phase 6 Release** (qa, build-engineer) — journey tests, docs, build, install test, tag `v3.0.0`. *Gate: RELEASE_CHECKLIST all ticked; user go for push/tag.*

Suggested parallelism: Phase 0 ∥ Phase 1 ∥ UI-01/02; then Phase 2 ∥ Phase 3 ∥ UI-03; then UI-04…08 ∥ Phase 5; then Phase 6.

---

## 6. Delegation protocol

Prompt template for any feature agent (fill every bracket; do not rely on shared context):

```
Task <ID> — <title>
Goal: <one sentence>
Why: <user requirement R#>
Files you own for this task: <paths>
Do NOT touch: <paths>
Current behaviour: <what the code does now, with file:line>
Required behaviour: <precise spec / acceptance test>
Constraints: <perf, licensing, API stability, no Qt in core/…>
Definition of done: tests green (`.venv\Scripts\python -m pytest tests -q -p no:cacheprovider`), report ≤ 250 words: changed files, how verified, open questions.
Do not commit.
```

After the report: spawn `code-reviewer` with the diff range; if APPROVE and `/run-tests` is green, commit:
`git add <files> && git commit -m "<type>(<area>): <summary>\n\nRefs: <task id>\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`, then `/save-handoff`.

Escalate to the user only when: a decision in §8 is needed, a requirement conflicts with another, or real SEM images are needed to validate detection.

---

## 7. Quality bars

- Tests: every task adds/updates tests; suite stays green on `v3-dev`.
- Performance: classical pipelines < 3 s per 2048×1536 image on CPU; UI never blocks > 50 ms on the GUI thread; project browser handles 1 000 sessions.
- Data safety: atomic writes (temp+rename), never move/delete user images, schema-versioned JSON, SQLite is a rebuildable cache.
- Licensing: only MIT/BSD/Apache/LGPL additions. No GPL/commercial widget libraries.
- Compatibility: Windows 10/11 first; macOS build must not break. Python 3.11.
- UX: keyboard reachable, tooltips, 4.5:1 contrast, DPI 100–200 %.

---

## 8. Decisions that need the user

| ID | Question | Default if no answer |
|----|----------|----------------------|
| D-03 | PyQt6 is GPL/commercial. Distributing a closed installer to colleagues may need a Riverbank commercial licence; PySide6 (LGPL) is a near-drop-in alternative. Keep PyQt6, buy licence, or migrate to PySide6? | Keep PyQt6; permissive add-ons only; keep code PySide6-portable (no PyQt-only APIs beyond `pyqtSignal`). |
| D-10 | Default workspace root for lab data? (e.g. `%USERPROFILE%\Documents\GrainAnalyzer\Projects` vs a network share) | Documents folder; user can change in Settings. |
| D-11 | Corporate branding (logo, colours, company name) for reports and splash? | Neutral "Grain Analyzer" branding; logo slot left empty. |
| D-12 | ASTM E112 G-number required in reports? | Yes when calibrated; label the method. |
| D-13 | Provide 3–5 real SEM images (incl. one with black regions) for validation? | Synthetic fixtures only; flag as risk. |

---

## 9. Git and release

- Branch `v3-dev` for all work; `main` stays at v2.3 so the existing installer download keeps working until v3.0.0 is tagged.
- **GitHub push is currently blocked**: the machine's cached credentials belong to another account (`Harvey-FS`), remote is `https://github.com/JackSamaniego1/sem-grain-analyzer.git`. The user must either clear the Windows credential entry for `github.com` and sign in as `JackSamaniego1`, or switch the remote to SSH. Until then: commit locally, never push. `DEVELOPMENT_STATUS.md` has step-by-step instructions to give the user.
- Releases: tag `v3.0.0` → CI builds `GrainAnalyzer_Setup.exe` + `GrainAnalyzer.dmg` → GitHub Release. Only with the user's explicit go.

---

## 10. Risks

- Detection fix over-suppresses legitimately dark grains → mitigate with conservative default threshold + user-exposed parameter + tests for dark-grain mosaics.
- UI rewrite regresses hidden behaviours → keep `main_window.py` until parity; journey tests.
- PyInstaller bundle bloat with new deps → CPU-only torch; measure size each build.
- SAM mode untestable in CI (375 MB checkpoint) → unit-test the post-filter with fake masks; manual validation on a dev box.
- Context loss between sessions → scribe cadence; `SESSION_STATE.md` is mandatory.

---

## 11. Definition of "v3.0.0 shippable"

All R1–R13 acceptance criteria met; RELEASE_CHECKLIST ticked; user has seen screenshots of every page and the sample XLSX/PPTX; installer built by CI installs and runs on a clean Windows machine.
