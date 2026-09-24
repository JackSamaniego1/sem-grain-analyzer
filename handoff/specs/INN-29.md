# INN-29 — Calibration verification against a reference standard

**Owner:** detection-engineer (`core/cal_verify.py` pitch measurement) · data-architect (records + lookup) · ui-designer (dialog, status chip) · report-engineer (report stamp). **Depends on:** DET-07 (per-image calibration), DATA-01, DATA-04 (QSettings: instrument list), REP-01. **Effort:** S.

## User story
As a technician I image our certified calibration grating once a week at the magnifications we use; in 30 s the app measures the pitch, compares it with the certificate and records the check. Every report then states "Scale verified 2026-09-21, error +0.6 % (limit ±2 %), standard SN 1234".

## UX
- Settings → **Calibration standards**: add a standard (name, type `line grating` / `square grid` / `stage micrometer`, certified pitch µm, expanded uncertainty µm, certificate no., cert. expiry date).
- Tools → **Verify calibration…** dialog: pick instrument, magnification, standard; load image; app auto-measures pitch (preview overlays detected period lines); manual fallback = drag across N periods. Shows measured pitch, error %, PASS/FAIL vs tolerance (default ±2 %, editable per instrument). Save.
- Status bar chip: "Cal ✔ 2 d ago" / amber "Cal due" / red "Cal failed" for the current instrument.
- Sessions record which check applied; reports stamp verified / **not verified** (amber text, never blocking).

## Algorithm (`core/cal_verify.py`, no Qt)
1. Grayscale, apply DET-01 valid mask, Hann window, 2-D FFT magnitude; suppress DC.
2. Find the strongest peak(s); period_px = N / peak_radius (sub-pixel via parabolic fit on log-magnitude). For gratings use the peak along the dominant direction; for grids average the two orthogonal peaks.
3. Cross-check with autocorrelation along the dominant direction; disagree > 1 % → mark "low confidence", ask for manual fallback.
4. `measured_pitch_um = period_px / px_per_um(image)`; `error_pct = 100·(measured − certified)/certified`; PASS if |error| ≤ tolerance.
5. Report expanded uncertainty budget (simple RSS): certificate U, pitch-fit repeatability (std over 3 sub-images), pixel quantisation (0.5 px over measured span).

## Data model changes
- Workspace-level `calibration/standards.json` and append-only `calibration/checks.jsonl`: `{id, datetime, operator, instrument, magnification, standard_id, certified_pitch_um, measured_pitch_um, error_pct, tolerance_pct, passed, method: fft|manual, image_sha256, image_copy_path}`. The verification image is copied into `calibration/images/`.
- Settings: `instruments: [{name, tolerance_pct, check_interval_days (default 7)}]`.
- `Session manifest`: `calibration_check_id` (latest passed check for instrument + magnification within interval, else null + reason).
- `ReportModel.calibration`: `{source (metadata|manual|scale-bar), px_per_um, check: {...} | null, status}` → methods statement + cover stamp.

## References
ASTM E766 (calibrating SEM magnification); ISO 16700 (SEM image magnification calibration guidelines); ISO/IEC 17025 §6.4.13 & §6.5 (equipment records, metrological traceability).

## Acceptance tests (`tests/test_cal_verify.py`)
1. Synthetic sinusoidal grating, period 23.7 px, noise σ = 10 → measured period within 0.3 %.
2. Square grid → both axes measured; rotation 7° handled.
3. Error sign/percent correct; ±2 % tolerance boundary exact (2.0 % passes, 2.01 % fails).
4. Check record appended atomically; `checks.jsonl` never rewritten; session picks newest passing check within interval, else `status="not verified"`.
5. Expired standard certificate → check saved but flagged, report says "standard certificate expired".
6. Report cover contains verification text; offline test still passes (no new imports of network modules).
