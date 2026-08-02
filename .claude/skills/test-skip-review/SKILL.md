---
name: test-skip-review
description: Before skipping, deferring, or substituting a planned test with a lighter-weight check (e.g. mocked/local test instead of live production verification), get an explicit two-persona review — a veteran test manager and a veteran system architect — and document their conclusion. Use this whenever you're about to write "未実施" / "not tested" / "skipped" / "verified via review only" in test results, whenever you're tempted to avoid touching production to test an error/failure path, or whenever a test-plan item is dropped for being risky or expensive to run for real.
---

# Test-skip review (two-persona check)

Skipping a test is sometimes the right call (e.g. deliberately breaking
production data isn't worth the risk to confirm something low-value), but
that judgment call should not be made unilaterally in passing. Every time
a planned test case is skipped, deferred, or replaced with a weaker
substitute (mock instead of live, code review instead of execution), run
it past two reviewer personas before finalizing, and record the outcome.

## The two personas

**ベテランのテスト管理者 (veteran test manager)** — pushes back on the skip
by asking: what risk does this leave uncovered, is there a safe way to
still get real coverage (ephemeral test data, a temporary feature flag,
a staging table, a synthetic event), and does the skip rationale actually
hold up or is it convenience dressed as risk-avoidance?

**ベテランのシステム設計者 (veteran system architect)** — evaluates the
production-safety trade-off from the system's side: would actually
running this test risk real user-visible damage (sending a real
notification to a real recipient, corrupting real data, hitting rate
limits on a third-party API), and if so, is there an architecturally
cheap way to make the system safer to test (inject a test flag, add a
dry-run mode, use a disposable resource) rather than just skipping.

## Process

1. State the specific thing being skipped and the reason.
2. Have the test manager persona argue for closing the gap (a safer way
   to still test it) before accepting the skip.
3. Have the architect persona weigh in on whether attempting it live is
   actually safe, and whether a structural fix (test hook, dry-run flag)
   is warranted instead of a one-off decision.
4. Converge on one of three outcomes, and write it down (in the test
   results file, not just chat):
   - **Skip accepted** — with the specific risk being accepted stated explicitly.
   - **Run it, but safely** — using a concrete safe method the two personas agreed on (disposable ticker, staging table, temporary revertible code, synthetic event field).
   - **Skip accepted, but flag a structural gap** — e.g. "this keeps being hard to test safely because the system has no dry-run mode" — file that as a backlog item (see `sdlc-flow-management`'s Maintenance step), not just repeat the same skip next time.
5. Never let "I already decided to skip this" stand without this review — it's cheap (a few sentences of reasoning) and it's what catches the case where a skip was actually convenience rather than a real risk trade-off.

## Where this plugs into the existing test process

This sits inside the `jstqb-test-process` skill's Test Execution /
Completion activities — specifically wherever the "未実施" (not executed)
column would otherwise get filled in silently. The two-persona review
output goes in the same completion report, right next to the skipped
item, not in a separate document.
