# UPDATE 4 item 7 — stability during analysis: diagnosis and fix plan (2026-09-30)

Read-only investigation (opus). Nothing implemented yet. Line numbers drift; function names are the anchor.

## Defects, worst first
1. `ui/workers.py` (~433, 357-363) `AnalysisQueue`: Cancel in the 30 ms gap between images lets the queue finish and a new run start; the old `QTimer.singleShot(30, _next)` then starts a second worker thread over the first. First `QThread` loses its reference while running → Qt aborts. Likelier on slow PCs. Confirmed.
2. `ui/app_shell.py` (~904-916) close: waits 20 s then continues even if the thread still runs (SAM on CPU can take longer) → "thread destroyed while running" abort. Confirmed.
3. No `sys.excepthook`, `threading.excepthook` or `faulthandler`; build is `console=False` → crashes die silently, no log. Confirmed.
4. `open_session` (`app_shell.py` ~519) has no running-analysis guard; `AppState.close_session` does not cancel the queue; `flush()` (app_state ~2344) blocks up to 3 × 15 s and its `processEvents()` delivers results during teardown. Confirmed.
5. Stale undo: `AppState.set_result` (~1818) wipes hand edits but leaves `GrainGeometryCommand`s for that image on the undo stack (~769); undo after re-analysis restores old detection; an edit made while the image is running is lost when the result arrives. Data loss. Confirmed.
6. Thread oversubscription: `set_filter_options`/`refilter`/Auto-find queue one task per image on the global pool (= logical cores), each reading full-res pixels; OpenCV, torch, OCR each default to all cores; no thread limits anywhere. Confirmed.
7. Grain edits on the UI thread: `layers.py` (~63-79) `overlay_layer` makes several whole-image copies (~300 MB at 12 MP) per show/edit; merge/split do whole-image scans there. Freezes; `MemoryError` can leave canvas half-updated. Suspected.
8. `_record_payload` (~2296) makes one wasted full-size label copy per save. Confirmed.
9. `pack_result` (`core/result_pack.py` ~93-100) sets arrays to `None` one by one before marking packed → reader can see `label_image=None`. Suspected, low risk.
10. `workers.py` (~450): `_SHUTTING_DOWN` set on close but `_deliver_done` never checks it. Confirmed.

## Fix plan
### (a) Core / workers
- `AnalysisQueue`: `_gen` counter; `_next(gen)` returns early if generation changed; never overwrite `_thread` while one is running.
- Start analysis thread and pool threads at `QThread.LowPriority`.
- New `core/perf.py` `configure_threads()`: OpenCV threads = cores − 1, torch = max(1, physical cores − 1), OCR engine 1–2 threads. Must not change results.
- `pack_result` atomic: build packed dict first, then swap attributes.
- Check `_SHUTTING_DOWN` in `_deliver_*`.
- Remove the wasted label copy in `_record_payload`.

### (b) UI
- ONE gate: `AppState.analysis_lock` with `is_active()`, `guard(action_name) -> bool`, `lock_changed` signal; driven by `busy_changed`.
- Guard checked inside: `delete_grains`, `restore_grains`, `merge_grains`, `split_grain`, (new) add-grain, `set_filter_options`, `apply_filters_to_all`, `set_scan_rect`, `set_calibration`, `auto_setup`, `remove_images`, `open_session`/`close_session`; undo/redo via new `state.undo()`/`state.redo()` wrappers that call the guard.
- Still allowed during analysis: pan, zoom, display mode, opacity, switching image, hover, selection, opening Review.
- Dialog: shown once per run — "Continue anyway" (holds for the current run) and "Don't warn again" (saved as `AppSettings.warn_edit_during_analysis` in `%LOCALAPPDATA%\GrainAnalyzer\settings.json`; can be re-enabled in Settings). Mention low-performance PCs in plain words.
- Always blocked even after "Continue anyway": editing an image that is queued or running, starting another analysis, opening/closing a session, removing images that are in the batch.
- Undo stack: `set_result` removes that image's commands.
- Close: "Analysis running — stop and close?", then wait in a loop with no fixed time limit (cancel check) instead of 20 s.
- Crash log: `faulthandler` + both excepthooks → log file in `%LOCALAPPDATA%\GrainAnalyzer`, friendly message. No upload (offline rule).
- Load: filter/Auto-find tasks on a small dedicated pool (1–2 threads); build overlay off the UI thread.

## Tests (pytest-qt, headless)
`test_queue_stale_timer` (cancel + restart in the gap → only one thread ever running), `test_close_waits`, `test_lock_blocks_edits` (each gated method; both dialog answers), `test_pan_zoom_view_allowed`, `test_open_session_blocked`, `test_undo_after_reanalysis`, `test_excepthook_logs`, `test_perf_thread_caps`, and a stress test: monkeypatch `GrainDetector.analyze` with a slow fake; while 5 images run, fire random hover / `grain_at` / view + opacity changes / image switches / delete + undo with "Continue anyway" / a filter change via `QTimer`; assert no exception, all 5 results arrive, undo stack consistent, UI thread never stalls > 200 ms.
