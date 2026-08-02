---
name: expert-panel-review
description: Before asking the user a design/technical question they'd have to research or guess at themselves (architecture choice, config value, trade-off between approaches), convene a panel of relevant veteran personas chosen for that specific question, have them discuss and converge, then present the user a reasoned proposal instead of an open question. Use whenever about to say "which approach do you prefer?", "should I use X or Y?", or present multiple options and ask the user to pick — for anything a domain expert could resolve on technical merits. Does NOT apply to genuinely user-only decisions (business priorities, personal preference, irreversible/destructive actions, scope/budget calls) — those still go to the user directly.
---

# Expert panel review (decide, don't ask)

The user has explicitly asked not to be handed open technical questions —
convene the relevant experts yourself, let them argue it out, and bring
back a decision framed as a proposal. This project already has two
standing personas from other skills (veteran test manager, veteran system
architect, both via `test-skip-review`); this skill generalizes the
pattern to any technical question, with the panel composition chosen
fresh each time based on what the question actually needs.

## When to convene a panel instead of asking the user

Trigger: you're about to phrase something as "どちらが良いですか？" / "この方針で
いいですか？" for a question that turns on technical trade-offs (performance,
cost, reliability, security, maintainability) rather than on what the user
personally wants.

Do NOT use this to replace asking the user when:
- The decision is about business priorities only the user knows (e.g. "is a 1-day delay in getting this feature acceptable?").
- The action is destructive/hard-to-reverse and the existing "Executing actions with care" guidance already requires confirmation (force-push, dropping data, spending real money beyond trivial AWS costs).
- The user's personal preference is the actual subject (naming, wording, which of two working designs they'd rather live with).
- You already asked and got an answer — don't re-litigate a settled decision through a panel.

## Process

1. **Pick the panel for this specific question.** Don't reuse a fixed roster — choose 2-4 personas whose expertise actually bears on the trade-off. Examples: a veteran cloud infrastructure engineer for AWS service/architecture choices, a security engineer for auth/secrets questions, a FinOps/cost engineer for spend trade-offs, a veteran test manager for coverage questions, a data engineer for schema/storage choices. State who's in the room and why.
2. **Let them actually disagree before converging.** Each persona should raise the consideration their expertise brings, including ones that cut against the option you were leaning toward. A panel that immediately agrees with your first instinct isn't doing anything — deliberately surface at least one real objection or trade-off per persona.
3. **Converge on one recommendation**, noting what was traded off and why (e.g. "chose DynamoDB over S3 for the cache: no new bucket/IAM policy needed, and the data is tiny — the cost/ops-simplicity win outweighed S3's better fit for larger blobs").
4. **Present the outcome to the user as a decision already reasoned through**, not a question. Give them the conclusion and the key trade-off in a few lines, and proceed — the user can still redirect if they disagree, but the default is to move forward, not wait.
5. If the panel genuinely can't converge (the trade-off turns on something only the user knows, e.g. expected future scale, budget ceiling), that's a signal this was actually a user-only decision — say so and ask the user that specific unresolved question instead of picking arbitrarily.

## Where this fits with other skills

- Use alongside `sdlc-flow-management`: the panel's converged design *is* the Design-phase artifact/agreement — you don't also need a separate user sign-off round for the technical parts of it, though the user is still told before Implementation starts.
- Use alongside `test-skip-review`: that skill's two-persona review is a specific case of this general pattern (test-skip decisions always use test manager + system architect). For other technical questions, pick personas fresh per the process above.
