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

### D-27 · 2026-09-25 · accepted (user) — Release hold for user feedback fixes
Context: User installed v3.0.0 locally and reported 16 UX change requests (UX-01..16) after first-use testing. Coordinator reviewed: all are backwards-compatible, no model/spec breakage, safe to include in v3.0.0 ship. Decision: **Hold release** (main not yet advanced past 8f8753c; v3.0.0 tag local only) until all UX items complete + full test suite green + GrainAnalyzer_Setup.exe rebuilt. Timeline: overnight sprint (2026-09-25 evening) — ui-designer (UX-01..06, UX-08..11), report-engineer (UX-12..16), detection-engineer (UX-07 already done b0437c1). By 2026-09-25 morning: all done, reviewed, suite green, installer rebuilt, main ff'd, tag v3.0.0 re-tagged locally. Then: user test-installs exe locally and pushes if OK. Consequences: v3.0.0 ships with better UX (settings order, pre-analysis validation, multi-lot reports, editable charts, custom palettes); FND-04 GitHub auth is resolved (JackSamaniego1 credential in place).

### D-28 · 2026-09-25 · accepted (user) — Lot Summary sheet: default ON, Excel-only
Context: FB-02 implemented new "Lot Summary" sheet (per-lot distribution chart + stats block) in Excel reports after Overview. User feedback during v3.0.0 review: feature approved. Decision: **Lot Summary sheet enabled by default** (report section toggle `lot_summary` default true); **Excel-only for v3.0.0** (PPTX counterpart deferred to v3.1+). Old report.json files backfilled with new section. Consequences: multi-lot reports ship with per-lot summary by default; users can toggle off in report designer if not needed; PPTX lot summary is a future feature.

### D-29 · 2026-09-25 · accepted — v3.0.0 published; no more retagging
Context: v3.0.0 tag (annotated, on commit 5e99278) pushed to origin (GitHub). From this point, v3.0.0 is public and immutable. Any further fixes or features must be released as v3.0.1, v3.1.0, etc. (semantic versioning). Decision: **No retagging of v3.0.0 after push.** If a bug is found post-release, create a new version tag and release. Consequences: GitHub Release v3.0.0 is stable; changelog and version.py must advance for next release; handoff board removes any "retag" tasks for v3.0.0.

