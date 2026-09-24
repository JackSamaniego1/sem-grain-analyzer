---
name: save-handoff
description: Checkpoint the project state for the next session — delegate to the scribe agent to update handoff/SESSION_STATE.md, the progress log and task board, then commit the handoff folder. Use after each completed task, each decision, and always before ending a session.
allowed-tools: Agent, Bash, Read
---

# Save handoff

1. Gather: last 3 things completed (task IDs), anything half-finished (file + what remains), any decisions made, next 3 actions, and `git rev-parse --short HEAD`.
2. Spawn the `scribe` agent (model haiku) with that information in a self-contained prompt. Ask it to update `handoff/SESSION_STATE.md`, append to `handoff/06_PROGRESS_LOG.md`, flip statuses in `handoff/03_TASK_BOARD.md`, append to `handoff/05_DECISIONS.md` if needed, and commit `handoff/` only.
3. Confirm the commit landed: `git log --oneline -1`.
4. Never push from this skill.
5. If no background agent is running and the working tree is clean, end your reply with: "Handoff saved — safe to `/clear` now, then say: *Read handoff/SESSION_STATE.md and continue*." (keeps usage low; the user asked for frequent clears).
