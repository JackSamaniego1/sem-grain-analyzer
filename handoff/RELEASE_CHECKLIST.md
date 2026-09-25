# Release Checklist — v3.0.0

- [x] All Phase 1–4 gates passed; ≥3 innovator ideas shipped (INN-02, INN-29, INN-43)
- [x] `/run-tests` green on `v3-dev` (820 passed; UX batch complete)
- [x] Journey tests (REL-01) green (3 journeys, 641 tests)
- [x] `version.py` = `3.0.0`; README updated; `CHANGELOG.md` written (709fd7d)
- [x] Local PyInstaller succeeds; `dist\GrainAnalyzer\GrainAnalyzer.exe` launches; bundle 1.3 GB
- [x] NSIS installer builds (GrainAnalyzer_Setup.exe 644 MB, portable NSIS 3.12)
- [x] All UX items (UX-01..16) complete; backwards-compatible, no model breakage
- [x] Offline audit passed (core/offline_guard.py enforced; no egress detected)
- [ ] NSIS silent install to Program Files + start-menu/desktop shortcuts; uninstall clean (requires UAC; user to test)
- [ ] Sample XLSX opens in Excel; PPTX opens in PowerPoint (covered by REL-01 journey tests + UX-13..15)
- [ ] Screenshots of every page reviewed by the user (pending; user to review after install test)
- [x] GitHub auth fixed (FND-04); JackSamaniego1 verified with dry-run push
- [x] main fast-forwarded to v3-dev; v3.0.0 tag annotated at 709fd7d; awaiting user push
- [ ] CI green; Release page has exe and dmg (pending user push + CI trigger)
- [x] Handoff saved with all UX-01..16 status, SESSION_STATE for resume, RELEASE_CHECKLIST updated