### D-30 · 2026-09-29 · accepted — Report design: Option A (modified) spec
Context: REP-DESIGN-01 (report redesign). User reviewed 4 mockup options (artifact MiCcw4BV5AgFyfUNEQ4Y52) for PPTX summary slide (slide 2) and long-table pagination. User selected **Option A (modified)** with revised mockup v2, awaiting final approval. Decision: **Implement Option A (modified) summary slide** per spec:
  - **PPTX slide 2 (summary)**: single table with ONE ROW PER PART (averaged over all lots in the part). Columns: Part, Lots (count of lots), Images (total images across the part's lots), ASTM G ± 95% CI, mean diameter ± SD, mean area ± SD. NO Grains column. NO KPI tiles at top. NO "higher = finer" hint text. NO red row highlighting.
  - **Three bar charts**: G by part, diameter by part, area by part. One bar per PART. x-axis title "Part Number" with part names as tick labels.
  - **Per-image data table slides**: restyled in same table format as summary. Max 14 rows per slide with "(continued n/m)" and repeated header. All existing columns kept; Median area and ASTM G added. NO red rows.
  - **Long-table pagination rule**: max 14 rows per slide, break at part boundaries, truncate part/lot/image names >14 chars (applied to both Excel and PPTX).
Consequences: v3.0.1 implementation via report-engineer with tests; python-pptx XML injection for bar charts; Excel-only Lot Summary pagination matching. **Still open: user's Excel complaints** (asked which sheet/column issues matter most; no answer yet). Next: implement after final mockup approval.

### D-31 · 2026-09-29 · accepted (user) — Item 15 (Lot summary charts): build without wait for mock-up approval
Context: UPDATE 4 item 15 requests lot-vs-lot charts (combo bar+line). User feedback 2026-09-29: "do NOT wait for mock-up approval — build it; the user reviews it when testing the new report." Decision: **Item 15 is unblocked and ready for coding.** Lot summary charts (bar+trendline per lot, plus job summary) implemented with tests; user approves during testing. Consequences: faster dispatch to report-engineer; no mock-up gate; acceptance via user's test workflow.

### D-32 · 2026-09-29 · accepted (user) — Item 10b (GPU/CUDA): graphics card is NVIDIA
Context: UPDATE 4 item 10b depends on GPU confirmation. User feedback 2026-09-29: work PC has **NVIDIA GPU**. Decision: **Use CUDA torch build** (not DirectML). Installer is ~2.5 GB larger; build-engineer adds CUDA torch to build.yml and requirements. Consequences: GrainAnalyzer_Setup.exe will be ~2.5 GB (up from 644 MB); item 10b unblocked for GPU-mode implementation.

### D-33 · 2026-09-29 · accepted (user) — Item 4 sample images supplied; copy to scratch/real_sem/
Context: UPDATE 4 item 4 (OCR of scale-bar label) requires real SEM images for tuning RapidOCR. User supplied two images 2026-09-29: (1) JEOL 1280×1024 with black bar bottom ~64 px, "100nm JEOL" label, small solid scale rectangle; (2) Thermo Phenom-style 1080×717 with tick-marked scale "15 µm". Decision: **Copy both files to C:\Users\saman\GRAIN ANALYSIS TOOL\scratch\real_sem\.** detection-engineer tunes RapidOCR on these images for item 4 implementation. Consequences: item 4 (OCR) unblocked; real-image validation test data in place.

### D-34 · 2026-09-30 · accepted (user) — Keep BOTH opacity controls
Context: UPDATE 4 item 9 (overlay opacity slider in top-right pill) overlaps with existing Analyze settings opacity control. User decision 2026-09-30: keep both (pill for quick adjustment, side-panel slider for detailed). Decision: **Retain both opacity controls.** Pill and slider share the same value (persisted). Consequences: users have UI option for fast or detailed opacity adjustment; no feature removal; new pill is Overlay view only (Analyze + Review tabs).

### D-35 · 2026-09-30 · accepted (user) — GPU option ships as SEPARATE installer file
Context: UPDATE 4 item 10b (GPU/CUDA support). CUDA torch 2.5 GB wheel adds ~2.5 GB to the installer (total ~3.2 GB). User decision 2026-09-30: ship GPU as optional extra. Decision: **GrainAnalyzer_GPU_Pack.exe is a separate file on GitHub Release** (alongside main GrainAnalyzer_Setup.exe). Main installer: asks "Install GPU acceleration?" → auto-detects pack file in $EXEDIR (same folder as setup exe); if not found, offers Browse button to locate the file. Base installer shows notice when replacing a prior GPU-pack install. Consequences: users choose GPU support at install time; 644 MB base installer stays lean; larger pack is opt-in for work PCs with NVIDIA GPUs.

### D-36 · 2026-09-30 · accepted (user) — App is "universal": GPU if available, CPU otherwise
Context: UPDATE 4 item 10b design for flexibility. User decision 2026-09-30: one app works everywhere. Decision: **Single GrainAnalyzer executable runs on any PC.** Uses NVIDIA GPU when available and working (via `core/ai_device.py ai_devices()` + resolve_device). On GPU unavailable, GPU OOM, or GPU fault → falls back to CPU automatically (no user intervention). DetectionParams.sam_device="auto" (default). Results record ai_device / ai_device_fallback / ai_device_note for audit. Consequences: installer choice (GPU pack present/absent) determines whether GPU is available; app auto-adapts at runtime; no CPU-only vs GPU-only build distinction.

### D-37 · 2026-09-30 · accepted (user) — JEOL 110–135 mm check stays as is
Context: UPDATE 4 item 4 (OCR) performs scale-bar text reading + cross-checks against metadata (when both exist). User supplied JEOL image at 100 nm; no images at other magnifications. User decision 2026-09-30: no multi-magnification testing needed. Decision: **JEOL 110–135 mm dynamic range check stays as is** in core/scale_bar.py find_scale_bar_candidates. On mismatch with metadata, app asks user to confirm (never silently wrong). Consequences: no additional validation code; JEOL images keep existing safeguard; real-world testing can refine if more magnifications are supplied later.

### D-38 · 2026-09-30 · accepted — Adaptive SAM point skipping rejected
Context: UPDATE 4 item 12 (speed optimisation). Fable investigated dynamically skipping SAM detection points based on local grain density. Early prototype: adaptive skip reduced detection time <5% but changed grain count by -8 grains and diameter by +2% vs standard run on same image. Decision: **reject adaptive SAM skipping.** Results must be reproducible and stable across runs. Consequences: no lossy detection speed-up via point skipping; UI-side optimisation (overlay, grain edits, batch overhead) remains only option for item 12 UI work (deferred per user).

### D-39 · 2026-09-30 · accepted — Out-of-date result rule: flag + exclude, never delete
Context: UPDATE 4 item 5 (Resolution Profiles). When user manually changes scale or scan area (or applies a profile that differs from current), previously-analysed images' results become stale. Three options: (1) auto-delete stale results, (2) flag but keep, (3) user manually re-analyse. Decision: **flag stale results "Needs re-analysis — scale changed" and exclude from reports/exports, but DO NOT delete.** Results survive session reopen for scale changes (user may have saved intentionally; undo/re-analyse makes current). Hand changes clear profile label. Consequences: conservative approach (never lose data); users see stale state in UI; exports are correct (include only current results).

### D-40 · 2026-09-30 · accepted — Garbage collection runs only on the UI thread
Context: UPDATE 4 item 7 stability issue (crash during analysis on weak CPUs). Root cause (d880550): Python's cycle garbage collector ran on Qt worker thread → destroyed Qt objects owned by UI thread (AppState + QTimers live in reference cycles) → pending timer events → access violation. Decision: **disable automatic GC (`gc.disable()`), run threshold collection every 500 ms on UI thread only (ui/gc_guard.py timer).** core/ai_device.release_gpu_memory no longer calls gc.collect() on analysis thread. Consequences: eliminates hard crash on weak PCs during analysis; GC latency isolated to UI thread; no user-visible change.

### D-41 · 2026-09-30 · accepted — Lighter review cadence was one-off per user request
Context: UPDATE 4 batch 3 (items 5, 7, 8, 10b, 11, 12, 15) ran under lighter code-review cadence (per-step review + full suite once at end, not after every commit) per user request 2026-09-30. Decision: **this cadence is a one-off exception.** Standard process resumes: per-task agent work → code-reviewer → commit → save-handoff → next task. Lighter cadence suitable only when batch is small and low-risk (familiar agents, well-tested components). Consequences: next UPDATE 5 or feature batch returns to strict per-task review.

### D-42 · 2026-09-30 · accepted (user) — Lot Summary trendline: join, no fitting
Context: UPDATE 4 item 15 (Lot Summary charts) implemented bar charts and trendlines in Excel and PowerPoint. Initial design included curve-fitting (polynomial, exponential, linear). User decision 2026-09-30: no fitting of any kind. Decision: **Lot Summary "trendline" is a continuous line joining the lot values in lot order.** Single line per chart; omitted when fewer than two lots have a value. Applied to Excel, PowerPoint, and on-screen preview. One-row-per-lot layout already satisfied. Consequences: trendlines are now simple piecewise-linear; no curve-fit computation; v3.0.1+ reports ship with joined-value trendlines only.

### D-43 · 2026-10-01 · accepted (user) — Scale read from image is auto-applied app-wide
Context: UPDATE 4 batch 4B (dae53e3) implemented scale-bar text reading (RapidOCR) with auto-apply on step 2 ("All images"/"Current image"). Original constraint was "never auto-apply an unconfirmed label". User decision 2026-10-01: auto-apply is OK. Decision: **Scale label read from the image is auto-applied on Analyze step 2** ("Set scale bar"). Safeguard is the read-back strip showing "Scale bar: 10 µm · 160 px → 16 px/µm" with origin tags (Read from image / From file metadata / Set by hand) + Edit… button. Auto-apply is NOT applied if it contradicts file metadata; falls back to length entry if text unreadable. Consequences: faster workflow for users; Edit… button allows override; audit trail visible; v3.0.2+ may add confidence scoring.

### D-44 · 2026-10-01 · accepted (user) — American spelling in all user-visible text
Context: UPDATE 4 batch 4B (dae53e3) standardized spelling across UI strings and core/data error messages. User feedback: lab operates in US context. Decision: **All user-visible text (dialogs, buttons, error messages, report headings, tutorials, tooltips) use American spelling** (color not colour, analyze not analyse, meter not metre, etc.). Core messages also converted. Consequences: consistent terminology; no mixing of en_GB and en_US; re-exports and printouts uniform.

### D-45 · 2026-10-01 · accepted (user) — Tutorial structure: post-analysis Review tools + Reports showcase
Context: UPDATE 4 batch 4B (dae53e3) redesigned the tour from initial 11 steps to 24 action-driven cards. User feedback during v3.0.0 testing: users often skip the wizard; need to teach Review editing and Reports export. Decision: **Tour flow is (1) session setup, (2) Analyze step wizard + analysis run, (3) auto-advance to Review page and teach ALL edit tools (select, merge, cut/split, add grain, delete, undo/redo, opacity, overlay, filters) via callout cards, (4) switch to Reports page and showcase Excel/PowerPoint export with custom palette/chart editing.** No Next/Back buttons (Skip only). "Got it" button only on passive cards (no expected user action); action cards auto-advance on control interaction (keyboard shortcut or tool click). Consequences: users learn by doing; tutorial reinforces the app's core workflows; tutorial is non-blocking (can be skipped at any time).

### D-46 · 2026-10-01 · accepted (user) — PowerPoint deck restructure: per-part summary + per-part distributions
Context: UPDATE 4 batch 4D (edf0465) restructured PPTX report based on user feedback ("reports are very messy; tables overflow"). Decision: **PowerPoint report slide order and content**:
  - **Slide 2 (Grain Size Summary)**: Renamed to "Grain Size Summary" (was already present); remains unchanged.
  - **NEW: Slide 3+ (Lot Summary slides)**: ONE slide per PART, named "Lot Summary — <part name>". Content: 2×2 grid of stat panels (ASTM G ± 95% CI, mean diameter ± SD, mean area ± SD, grain density = total grains ÷ total scan area). Stacked bar chart per lot (bars side-by-side). No trendlines. Legend shows lots. Omitted if part has <2 lots.
  - **NEXT: (Grain Distributions — <part>)**: ONE slide per PART. Bars coloured by lot; legend shows lot names. Separate slides per part (no combined distribution slide on PPTX). Bars grouped by diameter/area/G, no trendlines. Omitted if part has no lot data.
  - **Per-image data tables**: Restyled; max 14 rows per slide with "(continued n/m)" + repeated header. Truncate names >14 chars.
  - **REMOVED**: Methods sheet, Parameters appendix, two-table lot comparison slide, D10/D50/D90 explainer, page-number footer (page number only on bottom right, plain text).
Consequences: PPTX is now more compact and readable; per-part summary removes clutter; fewer slides; Excel Lot Summary sheet (unchanged) remains separate.

### D-47 · 2026-10-01 · status = active — GPU pack trial build environment status
Context: UPDATE 4 item 10b (GPU/CUDA support). Build-engineer assembled a trial GPU pack on 2026-09-30 (C:\ga_gpu_stage\GrainAnalyzer_GPU_Pack.exe 1.39 GB) against an older bundle (Sep 25 dist\). This version was NOT built via BUILD_WINDOWS.bat gpu and was not tested in the app. NSIS 3.12 is now installed on dev PC. Decision: **Trial pack must be REBUILT from final source using BUILD_WINDOWS.bat gpu before any on-device testing.** This ensures: (1) final source code in the bundle, (2) correct torch version matching final app, (3) final THIRD_PARTY_LICENSES.txt included in pack. Staging folder C:\ga_gpu_stage contains ~11 GB residual; can be purged after rebuild confirms pack boots. Consequences: real testing can proceed only with fresh rebuild; timing uncertain pending build-engineer action; GPU ship-out blockers remain (owner NVIDIA sign-off, on-device SAM test, RTX 50xx fallback check).

### D-38 · 2026-09-30 · accepted — Analyze right sidebar becomes a tiered step wizard
Context: UPDATE 4 batch 4 workstream A (UX overhaul). User manual testing revealed current sidebar (Run at top, scale/scan, filters, modes, params, overlay, excluded regions) is confusing for first-time users. Decision: **Rebuild Analyze right sidebar as a linear step wizard: (1) Resolution profile (optional, auto-fills steps 1–2), (2) Set scan area, (3) Set scale bar, (4) Detection mode, (5) Start analysis, (6) Progress at bottom.** Each step active only when previous done (greyed/disabled after), shows check badge when done. Profile selector auto-fills scan area and scale. Edit buttons open existing dialogs. Detection modes get an Advanced disclosure. Excluded-regions section and overlay opacity slider removed from sidebar (canvas pill stays per D-34). Under-image SetupTile becomes read-only details strip: scan rect, scale, image metadata (mag/kV/WD from item 11). Consequences: linear goal progression for new users; existing expert workflow (Edit buttons still available) preserved; navigation follows step completion.

### D-39 · 2026-09-30 · accepted — Tutorial is action-driven (auto-advance, no Next/Back)
Context: UPDATE 4 batch 4 workstream B. Existing tour has Next/Back buttons. User feedback: add bundled Tutorial sample job with 3 synthetic SEM images; tour should point at controls and auto-advance when user acts (not on timer). Decision: **Tour rebuilt: each step points at exactly one control and advances automatically on that action.** Bundled assets/tutorial/ has 3 synthetic SEM images (JEOL-style 10 µm scale) auto-copied to a "Tutorial" job on first tour start; steps advance via AppState signals (session open, scan/scale set, mode chosen, analysis done, grain selected, export done). No Next/Back buttons; only Skip and finish. Advancement is action-driven, not timer-driven. Consequences: tutorial teaches by doing; users never "stuck" on a step; Tutorial job is a normal deletable project.

### D-40 · 2026-09-30 · accepted — Launcher splash becomes an animated grain rendering
Context: UPDATE 4 batch 4 workstream C. Current splash is static QSplashScreen. Decision: **Replace with a frameless QWidget-based animated splash** (Voronoi polycrystal with ~60 grains, boundaries drawing in over 1.2 s, subtle per-grain shading, app name/version, thin progress line, all QPainter-drawn). Pure QPainter, no media files, no network. Respects GRAIN_REDUCED_MOTION env var (static final frame). Splash runs while main window loads; closes when window ready and animation completes at least one pass. No startup delay. Consequences: launcher splash is branded and dynamic; zero new dependencies; offline constraint met; users see app branding during boot.

### D-41 · 2026-09-30 · accepted — Tutorial sample images are SYNTHETIC (no confidentiality risk)
Context: UPDATE 4 batch 4 workstream B needs bundled images for the tutorial job. Options: (1) real user SEM images, (2) synthetic generated. User decided: synthetic. Decision: **assets/tutorial/ images are generated by tools/make_tutorial_images.py using conftest synthetic mosaic generator + fake JEOL-style info bar with 10 µm scale.** Not real SEM data. Regenerate-once script (checked in, not auto-run). Consequences: no confidentiality review needed; consistent tutorial across installs; auto-find works predictably on synthetic bar; user can supply real images later if desired.


