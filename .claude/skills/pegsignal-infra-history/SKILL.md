---
name: pegsignal-infra-history
description: Background and rationale for PegSignal's AWS/domain architecture and past incidents — why each Lambda/domain/table exists in its current shape, not just what it is. Load this when you need to understand *why* something is set up a certain way (before changing domain routing, IAM policy, universe/index support, email sending, or dev/prod separation), when a decision seems odd and might have a documented reason, or when the user asks about project history. CLAUDE.md has only the current-state facts; this skill has the story behind them.
---

# PegSignal infrastructure history

CLAUDE.md states current architecture facts. This skill fills in *why*, so you
don't accidentally re-break something that was already fixed once, or waste
time re-deriving a decision that was already made deliberately.

## Security incident (2026-08-05)

An AWS access key was committed in plaintext while the repo was also public,
so both problems compounded. Fixed by rotating the key, keeping the repo
private by default, and maintaining `.gitignore` patterns for
`*.local.md`/`.env`/`*credentials*`. Always check file contents for secrets
before committing, especially "internal notes" files that feel harmless.

## Domain architecture: how it got to its current shape

Originally there was only the raw `register-stock` Lambda Function URL
(`xxxxx.lambda-url.us-east-1.on.aws`) — no branded domain at all, and no
public preview page.

Staged migration (all 2026-08-06 to 2026-08-07), each stage independently
verified before the next:

1. Added `landing-preview`, a separate read-only Lambda (GetItem-only IAM
   role) that renders the daily `selection_snapshot` for anonymous visitors
   without any signup. Deployed first at `preview.pegsignal.com`.
2. Added `app.pegsignal.com`, pointed at the same `register-stock` Function
   URL that `pegsignal.com` used at the time, so both worked simultaneously
   during the cutover. Switched `REGISTER_FUNCTION_URL` (shared by
   register-stock/daily-batch, used for email links) to this branded
   subdomain — raw AWS Lambda URLs in stock-service emails read as phishing
   and hurt deliverability.
3. Swapped the live `pegsignal.com` apex CloudFront distribution's origin
   from register-stock to landing-preview (an in-place origin swap, same
   aliases/cert/behavior — the highest-risk step, done only after explicit
   user go-ahead).
4. Embedded the signup form directly into landing-preview's page instead of
   just linking out, so the experience is fully web-based. The form's
   `action` still points at `app.pegsignal.com` (register-stock) via a plain
   `method="GET"` HTML form — no processing is duplicated. The only thing
   generated on landing-preview's side is two CAPTCHA random integers;
   register-stock's captcha check is stateless (just `n1+n2 == answer`), so
   either Lambda generating them is safe.
5. Once the apex served the same content as `preview.pegsignal.com`, that
   subdomain became fully redundant. Disabled (not deleted) via
   `aws cloudfront update-distribution --enabled false` on distribution
   `EZW5FYV1PT3FO` — kept so it's instantly restorable if something regresses.
   User explicitly wants to wait before actually deleting it.
6. Added a visual/UX pass to landing-preview (trust-line reassurance text,
   a NASDAQ100-vs-S&P500-style compare table, a 3-step flow) reusing visual
   patterns from the old register-stock top page, since the plain-text-only
   version tested as less intuitive.

All of this was decided via `expert-panel-review` sessions (ad monetization
type, integration method, frontdoor placement) — if a similar
domain/monetization tradeoff comes up again, that skill is the right tool,
not re-deriving from scratch.

Deploy scripts (`deploy_domain.sh`, `deploy_domain_en.sh`,
`deploy_domain_preview.sh`, `deploy_domain_app.sh`) all follow the same
pattern: ACM cert in `us-east-1` (DNS validation) → CloudFront distribution
using the managed `CachingDisabled` policy
(`4135ea2d-6df8-44a3-9df3-4b5a84be39ad`) + `AllViewerExceptHostHeader`
origin-request policy (`b689b0a8-53d0-40ab-baf2-68738e2966ac`) → Route53
ALIAS A-record (CloudFront's fixed hosted-zone-id `Z2FDTNDATAQYW2`). Reuse
this pattern rather than inventing a new one for the next subdomain.

## Email: Resend instead of SES

SES production-access review was repeatedly rejected with no path to
resolution, so mail sending switched to Resend's API via `mailer.py`'s
`ResendMailer` (2026-08-06). It intentionally mirrors boto3's SES
`send_email(Source=, Destination=, Message=, ...)` interface, so no calling
code needed to change — if you're hunting for "why does this call SES-shaped
methods that aren't boto3", that's why.

## Dev/prod separation

Originally dev testing shared production's `approved_tokens` and
`selection_snapshot` data, meaning dev test actions could trigger real
production emails and overwrite the production snapshot. Fixed by giving
`develop` a fully separate set of `-dev`-suffixed Lambdas and DynamoDB
tables (`deploy_dev.sh`). `daily-batch-dev` deliberately has no EventBridge
schedule (manual invoke only) to avoid unattended cost/misbehavior in dev.

## IAM policy size constraint

The `claude-deploy` IAM user's inline `deploy-scoped` policy sits near the
2048-byte aggregate inline-policy limit — `aws iam put-user-policy` fails
with `LimitExceeded` if you try to grow it further. For one-off infra tasks
needing broader permissions (ACM, CloudFront, Route53), the working pattern
is: `aws iam attach-user-policy` a relevant AWS-managed policy at the start
of the task, then `aws iam detach-user-policy` once done, rather than trying
to shoehorn more into the inline policy. `IAMFullAccess` stays permanently
attached; other managed policies (`AmazonRoute53FullAccess`,
`CloudFrontFullAccess`, `AWSCertificateManagerFullAccess`) are temporary.

## Universe (index) support

NASDAQ100 was the only supported index at first; S&P500 was added later via
`config.py`'s `UNIVERSES` dict + `normalize_universe`/`selection_snapshot_key`
pattern, extending the same per-(mode) snapshot-key mechanism to
per-(mode, universe). `landing-preview` only shows a universe if at least one
approved user has that universe selected (that's what makes daily-batch
actually scan and save a snapshot for it) — if a newly-added universe shows
"no data yet" on the preview page, that's very likely why, not a bug.
NYダウ・日経225 remain unimplemented and are low priority relative to
distribution/monetization work.

## Sandbox networking

This development environment's outbound proxy blocks arbitrary external
domains, including `pegsignal.com` itself — confirmed via
`curl -sS "$HTTPS_PROXY/__agentproxy/status"` showing `connect_rejected`
entries. This is a sandbox limitation, not a signal that the production
domain is broken or rejecting bot traffic. Verify production behavior via
direct `aws lambda invoke` against the real Lambda functions instead of
trying to reach the domains over HTTP from this environment.
