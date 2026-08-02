---
name: agent-led-aws-deploy
description: Run AWS CLI deployment work (Lambda updates, IAM policy changes, DynamoDB setup) directly yourself instead of walking the user through commands one by one, when you don't have AWS credentials in your own environment. Also covers pushing to GitHub when the session's GitHub integration lacks write access. Use this whenever you're about to ask a user to copy-paste a sequence of AWS CLI or git commands, whenever a user says something like "don't make me do manual work" or "can you just do it," or whenever you're in a sandboxed/remote environment without direct AWS or git-push access but the user has an AWS account and a terminal.
---

# Agent-led AWS deployment (when you have no credentials of your own)

In a sandboxed or remote environment you likely have no AWS credentials and possibly no network path to AWS at all. That doesn't mean the user has to hand-run every command. Get scoped credentials once, then drive the deployment yourself, verifying each step instead of asking the user to eyeball CLI output.

## Why this beats "here are 10 commands, paste them in order"

- The user is often on Windows/PowerShell, where `\` is **not** a line continuation (bash muscle memory breaks silently — PowerShell runs the next line as a separate malformed command).
- Inline JSON with nested quotes (`--policy-document "{\"Version\":...}"`) gets mangled by shell escaping almost every time.
- Terminal/clipboard security tooling can silently mask strings that look like credentials (`AKIA...`) as they're typed *or* pasted, corrupting the argument and producing confusing "invalid characters" errors unrelated to the command.
- You can't see their screen, so wrong-directory or wrong-resource-name mistakes take a full round-trip to catch.

Running commands yourself gives immediate verifiable feedback (exit codes, real API responses) and lets you adapt on the spot — e.g. discovering the deployed IAM role isn't named what the README says and just using the real name.

## The workflow

### 1. Ask for scoped, not root, credentials

Never ask for main/root credentials. Ask for an IAM user with an inline policy scoped to exactly the actions and resource ARNs this task needs. Write the policy JSON yourself and hand it over **as a file** — don't make them write IAM JSON, and don't pass it inline (see escaping above):

```bash
aws iam create-user --user-name <name>
aws iam put-user-policy --user-name <name> --policy-name <policy> --policy-document file://policy.json
aws iam create-access-key --user-name <name>
```

`file://` sidesteps all the quoting problems. Scope to specific resource ARNs rather than `*` wherever actions support it. If a step needs one more permission you didn't anticipate, ask for one targeted follow-up rather than requesting broad access up front.

### 2. For multi-round work, offer a standing user

Recreating and deleting a temp IAM user for every change is real friction across a multi-session project. If more work is coming, offer the choice explicitly: one-off temp user they delete afterward, vs. a standing deploy user with a slightly broader (still scoped) policy. Let them pick.

### 3. Store credentials in a separate profile, not env vars

```bash
cat >> ~/.aws/credentials << 'EOF'

[my-deploy-profile]
aws_access_key_id = ...
aws_secret_access_key = ...
EOF
```

Then `export AWS_PROFILE=my-deploy-profile`. **Explicitly `unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN` first** if the environment has pre-existing dummy/proxy credentials there — env vars outrank `AWS_PROFILE` in the credential chain, so leftovers silently shadow your profile and produce a generic `InvalidClientTokenId` that has nothing to do with the new key being wrong. Put the `unset` in *every* command block, since shell state doesn't persist between calls.

### 4. Verify identity and real resource state before changing anything

Don't trust names/roles/regions from a README or handoff doc — they're often stale relative to what's deployed:

```bash
aws sts get-caller-identity
aws lambda get-function-configuration --function-name <name> --query Role
```

A `NoSuchEntity` on `put-role-policy` means your assumed name is wrong — look up the real one instead of retrying.

### 5. Deploy, verify each step, then exercise end-to-end

After code/config changes, invoke the real function with an event matching production traffic and check the actual response — don't just trust that `update-function-code` returned success. `LastUpdateStatus` passes through `InProgress`; poll before invoking:

```bash
for i in 1 2 3 4 5; do
  s=$(aws lambda get-function-configuration --function-name <name> --query LastUpdateStatus --output text)
  [ "$s" = "Successful" ] && break
  sleep 3
done
```

**The very first invoke right after a deploy can still hit a stale warm execution environment from before the update**, even after `LastUpdateStatus` reports `Successful` and `CodeSha256` matches your local zip's hash — observed repeatedly on this project (a removed diagnostic code path answered once after its own redeploy, then correctly disappeared on the next two invokes; a logging fix behaved the same way). Don't trust a single post-deploy invoke, especially right after two deploys in quick succession — invoke 2-3 times and expect the *later* ones to reflect the new code, not necessarily the first.

If a deploy's result looks impossibly wrong (e.g. the response matches code you replaced two deploys ago), verify by comparing `CodeSha256` against a local hash of the zip before theorizing:

```bash
python3 -c "import hashlib,base64;print(base64.b64encode(hashlib.sha256(open('f.zip','rb').read()).digest()).decode())"
aws lambda get-function-configuration --function-name <name> --query CodeSha256 --output text
```

Testing against real infrastructure is how you catch bugs mocked tests can't reproduce. For behavior changes, prove propagation with an observable difference rather than assuming — e.g. after switching a config that changes an indicator's period, confirm the stored numeric output actually changed.

### 6. Clean up temp credentials

Remind the user to delete the access key and user. If they hit the credential-masking problem when deleting, splitting the key across two variables avoids whatever pattern-matcher is interfering:

```powershell
$p1 = "AKIA..."; $p2 = "..."; aws iam delete-access-key --user-name <name> --access-key-id ($p1+$p2)
```

A user cannot delete their own access key if the scoped policy doesn't grant `iam:DeleteAccessKey` — that's expected, and it has to be done from their admin terminal.

## When your GitHub integration also can't push

If pushing fails with `403 Resource not accessible by integration` (API) or a `403` on `git push` while *reads* work fine, that's a read-only GitHub App installation for this session, not something the user can necessarily fix in GitHub App settings (worth a try, but don't be surprised if it doesn't resolve it).

The reliable workaround: a **fine-grained Personal Access Token** scoped to just that repo with **Contents: Read and write** (Settings → Developer settings → Personal access tokens → Fine-grained tokens):

```bash
git remote set-url origin https://x-access-token:<PAT>@github.com/<owner>/<repo>.git
git push origin <local-branch>:<remote-branch>
```

If the remote branch diverged (e.g. the user manually applied a patch you sent before you had push access), diff your commit against the remote tip before force-pushing — identical trees mean it's a rebase artifact and `--force-with-lease` is safe; differing trees mean merge instead of clobbering their work.

Session-injected credentials can reset `git remote set-url` between turns, the same way env vars override `AWS_PROFILE`. If a push fails after you set the PAT URL, re-check the remote before assuming the token is bad.

**Never store the PAT value itself in any file that gets committed to the repo** (including skill files) — skills here are git-tracked, so a token written into one would leak into git history permanently, visible to anyone with read access, even after later removal. If a push fails with the remote reset to the internal proxy, just ask the user to paste the PAT again (or confirm the previously-issued one is still valid) and re-run `git remote set-url` — don't try to work around it by persisting the secret in tracked files. The remote URL with the embedded token lives only in the local, untracked git config, and is lost on container/session restart — that's expected, not a bug to fix by storing it somewhere durable.
