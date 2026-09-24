# INN-30 — Report approval & sealed sign-off (local, no network)

**Owner:** data-architect (`data/approval.py`, hashing, revision logic) · report-engineer (approval block, DRAFT/APPROVED marks) · ui-designer (approve dialog, lock state). **Depends on:** DATA-02 (session files), DATA-04 (operator name), REP-01, REP-06 (report reload); INN-07 audit trail optional but recommended. **Effort:** M.

## User story
As a reviewer I open an analysed lot, check it, and click **Approve**; my name, role, time and meaning are stored with a SHA-256 seal. Anyone opening the report later — or the customer, using `SHA256SUMS.txt` — can check that nothing changed since approval. Editing an approved report creates Revision 2 instead of silently altering Revision 1.

## UX
- Report editor header shows state chip: `DRAFT` (grey) → `REVIEWED` (blue) → `APPROVED` (green) / `SUPERSEDED` (struck-through).
- **Approve…** dialog: signer name (prefilled from Settings operator), role (Analyst / Reviewer / Approver), meaning ("Reviewed", "Approved for release"), comment, and a re-typed name/initials confirmation. Warning (not block) if approver = analyst who ran the analysis.
- Approved sessions: parameters, grain edits and report text become read-only; banner "Approved by J. Doe 2026-09-23 14:02 — Create revision to edit".
- **Verify seal** button → "Intact" (green) or "Modified since approval: grains.json, report.json" (red).
- Exports: DRAFT watermark diagonal on every XLSX sheet header/PPTX slide until approved; approved exports carry an approval block (names, roles, times, short hash 12 chars).

## Seal
- Canonical payload = sorted list of `(relative_path, sha256)` for `manifest.json`, `results/*` (labels `.npz`, `grains.json`), `report.json` (serialised with sorted keys, UTF-8, `\n`), and input images in `images/`.
- `seal = sha256("\n".join(f"{p}  {h}"))`. Stored in `approval.json`; also written as `SHA256SUMS.txt` into each export pack (INN-38).
- Honest limitation text in Help & methods: "Local integrity seal, not a cryptographic e-signature; not a 21 CFR Part 11 system." No keys, certificates or network time.

## Data model changes
- Session `approval.json`: `{revision, state, signatures: [{name, role, meaning, comment, datetime_local, datetime_utc, windows_user, seal}], superseded_by?}`.
- Revision = copy of report/results into `revisions/r{n}/` (images referenced, not duplicated); `manifest.revision` increments; catalog columns `state`, `revision`.
- `ReportModel.approval` rendered on cover/certificate (INN-40).
- Audit entries (INN-07) for approve, verify, revise.

## References
ISO/IEC 17025:2017 §7.5 (technical records, changes traceable to previous versions), §7.8.2.1(o) (identification of person authorising the report), §8.4 (control of records).

## Acceptance tests (`tests/test_approval.py`)
1. Approve → `approval.json` written atomically; seal recomputes identically on reload.
2. Modify one byte in `grains.json` → verify reports that file only.
3. Editing an approved session through the API raises `ApprovedLockedError`; `create_revision()` → r2 DRAFT, r1 `SUPERSEDED`, r1 files untouched.
4. XLSX of DRAFT contains "DRAFT" header on every sheet; APPROVED contains signer name and 12-char hash.
5. Same-person analyst/approver produces a warning flag in the approval record.
6. Catalog rebuild restores state and revision; filtering "Approved" lots works.
