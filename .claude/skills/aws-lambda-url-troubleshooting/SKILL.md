---
name: aws-lambda-url-troubleshooting
description: Diagnose AWS Lambda Function URL "403 Forbidden" errors, and other Lambda/DynamoDB serverless pitfalls that look like misconfiguration but aren't. Use this whenever a user reports a Lambda Function URL returning 403 despite AuthType NONE and a seemingly-correct resource policy, whenever they're debugging a public Lambda endpoint that "was working and now isn't," or whenever code that writes numeric values to DynamoDB crashes with "Float types are not supported." Trigger proactively even if the user just describes the symptom (e.g. "my Lambda URL gives Forbidden", "DynamoDB TypeError float") without naming AWS explicitly as the cause.
---

# AWS Lambda Function URL & DynamoDB troubleshooting

Real bugs hit in this project's Lambda/DynamoDB stack, where the obvious checks all pass and the actual cause is somewhere you wouldn't think to look. Work through them in order — each rules out a layer before moving on.

## Function URL returns 403 Forbidden

The symptom: `{"Message":"Forbidden"}` from the URL, but `aws lambda invoke` on the same function works. That split is the key clue — the request never reaches the function. It's rejected by an authorization layer in front of Lambda, not by handler code. Don't debug the handler; look at what sits between the browser and the function.

### Step 1 — confirm the request never reaches Lambda

Check CloudWatch Logs (`/aws/lambda/<name>`) for an entry around when the URL was hit. Nothing there means rejection happened at the edge before Lambda started — this confirms it's authorization/policy, not application code, and saves you debugging the wrong layer.

### Step 2 — the usual real cause: missing `lambda:InvokeFunction`

Since an AWS change in October 2025, Function URLs created after that date require **two** permissions in the resource policy, not one:

- `lambda:InvokeFunctionUrl` (the one everyone remembers)
- `lambda:InvokeFunction` (easy to miss — pre-October-2025 tutorials and muscle memory omit it)

Console-created URLs get both automatically. CLI/IaC-created ones usually get only the first, and older `add-permission` snippets on the internet only add the first. **This is very often the entire bug.**

```bash
aws lambda get-policy --function-name <name> --query Policy --output text | python3 -m json.tool
```

Look for both actions across all statements. If `lambda:InvokeFunction` is missing:

```bash
aws lambda add-permission --function-name <name> \
  --action lambda:InvokeFunction --principal "*" \
  --statement-id public-url-invoke-function --region <region>
```

`--function-url-auth-type` can only accompany `lambda:InvokeFunctionUrl` — passing it with `lambda:InvokeFunction` fails with `InvalidParameterValueException`. Omit it for the second grant.

### Step 3 — rule out a stale URL

`*.lambda-url.<region>.on.aws` is wildcard DNS. A **deleted** Function URL ID still resolves and still returns `{"Message":"Forbidden"}` — not a DNS failure, not a 404. If the URL was ever recreated, confirm you're testing the current one:

```bash
aws lambda get-function-url-config --function-name <name> --query FunctionUrl --output text
```

### Step 4 — check the policy Condition matches the AuthType

With AuthType `NONE`, the `InvokeFunctionUrl` statement should carry `Condition: {"StringEquals": {"lambda:FunctionUrlAuthType": "NONE"}}`. A leftover statement from when the URL used `AWS_IAM` won't match and won't error — it just silently doesn't apply. Worse, `add-permission` **fails instead of overwriting** when the `--statement-id` exists (`ResourceConflictException`), so a script doing `add-permission || echo "skipping"` leaves a wrong condition in place while looking successful. Remove then re-add:

```bash
aws lambda remove-permission --function-name <name> --statement-id <id> --region <region> 2>/dev/null || true
aws lambda add-permission --function-name <name> ... --statement-id <id> --region <region>
```

### Step 5 — plausible-sounding red herrings

- **"Block Public Access" for Lambda** — not a real feature (easy to confuse with S3's, which is real). There's no `get-public-access-block-config` for Lambda in the CLI. Don't chase it.
- **Local network interference** — if it fails from a sandbox *and* from the user's phone on cellular (bypassing their home/corporate network), that's ruled out.
- **Organizations SCP/RCP** — possible if the account is in an org, but check `aws organizations describe-organization` first (`AWSOrganizationsNotInUseException` = not applicable) before hunting for policies that don't exist.

## DynamoDB `TypeError: Float types are not supported`

boto3's DynamoDB **resource** API (`boto3.resource("dynamodb")`, unlike the lower-level client) refuses native Python `float` and requires `decimal.Decimal`. This bites when saving computed values (rounded ratios, percentages, scores) built with `round(float(x), n)`. It only fires at `put_item` time, so it passes code review and mocked tests, surfacing only when real data reaches production.

```python
from decimal import Decimal

def to_dynamodb_safe(value):
    if isinstance(value, float):
        return Decimal(str(value))  # str() avoids binary float imprecision
    if isinstance(value, dict):
        return {k: to_dynamodb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_dynamodb_safe(v) for v in value]
    return value
```

Values read back come out as `Decimal`, not `float` — fine for arithmetic and `f"{d:.2f}"`, but watch for downstream `isinstance(x, float)` checks.

**Testing locally**: if `moto` is unavailable or broken (its IAM backend pulls in a `cryptography`/`pyo3` dependency that can segfault in minimal containers), don't fight the library — write a ~20-line in-memory fake for `Table` (`get_item`/`put_item`/`delete_item`/`scan` over a dict) and monkeypatch it onto the module's `dynamodb` object. It exercises conditional writes and exception handling just as well.

## Silent NaN from insufficient history (pandas + rolling windows)

A subtler cousin of the above, hit in this project. Code fetched `period="6mo"` (~126 trading days) but computed `close.rolling(200).mean()`. With fewer rows than the window, pandas returns **all NaN**, and every comparison against NaN is `False` — so a "perfect order" trend check silently evaluated to `False` every single day. Nothing errored; the feature was simply dead.

When a rolling window's length is configurable, tie the required data length to it explicitly (a `min_rows` per preset) and reject rows below it, rather than assuming one fetch period fits all configurations. Verify by feeding synthetic data that *should* trigger the signal and asserting it actually does.
