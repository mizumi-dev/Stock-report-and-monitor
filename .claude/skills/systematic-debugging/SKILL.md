---
name: systematic-debugging
description: Work through a confusing or hard-to-reproduce bug methodically instead of guessing and patching symptoms. Use this whenever the user describes an error or bug whose cause isn't immediately obvious, when a first fix attempt didn't solve it, when something "used to work and now doesn't," when a system behaves inconsistently across environments, or when the user says things like "this is so weird" or "I don't understand why this is happening." Also use it proactively when you notice yourself about to make a second unverified guess at a fix for the same bug.
---

# Systematic debugging

The instinct under pressure is to read the error, form a guess, and patch it. That's fine for simple bugs. For confusing ones it produces a worse outcome than it looks: you fix a symptom, the real cause resurfaces later in a less obvious form, and now there are two things tangled together. Trading a little upfront time for actually finding the cause is usually faster once you count the wasted guesses.

## Isolate the layer before you touch code

Most confusing bugs aren't exotic — they're confusing because the failure is *observed* several layers from where it *happens* (a browser shows an error originating in a backend; a backend shows one originating in a third-party API; a UI shows stale data because of a cache between it and the source). Guessing without locating which layer owns the problem means you're as likely to "fix" a layer that was fine.

Before writing any fix:

1. **Find the boundary between known-good and known-bad.** Use the observation that most directly shows where failure occurs to split the system in half. Real example: a Lambda Function URL returned 403 while directly invoking the same Lambda succeeded — that single fact proved the handler code was fine and made the whole investigation about the authorization layer in front of it. Look for the equivalent split first.

2. **Get evidence closer to the failure than the user-visible symptom.** Logs, network traces, a direct API call bypassing the UI, a DB query bypassing the app. In that same case, checking CloudWatch Logs was decisive: *zero* log entries around the failed request proved it never reached the function, killing an entire category of hypotheses (bad handler code, bad env vars, DB permissions) in one move.

3. **State a hypothesis as a sentence, then design the cheapest test for that hypothesis** — a test, not a fix. "If X is the cause, then Y should be observably true." This is what separates debugging from trial-and-error: trial-and-error changes code and sees if the symptom disappears (slow, and success still doesn't tell you *why*); a real test confirms or kills the hypothesis directly.

4. **Verify the explanation accounts for everything observed**, not just the headline symptom. An explanation covering only part of the evidence is usually incomplete, and the uncovered part resurfaces.

5. **Don't stop at "a cause that could explain it."** Prove causation where you can — reproduce the failure by inducing the suspected cause, or fix only that one thing and confirm it alone resolves the issue. Plausible-but-unverified explanations are how a bug gets "fixed" twice.

## Watch for failures that produce no error at all

The hardest class isn't a crash — it's a computation that silently degrades to a constant. A `rolling(200)` window over 126 rows yields all-NaN, every comparison against NaN is `False`, and a feature evaluates to "no signal" every single day while looking perfectly healthy. Nothing logs, nothing throws.

When something "just never triggers," don't assume the condition is genuinely never met. Feed it synthetic input that *must* trigger it and assert it does. If it still doesn't fire, you've found a dead code path rather than a quiet market.

## When the first fix doesn't work

Resist guessing again. Treat the failed fix as evidence: it just told you something is *not* the cause, or not the only one. Update your model before proposing anything else. If you're several rounds in and still guessing, go back to step 1 — you probably mis-located which layer owns the problem.

## Rule out folklore cheaply

Complex systems accumulate "known gotchas" that sound authoritative but may not apply — or may not exist. Before investing real time in one, do a cheap existence check: does that feature/API actually exist for this exact configuration? A five-minute check can save an hour of chasing a wrong explanation. (In the Lambda case, a widely-assumed "Block Public Access for Lambda" turned out not to be a real feature at all.)

## Separate your environment from the one that matters

When debugging from a sandbox, be explicit about which failures are real and which are artifacts of your own environment — network egress restrictions, missing credentials, different tool versions. A request blocked by your sandbox's proxy looks identical to a genuine remote 403. Check whether the failure comes from the real target or your own boundary before drawing conclusions, and get a second data point from a representative environment (the user's machine, a different network) rather than trusting one ambiguous signal.

The reverse also applies: when your own earlier explanation is contradicted by new evidence, say so plainly and correct it. In the Lambda case an initial "it's timing out" theory was wrong — the job took 41 seconds and always succeeded; the browser just had no feedback. Carrying a wrong diagnosis forward would have produced the wrong fix (raising a timeout that was never the constraint) instead of the right one (async invocation plus immediate UI response).

## Writing up the finding

State the cause as a short causal chain, not a story: what state existed → what it caused → what that caused → the observed symptom. That's what makes it verifiable for the user, and it usually points directly at the single place to fix.
