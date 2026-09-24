# Decisions (ADR log)

Append-only. Format: ID · date · status · context · decision · consequences.

### D-01 · 2026-09-23 · accepted — Coordinator model and agent models
Context: user reserves Fable for planning; wants token-efficient team. Decision: coordinator = Claude Opus; agents pinned per `.claude/agents/*.md` (opus: detection-engineer, ui-designer, innovator; sonnet: data-architect, report-engineer, qa-engineer, build-engineer, code-reviewer; haiku: scribe). No agent may run on Fable. Consequences: predictable cost; coordinator must write self-contained prompts.

### D-02 · 2026-09-23 · accepted — Branching
Context: colleagues download the v2.3 installer from GitHub Releases. Decision: all v3 work on `v3-dev`; `main` unchanged until `v3.0.0` is tagged. Consequences: installer stays available; merge is a single fast-forward/merge at release.

### D-03 · 2026-09-23 · **accepted (user)** — Migrate PyQt6 → PySide6
Context: PyQt6 is GPL/commercial. User: "I want the installer to stay the same way it is now without any license — download the installer from GitHub to a flash drive, bring it to work and install." Decision: migrate to **PySide6 (LGPL v3, Qt for Python official)** — free for commercial/closed use with no licence purchase, provided Qt is dynamically linked (PyInstaller onedir ships Qt as separate replaceable DLLs — already our layout). Do it in Phase 0 (FND-06) **before** the UI rewrite so all new UI code is PySide6-native. Only permissive add-ons (MIT/BSD/Apache/LGPL); PyQt-Fluent-Widgets forbidden. Update `LICENSE.txt` third-party list and ship `THIRD_PARTY_LICENSES.txt` (incl. LGPL notice) in the installer. Consequences: installer workflow unchanged (GitHub Release → flash drive → NSIS install); `pyqtSignal`→`Signal`, `pyqtSlot`→`Slot`, `QAction` stays in QtGui, `exec()` same.

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

### D-14 · 2026-09-23 · **accepted (user) — HARD CONSTRAINT** — Fully offline, zero data egress
Context: user will install on a work machine; "never use the internet after the installation period. No data loaded into the program at any time should be leaving the computer. This is very serious." Decision:
1. **No network code at runtime.** Forbidden in app code: `socket`, `urllib`, `http.client`, `requests`, `httpx`, `aiohttp`, `ftplib`, `smtplib`, `QtNetwork`, `QtWebEngine*`, `QDesktopServices.openUrl` on http(s) URLs, `webbrowser`, torch.hub/HF hub downloads, update checks, telemetry, crash upload, cloud sync, web fonts, remote templates.
2. **Everything bundled at build time**: SAM checkpoint, icon fonts, report templates, fonts. Build scripts may download (that is "installation period"); the installed app never does. If an asset is missing, show "reinstall" — never a download link.
3. **In-process network kill-switch** (`core/offline_guard.py`, installed first thing in `main.py`): blocks outbound AF_INET/AF_INET6 connections and name resolution for the whole process, logs any attempt locally; env hardening (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `TORCH_HOME` pointed at the bundle, `QT_*` no network).
4. **Enforced by tests**: static AST scan of all first-party `.py` for forbidden imports/calls; runtime test that runs analysis + save + XLSX/PPTX export with the guard armed and a socket spy asserting zero connection attempts.
5. **Data stays local**: workspace defaults to a local folder; temp files created in the user temp dir and deleted after use (legacy exporter leaks temp PNGs — fix); logs local only; no analytics.
6. code-reviewer treats any violation as a **blocker**; innovator ideas must be offline/local-only (no auto-update, no cloud, no remote LIMS push — local file drops only).
Consequences: some ideas (auto-update check, crash reporting) are re-scoped to local-only variants.

### D-10 · defaulted — default workspace root (default: `~/Documents/GrainAnalyzer/Projects`)
### D-11 · defaulted — branding assets (default: neutral)
### D-12 · defaulted — ASTM G-number in reports (default: yes when calibrated)
### D-13 · defaulted — real SEM validation images (default: synthetic only, flagged risk)
