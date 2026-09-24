# INN-02 — Spec limits & conformity verdict (ILAC-G8 decision rule)

**Owner:** data-architect (spec storage + evaluation in `data/specs.py`, pure Python) · report-engineer (badges in XLSX/PPTX) · ui-designer (spec editor + badges). **Depends on:** INN-27 (sample statistics), DET-04, DATA-01, DATA-03, REP-01, REP-03, REP-04. **Effort:** S.

## User story
As a QA engineer I define "Alloy 718 bar: ASTM G 6.0–8.0, ALA ≤ G 3, ≥ 5 fields, %RA ≤ 10 %" once per project; every lot then shows PASS / FAIL / INCONCLUSIVE in the browser, on the Excel Overview and on the PowerPoint title slide, with the reason.

## UX
- Project settings → **Specifications** tab: table of rules (metric · lower · upper · unit), a spec name/revision, and a **Decision rule** selector: `Simple acceptance` (compare mean) or `Guarded acceptance` (compare CI bounds; default).
- A spec attaches to a Project, optionally overridden per Sample (e.g. different product form).
- Lot card and tree node show a badge: green PASS, red FAIL, amber INCONCLUSIVE, grey NO SPEC / NOT ADEQUATE. Hover lists each rule result, e.g. "G 7.4 [7.2–7.6] within 6.0–8.0 ✔".
- Badge change animates (colour cross-fade, 200 ms) — lab-appropriate, no bounce.

## Evaluation (guarded acceptance, per rule)
- Metrics available: `G_mean`, `ecd_mean_um`, `RA_pct`, `n_fields`, `ala_G` (when INN-33 lands), `invalid_area_pct_max`.
- For metrics with a CI (G, ECD): PASS if [CI_low, CI_high] ⊂ [lower, upper]; FAIL if the whole CI lies outside; otherwise INCONCLUSIVE. Simple rule uses the mean only.
- Count/limit metrics (n_fields, RA_pct): plain comparison.
- Overall = FAIL if any FAIL; else INCONCLUSIVE if any INCONCLUSIVE or data not adequate; else PASS.
- Statement text generated for the report: "Conformity decided by guarded acceptance: the 95 % confidence interval must lie within the specification (ILAC-G8:09/2019)."

## Data model changes
- `project.json` (schema bump): `specs: [{id, name, revision, decision_rule, rules: [{metric, lower, upper, unit}], applies_to: {sample_ids?}}]`.
- `data/specs.py`: `Spec`, `Rule`, `Verdict{overall, rules:[{metric, value, ci, status, text}]}`, `evaluate(spec, sample_stats) -> Verdict`.
- Catalog column `lot.verdict` (cache, rebuildable) for tree badges and filtering ("show FAIL lots").
- `ReportModel.verdict` block → XLSX Overview verdict cell with conditional format (green/red/amber) + per-image table stays unchanged; PPTX title slide badge; methods statement cites spec name + revision.

## References
ISO/IEC 17025:2017 §7.8.6 (statements of conformity must state the decision rule); ILAC-G8:09/2019 (simple vs guarded acceptance).

## Acceptance tests (`tests/test_specs.py`)
1. G 7.4 CI [7.2, 7.6], limits 6–8 → PASS; limits 7.5–9 → INCONCLUSIVE (guarded), FAIL (simple, mean 7.4 < 7.5); limits 8–9 → FAIL.
2. n_fields 3 with min 5 → overall INCONCLUSIVE with reason "fields 3 < 5".
3. Sample-level override beats project spec.
4. Spec round-trips through `project.json`; unknown metric raises a clear error.
5. XLSX: verdict cell text and fill colour asserted with openpyxl; PPTX title slide contains "PASS".
6. Catalog rebuild restores verdict badges identical to live evaluation.
