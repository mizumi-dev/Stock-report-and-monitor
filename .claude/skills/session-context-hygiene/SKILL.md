---
name: session-context-hygiene
description: Manage long-running session context proactively rather than letting it silently degrade. Use this whenever a conversation has been going for many turns, involves large tool outputs (full file reads, long command output, big diffs), or when responses start feeling slower or less precise. Also apply it whenever the user asks you to keep sessions efficient or self-manage context length.
---

# Session context hygiene

On this project, sessions tend to run long (multi-day, many rounds of design
discussion plus live AWS verification). Don't wait for the user to notice
context bloat — manage it yourself.

## Run `/compact` at your own judgment

The user has explicitly asked: don't wait to be told — if the conversation
feels long enough that context is getting heavy (long back-and-forth,
several large file reads/tool outputs accumulated, a natural milestone just
finished, e.g. a feature shipped and confirmed), run `/compact` proactively
at a natural breakpoint (after finishing and confirming a task, not
mid-edit or mid-investigation).

Signals it's a good time to compact:
- A discrete piece of work just finished and was confirmed working
  (tests passed, deploy verified, user said thanks/OK).
- The conversation has accumulated many large Read/Bash outputs that are
  no longer needed verbatim (already summarized into decisions or code).
- You're about to start a new, mostly-unrelated piece of work.

Avoid compacting:
- Mid-debug, before the root cause and fix are confirmed.
- Mid-implementation, before a change is committed/verified.
