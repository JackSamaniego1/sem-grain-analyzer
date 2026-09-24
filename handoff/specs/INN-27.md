# INN-27 — Sample statistics & E112 measurement uncertainty

**Owner:** detection-engineer (`core/metrics.sample_statistics`) · data-architect (field collection per lot) · report-engineer (sections). UI hook: ui-designer (small; Results dashboard). **Depends on:** DET-04 (INN-26 preferred), DATA-01, DATA-03, REP-01, REP-03, REP-04. **Effort:** S.

## User story
As a technician I image 5–8 fields of a lot; the app reports one lot result — "G 7.4 ± 0.2 (95 % CI), %RA 3.1 %, 6 of 5 required fields" — and tells me if I need more fields.

## UX
- Projects page: selecting a **Lot** shows a "Lot result" card: mean G, 95 % CI, %RA, fields n/required, mean ECD ± CI (µm), and a status chip (green adequate / amber "need ≥ k more fields" / grey "uncalibrated").
- Field table under the card: one row per analysed image (thumbnail, G, grains, valid area %) with an **Include** checkbox (exclusion requires a reason; logged).
- Settings/preset: `required_fields` (default 5), `target_RA_pct` (default 10).

## Definitions
- **Field** = one analysed `ImageRecord` (latest analysis). **Scope** = all included fields in all sessions of a Lot (default) or a single Session (selectable).
- Per-field value x_i = G (primary method from INN-26) and, separately, mean ECD.
- n, x̄, s (ddof=1), `t = scipy.stats.t.ppf(0.975, n−1)`, `CI95 = t·s/√n`, `%RA = 100·CI95/x̄` (compute %RA on ℓ̄ or N_A — the E112 practice — and report G ± CI by converting the CI bounds of ℓ̄/N_A through the G formula; also show a direct G ± t·s_G/√n for readability).
- Fields needed estimate: `n_req = ceil((t·s / (RA_target/100 · x̄))²)`, iterated because t depends on n; show max(required_fields, n_req).
- n < 2 → CI "n/a", status amber. Outlier hint: field with |x_i − x̄| > 2.5 s flagged (not auto-excluded).
- Pooled distribution (all grains of included fields) feeds INN-37 D10/D50/D90.

## Data model changes
- `data/models.py`: `ImageRecord.included: bool = True`, `exclusion_reason: str | None`.
- New `core/metrics.SampleStatistics` dataclass: `{scope, n_fields, required_fields, G_mean, G_ci95, G_ci_low, G_ci_high, RA_pct, ecd_mean_um, ecd_ci95_um, n_needed, adequate, outlier_field_ids, method}`; pure function `sample_statistics(field_results, cfg) -> SampleStatistics`.
- `catalog.py`: query `fields_for_lot(lot_id)` returning latest results per image.
- `ReportModel`: section `sample_statistics` (per-lot table); Excel **Overview** gets a lot summary block above the per-image table (R6); PPTX summary slide shows "G ± CI" big-number tile.

## References
ASTM E112 (statistical analysis: 95 % CI with Student t, %RA; ≥ 5 fields, %RA ≤ 10 % generally acceptable); ASTM E1382 (field-to-field statistics).

## Acceptance tests (`tests/test_sample_statistics.py`)
1. Values [7.0, 7.2, 7.4, 7.6, 7.8] → mean 7.4, s 0.3162, t 2.776, CI 0.3926 (±1e-3).
2. n = 1 → CI None, `adequate False`, status "need ≥ 4 more fields" with required 5.
3. Excluding a field changes n and is written to the audit log (INN-07 if present, else manifest note).
4. `n_needed` for s/x̄ = 0.1, target 10 % → ≥ 6 (t-iteration converges).
5. Lot with sessions A and B pools both; Session scope uses only one.
6. XLSX Overview contains lot block with CI and %RA cells; number format 0.00; PPTX tile text "±".
