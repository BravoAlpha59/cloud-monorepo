# platforms/amazon — Amazon SP-API Reports

Scoped context for the Amazon platform. The monorepo-wide [root CLAUDE.md](../../CLAUDE.md) covers shared AWS conventions, region, accounts, access model, toolchain, and Python rules — do not duplicate that here.

This platform has its own SAM template at [platforms/amazon/template.yaml](template.yaml). It imports shared resources from the base stack (see [infrastructure/base-stack.yaml](../../infrastructure/base-stack.yaml)) via `Fn::ImportValue`. Production writes to Secrets Manager go only through `DeploymentRole` (enforced by the `ProtectProductionSecrets` SCP).

**Visual references** (mermaid diagrams that render on GitHub):
- [docs/architecture/02-amazon-runtime.md](../../docs/architecture/02-amazon-runtime.md) — runtime sequence + resource topology
- [docs/architecture/04-secrets-and-auth.md](../../docs/architecture/04-secrets-and-auth.md) — per-seller secret layout + LWA token-exchange flow

## Overview

Serverless AWS system that uses the Amazon SP-API Reports API to schedule, retrieve, store, and deliver reports for Sincerely Hers plus three additional seller accounts. Operated by Sincerely Services as a private SP-API app (not published to the Selling Partner Appstore).

## Architecture — Locked Decisions

These are final. Do not re-open them.

- **Report scheduling**: EventBridge rules trigger a Lambda that calls `createReport`. We do **not** use SP-API's native `createReportSchedule` — we need explicit date-window control.
- **Queueing**: Separate SQS queues per notification type, each with its own DLQ.
- **Storage**: S3 for report payloads.
- **Job tracking**: DynamoDB.
- **Email delivery**: SES.
- **SP-API client library**: `python-amazon-sp-api`.

## Do Not Suggest

- Do **not** suggest `createReportSchedule` — EventBridge-triggered `createReport` is chosen for date-window control.
- Do **not** suggest a single shared SQS queue — per-notification-type queues with DLQs are the pattern.
- Do **not** suggest alternative SP-API clients — `python-amazon-sp-api` is the chosen library.

## SP-API App Context

- **App type**: private (Sincerely Services internal; not listed on the Appstore).
- **App name**: Sincerely Services
- **Client ID (LWA)**: `<LWA-CLIENT-ID>` — literal value lives in `.identifiers.local` (gitignored) and in each per-seller secret in Secrets Manager; never embedded in code or config.
- **Marketplace ID**: `ATVPDKIKX0DER` (US).
- **Sellers**: six total, each identified internally by a short alias used as the path segment in secret names and S3 keys. All six (`SH`, `KK`, `LLG`, `73J`, `OH`, `CO`) are onboarded to dev with all six rules currently `DISABLED` (SH/KK post-shake-out per commit 8f2a059; LLG/73J/OH/CO post-3-day-trial 2026-05-04, with trial S3 + DDB data deleted). Re-enable when prod cutover begins.
- **Refresh tokens**: one per seller, stored in Secrets Manager (see naming below). Sincerely Hers's refresh token is the first one onboarded.
- **Credentials rule**: never hardcoded. All SP-API and AWS credentials come from Secrets Manager at runtime.

## Secrets Manager Naming

Two secret paths under `sp-api/{app-prefix}/`:

- `app/credentials` — app-level, one secret per SPP app. `{client_id, client_secret}`. Rotated via the SP-API Application Management API (see [docs/design/credential-rotation.md](../../docs/design/credential-rotation.md)).
- `{seller-alias}/credentials` — per-seller, one secret per onboarded seller. `{refresh_token}` only. Issued at SPP self-authorization time and replaced if the seller re-authorizes; not rotatable via API.

The Lambda runtime reads both and merges before handing to `python-amazon-sp-api`. See `src/sincerelyhers_amazon/credentials.py`. App prefixes in use today: `sp-api/sincerely-services/` (live), `sp-api/bobnathan-test/` (sandbox for rotation smoke testing — only `app/credentials` populated).

In **dev** these credential secrets are created directly via the CLI. In **prod** the `ProtectProductionSecrets` SCP forbids CLI writes, so `app/credentials` + per-seller `credentials` are bootstrapped from a `DeploymentRole` deploy of [`secrets-template.yaml`](secrets-template.yaml) (`make deploy-amazon-secrets-prod`), a deliberately separate, rarely-deployed stack. Full prod sequence: [docs/handoffs/amazon-prod-cutover.md](../../docs/handoffs/amazon-prod-cutover.md).

### Odoo-webhook secrets (notification relay pattern)

For SP-API notifications relayed to Odoo webhooks (first instance: `FEED_PROCESSING_FINISHED` → `amazon_feed_status`), each seller has one secret per notification domain:

- **Secret name**: `sp-api/{app-prefix}/{seller-alias}/webhooks/{webhook-code}` — e.g. `sp-api/sincerely-services/KK/webhooks/amazon-feed`.
- **Shape**: `{secret, url, seller_id}` — HMAC key, Odoo endpoint URL, and the Amazon merchant ID for sellerId → alias dispatch at the relay Lambda.
- **`{webhook-code}`** mirrors the Odoo `webhook.endpoint` code (handler-key prefix, minus the trailing seller alias). The Odoo URL path component is `{webhook-code}-{alias-lower}`.

