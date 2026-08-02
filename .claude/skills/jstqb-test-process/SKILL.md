---
name: jstqb-test-process
description: Design, execute, and manage tests for this project following the JSTQB/ISTQB Foundation Level fundamental test process (test planning, monitoring & control, analysis, design, implementation, execution, completion) and its test levels/types/static-testing concepts. Use whenever asked to test a feature, write test cases, review or report test results, or before treating a shipped feature as actually done.
---

# JSTQB-based test process (this project)

Grounded in the ISTQB/JSTQB Foundation Level syllabus's fundamental test
process: **test planning → test monitoring & control → test analysis →
test design → test implementation → test execution → test completion**,
each activity producing a work product. This project is solo/small-scale,
so work products are lightweight files, not formal IEEE 829 documents —
but every activity still happens, in order, and every activity's output
is a file under `docs/`, not just chat text that disappears.

## Activities and their output in this repo

| JSTQB activity | What it means here | Output file |
|---|---|---|
| Test planning | Decide scope, objectives, risk-based priority, entry/exit criteria for this test round | `docs/test-plan.md` |
| Test monitoring & control | Track what's been run vs. planned; adjust if something blocks progress | Checklist state inside `docs/test-plan.md` |
| Test analysis | From the test basis (`docs/requirements.md`, `docs/signal-logic-reference.md`, code) derive test conditions — "what could be wrong" | Test condition list in `docs/test-cases.md` |
| Test design | Turn conditions into concrete test cases (input, steps, expected result) | `docs/test-cases.md` |
| Test implementation | Prepare the actual means to run each case (AWS CLI invoke payload, UI steps, test data) | Inline in `docs/test-cases.md` or a script |
| Test execution | Actually run against the real system (this project tests live on AWS, not just mocks — see `agent-led-aws-deploy` skill) | Dated file in `docs/test-results/` |
| Test completion | Summarize pass/fail, log defects back to `TODO.md`, decide if exit criteria met | Summary section in the same `docs/test-results/` file |

## Test levels (map before picking test level)

Component (unit) → Integration (Lambda↔DynamoDB↔SES↔EventBridge) → System
(end-to-end live invoke) → Acceptance (does it satisfy what the user
actually needs). This project currently has near-zero component tests
(tracked as `TODO.md` #4) — don't let that block System/Acceptance-level
testing, which is what actually matters for a small live service; note
the gap as a risk rather than pretending it's covered.

## Test types (pick which apply per feature)

- **Functional**: does the feature do what it's supposed to (signal values, report contents, registration flow).
- **Non-functional**: performance/timing (execution duration vs. Lambda timeout), reliability (external API failure handling), security (token/auth, IAM policy scope), usability (is the info page and email actually understandable).
- **Structural (white-box)**: rarely needed here given the project's size; skip unless a specific function's branch coverage is in doubt.
- **Change-related (regression/re-test)**: after any fix, re-run the specific failing case (confirmation testing) AND a quick pass over previously-passing adjacent features (regression) — this project has been bitten before by fixing one thing and silently breaking an unrelated screen.

## Static testing (do this before dynamic testing, it's cheaper)

Before running anything: an informal review of the diff and of any
user-facing text (email templates, the `_render_info` explanation page)
against what was actually implemented — catches spec/implementation
drift (JSTQB's core argument for static testing: cheaper defects caught
earlier). Full inspections/walkthrough meetings are overkill for this
project's scale; a single deliberate read-through is enough.

## Risk-based prioritization

Prioritize by (likelihood × impact), same logic already used in
`TODO.md`. When time is limited, test "breaks silently and nobody
notices" scenarios before polish items.

## Rules

1. Don't skip straight to execution — write the test conditions (analysis) and cases (design) first, even briefly, so coverage is a deliberate decision rather than whatever comes to mind while clicking around.
2. Every execution round ends with a completion summary and, if defects are found, an entry in `TODO.md` — a test that finds a bug and doesn't file it anywhere is wasted work.
3. Prefer testing against the real deployed AWS environment for System-level tests (per `agent-led-aws-deploy`); local logic tests are fine for Component-level checks of pure functions like `signals.py`.
4. **After a testing session that touched real production data/state** (watchlist entries, `app_settings` like period mode, any table mutated via register-stock or direct AWS CLI calls), do a final pass comparing every touched table/setting back to its pre-session baseline before calling the round done. Tests that switch state mid-run (mode switches, registrations) are easy to leave in a non-default state by the time the session ends across many rounds — don't assume an earlier "restored to X" step in the middle of the session is still true at the end; re-check it explicitly as the last step.
