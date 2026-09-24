# SEM Grain Analyzer — project instructions

## HARD CONSTRAINTS (user decisions D-03, D-14 — violations are release blockers)
1. **Offline & private.** The installed app must never use the network and no loaded data may leave the machine. No `socket`/`urllib`/`http`/`requests`/`QtNetwork`/`QtWebEngine`/`webbrowser`, no update checks, telemetry, crash upload, cloud, web fonts, or runtime downloads. All assets (SAM checkpoint, fonts, icons, templates) are bundled at build time. Missing asset → tell the user to reinstall, never show a download link. `core/offline_guard.py` blocks the network in-process; `tests/test_offline.py` enforces it.
2. **PySide6 (LGPL), not PyQt6.** Free, no licence to buy. Use `Signal`/`Slot`. Only MIT/BSD/Apache/LGPL/OFL dependencies.
3. Installer workflow must stay: GitHub Release → `GrainAnalyzer_Setup.exe` → flash drive → install on a work PC with no internet.

---

PySide6 desktop app for grain detection/measurement on SEM images (Python 3.11). v3 upgrade in progress; coordinator brief and state live in `handoff/` — **read `handoff/00_START_HERE.md` then `handoff/SESSION_STATE.md` before doing anything.**

## Commands (Windows, PowerShell)
- Tests: `.venv\Scripts\python -m pytest tests -q -p no:cacheprovider`
- GUI: `.venv\Scripts\python main.py`
- Headless smoke: `/smoke-app` skill
- Build: `BUILD_WINDOWS.bat` (PyInstaller + NSIS); CI builds on tag `v*`

## Conventions
- `core/`, `data/`, `reports/` contain no Qt imports. UI lives in `ui/`.
- Every behaviour change ships with a pytest test (synthetic fixtures from `tests/conftest.py`).
- Permissive licences only (MIT/BSD/Apache/LGPL). No PyQt-Fluent-Widgets. New dependency → add to `THIRD_PARTY_LICENSES.txt`.
- Temp files: create in the OS temp dir and delete after use.
- Version comes from `version.py` only.
- Commit on `v3-dev`; never push until GitHub auth is fixed (`handoff/SESSION_STATE.md`).
- After each completed task run `/save-handoff` (scribe agent) — the user requires regular handoff saves.

## Context & usage discipline (user request — keep token usage low)
- **Save the handoff after EVERY commit**, not just at the end of a phase: `/save-handoff` (scribe runs on haiku — cheap). `handoff/SESSION_STATE.md` must always be good enough to resume cold.
- **Clear context only when it's big.** The `UserPromptSubmit` hook (`.claude/hooks/context_hooks.py check`) injects a "CONTEXT SIZE" notice once the live context passes 120k tokens. Only then: (1) make sure nothing is uncommitted and no background agent is still running (wait, commit, save handoff), (2) tell the user in one line: "Context is large (~Nk tokens) — type `/clear`, then just say: *continue*." Don't suggest `/clear` otherwise. Claude cannot run `/clear` itself.
- **On resume after a clear/compact:** the `SessionStart` hook auto-injects `handoff/SESSION_STATE.md` — don't re-read it; read only the specific files/specs the next task needs. Do NOT re-read the whole handoff folder, the codebase analysis, or big source files "for context".
- **Keep the coordinator's context small:** delegate implementation to agents; ask agents for capped reports; don't paste or re-read large files, diffs, or test output (use `tail`/`grep`, `-q` pytest); view screenshots only when judging a UI deliverable.
- **Prefer cheap models** for mechanical work (scribe/haiku, reviewers/sonnet); use opus only for design-heavy UI or algorithm work.

## Team
Agents in `.claude/agents/` (models pinned; never run agents on Fable). Roster and concurrency rules: `handoff/02_TEAM_ROSTER.md`.
