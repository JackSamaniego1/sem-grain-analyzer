# SEM Grain Analyzer — project instructions

PyQt6 desktop app for grain detection/measurement on SEM images (Python 3.11). v3 upgrade in progress; coordinator brief and state live in `handoff/` — **read `handoff/00_START_HERE.md` then `handoff/SESSION_STATE.md` before doing anything.**

## Commands (Windows, PowerShell)
- Tests: `.venv\Scripts\python -m pytest tests -q -p no:cacheprovider`
- GUI: `.venv\Scripts\python main.py`
- Headless smoke: `/smoke-app` skill
- Build: `BUILD_WINDOWS.bat` (PyInstaller + NSIS); CI builds on tag `v*`

## Conventions
- `core/`, `data/`, `reports/` contain no Qt imports. UI lives in `ui/`.
- Every behaviour change ships with a pytest test (synthetic fixtures from `tests/conftest.py`).
- Permissive licences only (MIT/BSD/Apache/LGPL). No PyQt-Fluent-Widgets.
- Version comes from `version.py` only.
- Commit on `v3-dev`; never push until GitHub auth is fixed (`handoff/SESSION_STATE.md`).
- After each completed task run `/save-handoff` (scribe agent) — the user requires regular handoff saves.

## Team
Agents in `.claude/agents/` (models pinned; never run agents on Fable). Roster and concurrency rules: `handoff/02_TEAM_ROSTER.md`.
