# INN-26 — ASTM E112 / E1382 compliance engine

**Owner:** detection-engineer (core) · report-engineer (report section) · **Depends on:** DET-01, DET-02, DET-03, DET-04 (extends it; implement together or right after), REP-01, REP-03. **Effort:** M.

## User story
As a lab technician, the ASTM grain size number the app reports is the number an auditor would get by hand with the E112 procedures — and the report shows *which* procedure, which counting rules and whether the measurement was adequate.

## UX
- Analyze panel → "ASTM method" dropdown: `Planimetric (Jeffries)` · `Intercept – lines` · `Intercept – three circles` · `Both (default)`.
- Review page: toggle "Show test pattern" draws the intercept lines/circles and marks each counted hit (full = cyan dot, half = amber dot) on the overlay.
- Results: card "ASTM G 7.4 (planimetric) / 7.3 (intercept)" + an **Adequacy** chip listing failed checks (e.g. "38 grains < 50 in field").

## Algorithms (all in `core/metrics.py`, no Qt, operate on the label image + valid mask from DET-01)
- **Implementation note:** code lives in `core/astm.py` (not core/metrics.py). Border tolerance `border_tol_px=2` classifies grains within 2 px of the field edge as intercepted (conservative); stated in Methods.
- **Planimetric (Jeffries):** within the valid analysed area A (mm² at 1×, from calibration): `N_inside` = grains wholly inside, `N_intercepted` = grains touching the scan/valid-area border (counted ½). `N_A = (N_inside + 0.5·N_intercepted)/A`; `G = 3.321928·log10(N_A) − 2.954`.
- **Intercept (Heyn / E1382 style):** test lines at 0°, 45°, 90°, 135° spaced ≥ 2× the mean ECD, or three concentric circles (circumference ratio as in E112 three-circle pattern) centred in the valid area. Walk each line through the label image; only segments inside the valid mask count toward length L. A hit is a transition between two different grain labels (a boundary run ≤ `boundary_px` wide counts once). Rules: line end inside a grain = ½; tangent contact = 1 (ASTM E112-13 §13.3.3 — corrected 2026-09-23 after review; implementation constant `TANGENT_WEIGHT` in core/astm.py); triple-junction hit (≥3 labels within the boundary run) = 1.5. `P_L = hits/L`, `ℓ̄ = 1/P_L` (mm); `G = −6.643856·log10(ℓ̄) − 3.288`.
- **E1382 field statistics:** per field report ℓ̄, s(ℓ), number of intercepts, and the individual intercept lengths (for INN-37 distributions).
- **Adequacy checklist** (warnings, never silent): ≥ 50 grains per field (planimetric) or ≥ 50 intercepts per field (intercept); valid area ≥ 50 % of frame; calibration present; grains touching valid-mask border not counted as whole.
- Twins: not handled (INN-34 archived) → methods text states "twin boundaries not distinguished".

## Data model changes
- `AnalysisResult.astm`: `{method, G_planimetric, N_A_per_mm2, N_inside, N_intercepted, G_intercept, mean_intercept_um, P_L_per_mm, n_hits, test_length_um, pattern, adequacy: [{check, passed, detail}]}`; serialised in `grains.json` (schema bump).
- `DetectionParams.astm_method` (enum), `astm_pattern_spacing_factor` (default 2.0).
- `ReportModel` gains section `astm_compliance` (table + adequacy list + formula legend).

## Acceptance tests (`tests/test_astm.py`)
1. Formula anchors: `N_A = 7.75 mm⁻²` → G = 0.00 ± 0.01; `N_A = 1984` → G = 8.00 ± 0.01; `ℓ̄ = 0.32 mm` → G = 0.00 ± 0.02; `ℓ̄ = 0.020 mm` → G = 8.00 ± 0.02.
2. Synthetic square grid of known pitch d: 0°/90° lines only → `ℓ̄ = d` within 2 %; all four orientations → `ℓ̄ = 2d/(1+√2) ≈ 0.828 d` within 2 %; hex/Voronoi mosaic from `make_mosaic` of known grain count: planimetric G within ±0.25 of analytic.
3. Half-count rules: a hand-built 3-grain label image asserts ½ and 1.5 hit counting.
4. Black-region fixture: test length excludes invalid area (L drops by the invalid fraction ±2 %).
5. Adequacy: 20-grain image → failed "≥ 50 grains" check present in result and in report section.
6. Overlay with pattern renders without exceptions; XLSX contains "ASTM compliance" table.
