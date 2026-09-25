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

### D-15 · 2026-09-23 · accepted — Defense-in-depth for offline
Context: in-process socket guard cannot see sockets opened by native C/C++ libraries (e.g., OpenCV, ONNX). Decision: NSIS installer adds Windows Firewall inbound + outbound block rules for GrainAnalyzer.exe (removed on uninstall); non-fatal if firewall is centrally managed by IT. Consequences: two-layer protection (code + OS); offline guarantee survives even if a native dep leaks a socket.

### D-16 · 2026-09-23 · accepted — Custom agents in .claude/agents registered at session start
Context: `.claude/agents/` defines scribe, detection-engineer, data-architect, etc. as agent types; this session used fallback (general-purpose + "ROLE: read .claude/agents/scribe.md"). Decision: next session will register these agents at startup; `subagent_type` parameter in Agent calls will work directly. Consequences: cleaner prompt syntax; agents inherit model pinning from `.md` frontmatter.

### D-17 · 2026-09-24 · accepted — UI-11 calibration canvas pan shortcut
Context: UI-11 added scale-bar calibration modes (Rectangle, Level line, Free line); calibration canvas needs pan for large images. Ctrl+drag was the natural shortcut but conflicts with multi-select in Review canvas; Alt+drag was natural for pan but needed for Alt-disables-snapping in calibration canvas. Decision: use **middle-mouse-drag and right-drag** for pan in calibration canvas (no modifier). Consequences: consistent with desktop map convention; snapping toggled via Alt without interference; no conflict with future UI-05 multi-select.

### D-18 · 2026-09-24 · accepted — UI-11 last-used calibration mode persistence
Context: UI-11 offers three calibration modes (Rectangle, Level line, Free line; default Level). User may prefer one for their workflow. Decision: persist the last-used mode in `calibration_ui.json` (alongside `settings.json`, separate from session data) and restore it on next calibration dialog open. Consequences: faster workflow; non-destructive (mode always overridable); survives app restart.

### D-19 · 2026-09-24 · accepted — core/scale_bar.py kernel width fix
Context: Line-detection auto-calibration in UI-11 uses a convolution kernel for edge detection; measured bar length was correct but auto-detected bar position was 1 px off to the right. Decision: change kernel width 20→21 (odd width ensures symmetric padding and centered filter response). Consequences: auto-detected bar position now matches measured length; no API change; previous bar detections unaffected (mode is re-detectable).

### D-20 · 2026-09-24 · accepted (user) — Spec limits and calibration check optional
Context: INN-02 (spec verdict badges) and INN-29 (calibration verification) are optional features; users may not have specs defined or reference standards available. Decision: **default both OFF with zero user nags.** `data/specs.py` verdict only computed when a spec is defined; `AppSettings.calibration_verification_enabled=False` by default; Settings controls let users enable if needed. Report badges only appear when spec exists. Consequences: zero cognitive load for users without these features; clean onboarding for v3.0.0.

### D-21 · 2026-09-24 · accepted (user) — INN-30 (approval/sign-off) cancelled
Context: INN-30 proposed report approval workflow with SHA-256 sealed sign-off (local only). User feedback 2026-09-24: "not needed for v3.0.0". Decision: **cancel INN-30.** Mark task `cancelled` on task board; do not schedule UI for data/approval.py; remove from Next lists. Consequences: simpler v3.0.0 scope; sign-off deferrable to v3.1+ if customer demand emerges.

### D-22 · 2026-09-24 · accepted (user) — Overlay export shows full original image
Context: v2 overlay export showed only the cropped scan area when auto-crop was active. User feedback 2026-09-24: "exported overlays must show the full original image incl. SEM info bar." Decision: **compose_full_overlay in core/overlay_compose.py returns full original frame** with thin dashed outline around the measured region; SEM info bar and unscanned areas untouched. Consequences: exports are context-rich; operators can see which area was actually measured; info bar preserved for instrument metadata.

### D-23 · 2026-09-24 · accepted — Calibration verification: newer failed supersedes older pass
Context: INN-29 (calibration_verify.py) may find that a calibration passes one check then fails a later check (e.g. drift detected). Coordinator pattern: newer result wins. Decision: **in cal_records.py, a failed check record supersedes an older passed record for the same calibration ID.** Flagged in UI as "FAILED" (red chip) not "PASSED then FAILED". Consequences: audit trail shows the ultimate verdict; no ambiguity in report rendering.

### D-25 · 2026-09-24 · accepted (user) — PowerPoint report bugs parked (then resumed)
Context: FIX-11, FIX-12, FIX-13 are PPTX rendering issues (title overlap, table overflow, chart bars vs line). Found during boss deck build; reported as high priority. User feedback 2026-09-24 (morning): "do that later". Later feedback 2026-09-24 (evening): user approved resuming PPTX work. Decision: **PPTX work resumed; FIX-11/12/13 and FIX-07 PPTX render completed in commit 21e6d67.** Consequences: PPTX reports now include calibration field and layout fixes; v3.0.0 release includes full PPTX support.

### D-26 · 2026-09-24 · accepted (user) — Idea triage: user decisions
Context: Innovator INN-run #2 (2026-09-24) generated 11 new ideas (INN-41…INN-51) focused on workflow and measurement trust; coordinator added to backlog for user review. User feedback 2026-09-24: only one idea approved. Decision: **ONLY INN-43 (lot comparison matrix + equivalence testing) approved for Phase 5.** INN-41, INN-42, INN-44, INN-45, INN-46, INN-47, INN-48, INN-49, INN-50, INN-51 **declined** — mark in 07_IDEAS_BACKLOG.md as archived/declined. Consequences: Phase 5 sprint is lean (INN-43 only after UI-05 complete); user noted "will fix GitHub auth tonight (FND-04)" and "will supply real SEM images later (D-13)"; user wants release ASAP. Next: finish INN-43 UI → merge to main → tag v3.0.0 locally → push when auth is fixed.


