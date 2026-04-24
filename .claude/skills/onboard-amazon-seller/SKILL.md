---
name: onboard-amazon-seller
description: Onboard a new Amazon seller alias to dev. Generates the EventBridge schedule rule + Lambda invoke permission in platforms/amazon/template.yaml, creates the smoke-test event payload, walks through the external SPP authorization + Secrets Manager + SP-API subscription steps, deploys, and verifies against deployed reality. Use when the user says "onboard <alias>", "add Amazon seller", or names one of the pending aliases (LLG, 73J, OH, CO).
---

# Onboard Amazon Seller

End-to-end ritual for adding a new seller alias to the Amazon SP-API integration in dev. Each step has a verification — the whole point is to never update CLAUDE.md from memory; always from queried AWS state.

## Inputs

- `alias` (required) — short seller code, e.g. `LLG`, `73J`, `OH`, `CO`
- `report_type` (default `GET_FLAT_FILE_OPEN_LISTINGS_DATA`)
- `marketplace_id` (default `ATVPDKIKX0DER`, US)
- `lookback_days` (default `1`)

## Preflight (workstation-portability checks)

Run these **before** doing anything else. Bob works across multiple workstations and per-machine setup is required.

1. **AWS profile present?**
   ```
   aws configure list-profiles | grep -q '^sincerelyhers-dev$'
   ```
   If missing, surface this and stop:
   > Run `aws configure sso --profile sincerelyhers-dev` in a real terminal (not the Claude `!` prompt — it has no TTY). Use `sincerelyhers` as the SSO session name; SSO region `us-east-2`. Get the start URL from the IAM Identity Center dashboard in the management account.

2. **Alias not already deployed?**
   ```
   aws cloudformation list-stack-resources --stack-name sincerelyhers-amazon-dev \
     --profile sincerelyhers-dev --region us-east-2 \
     --query "StackResourceSummaries[?contains(LogicalResourceId,'<Alias>')].LogicalResourceId" --output text
   ```
   Should be empty. If anything returns, this seller is already onboarded — stop and verify state instead.

3. **Alias not already in template?**
   ```
   grep -E "^\s*<Alias>ReportScheduleRule" platforms/amazon/template.yaml
   ```
   Should be empty.

## Step 1 — Compute the cron slot

Convention: stagger seller report runs by **+15 minutes** to avoid simultaneous Lambda cold starts. Find the latest slot in use:

```
grep -oE 'cron\([0-9]+ [0-9]+' platforms/amazon/template.yaml | sort -k1.7n -k1.10n
```

Then add 15 minutes; roll past `:45` into the next hour. Current and projected sequence:

| Alias | Cron |
|---|---|
| SH  | `cron(0 8 * * ? *)`  |
| KK  | `cron(15 8 * * ? *)` |
| LLG | `cron(30 8 * * ? *)` |
| 73J | `cron(45 8 * * ? *)` |
| OH  | `cron(0 9 * * ? *)`  |
| CO  | `cron(15 9 * * ? *)` |

## Step 2 — Add resources to template.yaml

Append two resources after the last per-seller block. **Read the existing `KKReportScheduleRule` and `KKReportRequesterInvokePermission` blocks first** — copy that exact structure rather than reinventing it. The pattern is:

```yaml
  <LogicalIdPrefix>ReportScheduleRule:
    Type: AWS::Events::Rule
    Properties:
      Name: !Sub "${Environment}-<alias_lower>-daily-report"
      Description: "Daily open-listings report for <ALIAS>"
      ScheduleExpression: "<computed cron>"
      State: ENABLED
      Targets:
        - Arn: !GetAtt ReportRequesterFunction.Arn
          Id: ReportRequesterTarget
          Input: >
            {
              "seller_alias": "<ALIAS>",
              "marketplace_id": "<marketplace_id>",
              "report_type": "<report_type>",
              "lookback_days": <lookback_days>
            }

  <LogicalIdPrefix>ReportRequesterInvokePermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !Ref ReportRequesterFunction
      Action: lambda:InvokeFunction
      Principal: events.amazonaws.com
      SourceArn: !GetAtt <LogicalIdPrefix>ReportScheduleRule.Arn
```

### Logical ID sanitization (subtle)

CloudFormation logical IDs **must start with a letter** and be alphanumeric. The alias `73J` starts with a digit, so use `Seller73J` as the `<LogicalIdPrefix>` (the `Name` and Input payload still use the raw alias `73J`). For all other current aliases, use the alias verbatim as the prefix.

| Alias | LogicalIdPrefix |
|---|---|
| LLG | `LLG` |
| 73J | `Seller73J` |
| OH  | `OH` |
| CO  | `CO` |

## Step 3 — Create smoke-test event

Write `events/request_report_<alias_lower>.json`:

```json
{
  "seller_alias": "<ALIAS>",
  "marketplace_id": "<marketplace_id>",
  "report_type": "<report_type>",
  "lookback_days": <lookback_days>
}
```

## Step 4 — External steps (manual + CLI)

These cannot be done from the repo. Surface them clearly to the user:

**a) SPP authorization (manual, in browser)**
- Bob logs into the seller's Seller Central
- Authorizes the "Sincerely Services" SPP app
- Captures the refresh token from the OAuth callback URL
- Stop and wait for Bob to confirm before continuing

**b) Store refresh token in dev Secrets Manager** (dev only — prod is locked to `DeploymentRole` by SCP):
```
aws secretsmanager create-secret \
  --name sp-api/sincerely-services/<ALIAS>/credentials \
  --secret-string '{"refresh_token":"<TOKEN>"}' \
  --profile sincerelyhers-dev --region us-east-2
```

