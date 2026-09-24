"""Claude Code hooks for context hygiene (dev tooling only - not part of the app).

  resume  (SessionStart, after /clear or compact): inject handoff/SESSION_STATE.md
  check   (UserPromptSubmit): if the live context is over THRESHOLD tokens,
          tell Claude to wrap up and suggest /clear at the next safe point.
"""
import json
import os
import sys

THRESHOLD = 120_000  # tokens of context before a /clear is worth it
TAIL_BYTES = 4_000_000


def _out(event, context, user_msg=None):
    payload = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}
    if user_msg:
        payload["systemMessage"] = user_msg
    print(json.dumps(payload))


def resume(root):
    path = os.path.join(root, "handoff", "SESSION_STATE.md")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            state = f.read()
    except OSError:
        return
    _out("SessionStart",
         "Context was just cleared/compacted. Current project state "
         "(handoff/SESSION_STATE.md) follows - resume from its 'Next' list "
         "without re-reading the handoff folder:\n\n" + state)


def context_tokens(transcript):
    with open(transcript, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - TAIL_BYTES))
        lines = f.read().splitlines()
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("isSidechain") or entry.get("type") != "assistant":
            continue
        usage = (entry.get("message") or {}).get("usage")
        if usage:
            return (usage.get("input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0))
    return 0


def check(data):
    transcript = data.get("transcript_path")
    if not transcript or not os.path.exists(transcript):
        return
    tokens = context_tokens(transcript)
    if tokens < THRESHOLD:
        return
    k = tokens // 1000
    _out("UserPromptSubmit",
         f"CONTEXT SIZE: ~{k}k tokens (threshold {THRESHOLD // 1000}k). Every turn now "
         "re-sends this much context. At the next safe point (no uncommitted work, no "
         "running agents): commit, run /save-handoff, then tell the user in one line: "
         "'Context is large (~%dk tokens) - type /clear, then just say: continue'." % k,
         f"Context ~{k}k tokens - Claude will suggest /clear at the next safe point.")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    if mode == "resume":
        resume(root)
    elif mode == "check":
        check(data)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never block the session
