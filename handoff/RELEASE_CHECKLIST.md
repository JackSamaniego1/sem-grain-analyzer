# Release Checklist — v3.0.0

- [ ] All Phase 1–4 gates passed; ≥3 innovator ideas shipped
- [ ] `/run-tests` green on `v3-dev`
- [x] Journey tests (REL-01) green
- [ ] `version.py` = `3.0.0`; README/GUIDE/docs updated; `CHANGELOG.md` written
- [ ] Local `pyinstaller grain_analyzer.spec --clean --noconfirm` succeeds; `dist\GrainAnalyzer\GrainAnalyzer.exe` launches; bundle size recorded
- [ ] NSIS installer builds; installs to Program Files; Start-menu + desktop shortcuts; uninstall clean
- [ ] Sample XLSX opens in Excel (tabs coloured, raw data last, charts labelled); PPTX opens in PowerPoint
- [ ] Screenshots of every page reviewed by the user
- [ ] User go for push + tag
- [ ] `git checkout main && git merge v3-dev`; `git tag v3.0.0`; `git push origin main --tags`
- [ ] CI green; Release page has `GrainAnalyzer_Setup.exe` and `GrainAnalyzer.dmg`
- [ ] `/save-handoff` with release notes