**c) Subscribe to REPORT_PROCESSING_FINISHED**:
```
uv run python scripts/sp_api_notifications.py create-subscription <ALIAS> <destinationId>
```
Reuse the existing `dev-sp-api-report-ready` destination; one destination is shared across sellers.

## Step 5 — Validate template

```
sam validate --template platforms/amazon/template.yaml --profile sincerelyhers-dev --region us-east-2 --lint
```

Stop and fix errors before deploying.

## Step 6 — Deploy

```
make build-amazon
make deploy-amazon-dev
```

If `sam build` hangs (known WSL2 + Docker Desktop issue per KK commit), suggest reboot and retry.

## Step 7 — Verify deployment against AWS reality

This is **the** value-add of the skill. Never edit CLAUDE.md without running this. Five reads + one async drift check, all read-only:

```bash
# 1. Stack status
aws cloudformation describe-stacks --stack-name sincerelyhers-amazon-dev \
  --profile sincerelyhers-dev --region us-east-2 \
  --query 'Stacks[0].[StackStatus,LastUpdatedTime]' --output table

# 2. Resources for this alias
aws cloudformation list-stack-resources --stack-name sincerelyhers-amazon-dev \
  --profile sincerelyhers-dev --region us-east-2 \
  --query "StackResourceSummaries[?contains(LogicalResourceId,'<ALIAS>')].[LogicalResourceId,ResourceStatus,LastUpdatedTimestamp]" --output table

# 3. EventBridge rule
aws events describe-rule --name dev-<alias_lower>-daily-report \
  --profile sincerelyhers-dev --region us-east-2

# 4. Secret presence (metadata only — never retrieve the value)
aws secretsmanager describe-secret --secret-id sp-api/sincerely-services/<ALIAS>/credentials \
  --profile sincerelyhers-dev --region us-east-2 \
  --query '[Name,LastChangedDate,LastAccessedDate]' --output table

# 5. SAM template still valid
sam validate --template platforms/amazon/template.yaml --profile sincerelyhers-dev --region us-east-2 --lint

# 6. Drift detection (async — kick off, then poll)
DRIFT_ID=$(aws cloudformation detect-stack-drift --stack-name sincerelyhers-amazon-dev \
  --profile sincerelyhers-dev --region us-east-2 --query 'StackDriftDetectionId' --output text)
until [ "$(aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id $DRIFT_ID --profile sincerelyhers-dev --region us-east-2 --query 'DetectionStatus' --output text)" != "DETECTION_IN_PROGRESS" ]; do sleep 3; done
aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id $DRIFT_ID \
  --profile sincerelyhers-dev --region us-east-2 --output json
```

**All must be green:** stack `UPDATE_COMPLETE`, both resources `CREATE_COMPLETE`, rule `ENABLED` with the right cron, secret present, validate clean, drift `IN_SYNC` with `DriftedStackResourceCount: 0`. If any fail, **do not** update CLAUDE.md — investigate first.

## Step 8 — Smoke-test invoke (optional but recommended)

```
sam local invoke ReportRequesterFunction --event events/request_report_<alias_lower>.json --profile sincerelyhers-dev
```

Or invoke the deployed Lambda directly:
```
aws lambda invoke --function-name <Sub-Stack-Function-Name> \
  --payload file://events/request_report_<alias_lower>.json \
  --profile sincerelyhers-dev --region us-east-2 /tmp/out.json
```

Watch CloudWatch Logs for the function and confirm a `REQUESTED` row lands in the DynamoDB jobs table.

## Step 9 — Update CLAUDE.md

Only after Step 7 is fully green. Two files:

- **`CLAUDE.md`** (root): in the "Amazon platform identifier TODOs" line, move the alias out of the pending list and add it to the onboarded list.
- **`platforms/amazon/CLAUDE.md`**: in the **Sellers** bullet (line ~34), move the alias out of the pending group. In the "Out of scope" section (line ~48), drop the alias from the onboarding TODO.

## Step 10 — Propose commit message

Two commits, in order:

**Commit 1** — template + event:
```
Onboard <ALIAS> seller: EventBridge schedule + smoke-test event

<seller> Seller Central authorized the Sincerely Services SPP app.
Refresh token stored at sp-api/sincerely-services/<ALIAS>/credentials
(dev) and REPORT_PROCESSING_FINISHED subscription created against
the shared dev-sp-api-report-ready destination.

Rule fires <N> minutes after <prior alias>'s daily rule to avoid
simultaneous Lambda cold starts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

**Commit 2** — doc updates (after deploy verified):
```
Mark <ALIAS> seller onboarded in CLAUDE.md

<ALIAS> CloudFormation deploy verified: stack UPDATE_COMPLETE, both
resources CREATE_COMPLETE, rule ENABLED with cron(<min> <hr> * * ? *),
secret present, drift IN_SYNC.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Per Bob's CLAUDE.md rule: **propose** the commit messages, do not commit unless asked.

## Anti-patterns

- ❌ Do **not** write to **prod** Secrets Manager from the CLI — only `DeploymentRole` (via `sam deploy`) is permitted; `ProtectProductionSecrets` SCP will deny anything else.
- ❌ Do **not** pick a cron slot that collides with another seller — always +15 min from the latest in use.
- ❌ Do **not** skip the verify step (Step 7) — the entire reason this skill exists is to keep CLAUDE.md aligned with deployed reality, not memory.
- ❌ Do **not** update CLAUDE.md before drift is `IN_SYNC` — partial deploys leave stale docs.
- ❌ Do **not** use `git@github.com` for any push — Bob's repos use the SSH alias `git@github-personal` (per his global CLAUDE.md).
- ❌ Do **not** retrieve the secret **value** during verification — `describe-secret` (metadata only) is enough; pulling the value would log an unnecessary access.
