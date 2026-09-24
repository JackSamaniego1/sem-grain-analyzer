# Decisions (ADR log)

Append-only. Format: ID · date · status · context · decision · consequences.

### D-01 · 2026-09-23 · accepted — Coordinator model and agent models
Context: user reserves Fable for planning; wants token-efficient team. Decision: coordinator = Claude Opus; agents pinned per `.claude/agents/*.md` (opus: detection-engineer, ui-designer, innovator; sonnet: data-architect, report-engineer, qa-engineer, build-engineer, code-reviewer; haiku: scribe). No agent may run on Fable. Consequences: predictable cost; coordinator must write self-contained prompts.

### D-02 · 2026-09-23 · accepted — Branching
Context: colleagues download the v2.3 installer from GitHub Releases. Decision: all v3 work on `v3-dev`; `main` unchanged until `v3.0.0` is tagged. Consequences: installer stays available; merge is a single fast-forward/merge at release.

### D-03 · 2026-09-23 · **needs user** — PyQt6 licensing
Context: PyQt6 is GPL/commercial; closed corporate distribution may require a Riverbank licence; PySide6 is LGPL. Decision (default): keep PyQt6, use only permissive add-ons (qtawesome), forbid PyQt-Fluent-Widgets, keep code PySide6-portable. Consequences: possible later migration (`pyqtSignal`→`Signal`, enum access) is mechanical.

### D-04 · 2026-09-23 · accepted — Data layout
Decision: `<Workspace>/<Project>/<Sample>/<Lot>/<Session>/` with `manifest.json`, `images/`, `results/` (`.npz` labels, overlay png, grains.json), `report.json`, `exports/`; `catalog.sqlite` at workspace root as a rebuildable index. Files are the source of truth. Consequences: human-browsable, backup-friendly; search needs the catalog.

### D-05 · 2026-09-23 · accepted — Report stack
Decision: `ReportModel` (JSON) is the single source; `xlsxwriter` renders Excel, `python-pptx` renders PowerPoint; shared `reports/charts.py` palette. `openpyxl` retained for reading/tests only. Consequences: legacy `utils/excel_export.py` retired after parity (REP-07).

### D-06 · 2026-09-23 · accepted — Testing
Decision: pytest + pytest-qt, `QT_QPA_PLATFORM=offscreen`, procedural synthetic fixtures (`tests/conftest.py::make_mosaic`), no binary fixtures in git. SAM path tested via fake-mask post-filter unit tests. Consequences: fast CI; real-image validation is manual (D-13).

### D-07 · 2026-09-23 · accepted — Versioning
Decision: `version.py` single source; v3 starts at `3.0.0-dev`; semantic versioning; `CHANGELOG.md` kept by build-engineer.

### D-08 · 2026-09-23 · accepted — Handoff cadence
Decision: `/save-handoff` after every completed task, every decision, and before every session end; `handoff/` committed separately with `handoff:` prefix. `SESSION_STATE.md` is overwritten each time; `06_PROGRESS_LOG.md` is append-only.

### D-09 · 2026-09-23 · accepted — Detection fix strategy
Decision: single `valid_mask` computed once in `analyze()`, threaded through all pipelines; conservative default threshold exposed as `DetectionParams.invalid_intensity_threshold`; stats over valid area only. Consequences: dark-but-legitimate grains must be protected by a regression fixture (DET-08).

### D-10 · needs user — default workspace root (default: `~/Documents/GrainAnalyzer/Projects`)
### D-11 · needs user — branding assets (default: neutral)
### D-12 · needs user — ASTM G-number in reports (default: yes when calibrated)
### D-13 · needs user — real SEM validation images (default: synthetic only, flagged risk)
