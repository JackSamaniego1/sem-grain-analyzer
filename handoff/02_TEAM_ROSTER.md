# Team Roster

Agent definitions live in `.claude/agents/*.md` (frontmatter pins model + tools). Spawn with the `Agent` tool, `subagent_type: <name>`. Never override a model to `fable`.

| Agent | Model | Owns | Use when | Typical cost |
|-------|-------|------|----------|--------------|
| `scribe` | haiku | `handoff/` | After every task / decision / session end (`/save-handoff`) | tiny |
| `detection-engineer` | opus | `core/`, detection tests | Segmentation, measurements, ASTM, SAM tuning, DET-* | medium |
| `data-architect` | sonnet | `data/`, data tests | Workspace/session/catalog, DATA-01…05 | medium |
| `ui-designer` | opus | `ui/`, `resources/` | Design system, shell, pages, canvas, UI-* and the UI halves of DATA-06/07, REP-05 | high |
| `report-engineer` | sonnet | `reports/`, report tests | ReportModel, xlsxwriter, python-pptx, REP-* | medium |
| `innovator` | opus | `handoff/07_IDEAS_BACKLOG.md`, `handoff/specs/`, `scratch/` | Start of each session; idea specs | low–medium |
| `qa-engineer` | sonnet | `tests/`, `scratch/qa/` | Before each commit, journey tests, release gate | low |
| `build-engineer` | sonnet | requirements, spec, build scripts, CI, `version.py` | Dependency changes, builds, releases, FND-*/REL-* | low |
| `code-reviewer` | sonnet (read-only) | — | Every diff before commit | low |

Built-in agents still available: `Explore` (fast read-only search), `Plan`, `general-purpose`.

## Concurrency map (which agents may run at the same time)
- Safe in parallel: `detection-engineer` (core/) ∥ `data-architect` (data/) ∥ `report-engineer` (reports/) ∥ `build-engineer` (packaging) ∥ `innovator` (handoff/scratch).
- `ui-designer` touches `ui/` — run it alone among UI tasks, but freely alongside the non-UI agents above.
- `qa-engineer` and `code-reviewer` run after a feature agent finishes, never during.

## Skills (`.claude/skills/`)
- `/run-tests` — full pytest run, summary only.
- `/smoke-app` — headless launch + screenshot.
- `/save-handoff` — spawn scribe with the current state and commit `handoff/`.
Also available from Claude Code: `/code-review`, `/simplify`, `/security-review`.

## Communication contract
Every agent report ends with: files changed, how verified, open questions, suggested next task. Reports are capped (see each agent file). The coordinator never asks an agent to "look at what the other agent did" — it passes the relevant API summary or file list explicitly.