**Local staging file convention** (operator workflow, never committed):

- Each secret has a corresponding JSON file under `secrets/` (gitignored): `secrets/{webhook-code}-{alias-lower}.json` — e.g. `secrets/amazon-feed-kk.json`.
- The file name matches the Odoo URL path component exactly, so the file → URL → secret mapping is unambiguous when staging values for `aws secretsmanager create-secret --secret-string file://...`.
- The operator deletes the file after the secret is created.

This pattern generalizes to other notification types (order changes, listing changes, pricing, etc.) without renaming or migrating existing secrets — each new domain gets its own `webhooks/{webhook-code}` leaf under the same per-seller prefix.

## Credential rotation pipeline

A second SAM stack ([`rotation-template.yaml`](rotation-template.yaml)) deploys per-app rotation infrastructure: two SQS queues + DLQs (expiry, new-secret), a DynamoDB rotation-events table, and four Lambdas (`ExpiryHandler`, `RotationRequester`, `CredentialRotationProcessor`, `OldSecretMonitor`). Deployed once per SPP app via `make deploy-rotation-services-dev` and `make deploy-rotation-bobnathan-dev`. Design rationale, decision history, and smoke-test plan in [docs/design/credential-rotation.md](../../docs/design/credential-rotation.md). D3 currently policy C (alert-only); operator triggers rotation by `aws lambda invoke` of `RotationRequester`. D6 verify-then-write enforced in `CredentialRotationProcessor`; D7 daily monitoring via `OldSecretMonitor`.

## First Milestone

**Complete (2026-04-21).** EventBridge cron → `ReportRequester` Lambda → SP-API `createReport` → DynamoDB (`REQUESTED`); then SP-API `REPORT_PROCESSING_FINISHED` → SQS (`sp-api-report-ready`, DLQ 3× redrive) → `ReportProcessor` Lambda → SP-API `getReportDocument` + download + gunzip → S3 (`sincerelyhers-reports-dev/amazon/sincerely-services/SH/.../{reportId}.tsv`) → DynamoDB `COMPLETED` → SES email with 12-hour pre-signed URL. Full round-trip verified against dev in 33–39 seconds end-to-end.

Out of scope for this milestone and still pending:

- Prod base and platform stacks (dev exercised first).
- Graduate pre-signed URL expiry from 12 h to 7 days (needs CloudFront signed URLs or long-lived IAM user creds — not a Lambda-role thing).
- Domain-identity SES + production-access request before prod cutover.

## Environmental knowns (SP-API app-level, outside this monorepo)

These exist in Amazon's SP-API infrastructure, attached to the Sincerely Services app, and are **not managed by this codebase**. Recorded here so they don't get rediscovered later as surprises.

- **Active `createReportSchedule` rule** (discovered 2026-04-21 via `getReportSchedules`):
  - `reportScheduleId = <EXTERNAL-REPORT-SCHEDULE-ID>` (literal in `.identifiers.local`)
  - `reportType = GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA`
  - `period = PT4H`, `marketplaceIds = [ATVPDKIKX0DER]`
  - Origin unknown — predates this monorepo.
- **External integration in the prod account (`<PROD-ACCOUNT-ID>`)** is actively using the Sincerely Services app with SH's credentials. Evidence:
  - Pre-existing notification destination `SH-OrderChange-Queue` (destinationId `<EXTERNAL-DESTINATION-ID>`, literal in `.identifiers.local`) points at an `OrderChangesQueue` in that account.
  - 24h `getReports` audit shows ~30 `GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA` and ~4 `GET_FLAT_FILE_OPEN_LISTINGS_DATA` reports per day that this monorepo did not request, at a cadence higher than the PT4H schedule would produce (implies direct `createReport` calls from that integration in addition to the schedule).
  - That integration does **not** appear to use SP-API Notifications — our `REPORT_PROCESSING_FINISHED` subscription succeeded at creation time, and SP-API allows only one subscription per `(seller × app × notificationType)`, so no prior subscription existed. It likely polls `getReports` to discover completed reports.
- **Runtime consequence**: every report the external integration requests via the Sincerely Services app for SH produces a `REPORT_PROCESSING_FINISHED` event that Amazon routes to our `sp-api-report-ready` queue, regardless of which tool issued the underlying `createReport`. `ReportProcessor` catches the `KeyError` from `dynamodb.get_job` on these unknown report_ids, emits a `WARN` log, and drops the SQS message — no DDB write, no S3 write, no email. Expect roughly 30 such skips per day in dev. SQS costs are trivial (<$0.01/mo at that volume).
- **Eventual cleanup options** (not required for current operation): (a) track down and migrate the external integration into this monorepo, at which point the "unknown report" skip becomes dead code; (b) leave the two systems running side by side indefinitely; (c) restrict our subscription to narrower notification criteria if SP-API adds filter support.
