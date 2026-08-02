---
name: sdlc-flow-management
description: Manage this project's software development lifecycle explicitly (planning, requirements, design, implementation, testing, deployment, maintenance) instead of jumping straight to code. Use when starting new feature work, when scoping a new user request, when deciding whether to implement immediately vs. define requirements/design first, or when the user asks what phase the project is in.
---

# SDLC flow management (this project)

Standard SDLC phases: Planning → Requirements → Design → Implementation
(Coding) → Testing → Deployment → Maintenance. (Source: general SDLC
references — Atlassian, Harness, GeeksforGeeks.) This project is a solo/
small-scale AWS serverless service, so each phase gets a lightweight
artifact rather than a formal document — but the phase itself is never
skipped, because skipping requirements/design is what already caused
rework in this project (e.g. the on-demand report button that had to be
fully reverted after shipping without a clear spec).

## Phase → artifact mapping in this repo

| Phase | Where it lives | Gate before moving on |
|---|---|---|
| Planning / backlog | `TODO.md` (priority-ordered) | Item picked, scope agreed with user |
| Requirements | `docs/requirements.md` (what must be true to call it done) | User confirms the requirement text |
| Design | `docs/signal-logic-reference.md` and per-feature design notes; for AWS changes, the plan surfaced via `ExitPlanMode` | User says OK to the concrete plan (file diffs named) before code changes |
| Implementation | git commits on a feature branch | Passes Step "Testing" below before merge/deploy is treated as done |
| Testing | `docs/test-plan.md`, `docs/test-cases.md`, `docs/test-results/` (see `jstqb-test-process` skill) | Test summary shows no unresolved high-priority defects |
| Deployment | `agent-led-aws-deploy` skill workflow, `README.md` deploy steps | Live AWS invoke confirms behavior, not just local tests |
| Maintenance | `TODO.md` (defects/risks found during testing get filed back here) | — |

## Rules

1. **Never start Implementation for a non-trivial feature without an explicit Requirements + Design step the user has agreed to.** A one-line bug fix doesn't need this ceremony; a new behavior (new signal logic, new report cadence, new UI flow) does.
2. **Track current phase.** Before starting new work, state which phase it's in ("this is at Design, waiting on your OK" / "this is Implementation, tests not run yet"). Don't imply something is done/deployed until it has passed Testing and Deployment, not just Implementation.
3. **Testing is a phase, not an afterthought.** When implementation finishes, hand off to the `jstqb-test-process` skill rather than declaring the feature complete on the strength of "it compiled" or one manual invoke.
4. **Feed Maintenance back into Planning.** Defects or risks found during Testing or Deployment go into `TODO.md` with a priority, not just fixed silently and forgotten — so the backlog stays the single source of truth for what's outstanding.
