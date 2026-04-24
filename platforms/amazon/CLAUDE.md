# platforms/amazon — Amazon SP-API Reports

Scoped context for the Amazon platform. The monorepo-wide [root CLAUDE.md](../../CLAUDE.md) covers shared AWS conventions, region, accounts, access model, toolchain, and Python rules — do not duplicate that here.

This platform has its own SAM template at [platforms/amazon/template.yaml](template.yaml). It imports shared resources from the base stack (see [infrastructure/base-stack.yaml](../../infrastructure/base-stack.yaml)) via `Fn::ImportValue`. Production writes to Secrets Manager go only through `DeploymentRole` (enforced by the `ProtectProductionSecrets` SCP).

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
- **App name**: TODO
- **Client ID (LWA)**: TODO — stored in Secrets Manager, never in code or config.
- **Marketplace ID**: `ATVPDKIKX0DER` (US).
- **Sellers**: six total, each identified internally by a short alias used as the path segment in secret names and S3 keys. `SH` (Sincerely Hers) and `KK` are onboarded to dev. The other four — `LLG`, `73J`, `OH`, `CO` — are pending authorization to the Sincerely Services SPP app (each seller grants the app OAuth access from their Seller Central).
- **Refresh tokens**: one per seller, stored in Secrets Manager (see naming below). Sincerely Hers's refresh token is the first one onboarded.
- **Credentials rule**: never hardcoded. All SP-API and AWS credentials come from Secrets Manager at runtime.

## Secrets Manager Naming

`sp-api/sincerely-services/{seller-alias}/credentials` — one secret per seller alias; stores refresh token and any per-seller credentials.

## First Milestone

**Complete (2026-04-21).** EventBridge cron → `ReportRequester` Lambda → SP-API `createReport` → DynamoDB (`REQUESTED`); then SP-API `REPORT_PROCESSING_FINISHED` → SQS (`sp-api-report-ready`, DLQ 3× redrive) → `ReportProcessor` Lambda → SP-API `getReportDocument` + download + gunzip → S3 (`sincerelyhers-reports-dev/amazon/sincerely-services/SH/.../{reportId}.tsv`) → DynamoDB `COMPLETED` → SES email with 12-hour pre-signed URL. Full round-trip verified against dev in 33–39 seconds end-to-end.

Out of scope for this milestone and still pending:

- Onboard sellers **LLG**, **73J**, **OH**, **CO** to the Sincerely Services SPP app; store each refresh_token at `sp-api/sincerely-services/{alias}/credentials`; add per-seller EventBridge schedule rules to `template.yaml`; and run `scripts/sp_api_notifications.py create-subscription {alias} {destinationId}` for each. Destination can be reused across sellers.
- Prod base and platform stacks (dev exercised first).
- Graduate pre-signed URL expiry from 12 h to 7 days (needs CloudFront signed URLs or long-lived IAM user creds — not a Lambda-role thing).
- Domain-identity SES + production-access request before prod cutover.

## Environmental knowns (SP-API app-level, outside this monorepo)

These exist in Amazon's SP-API infrastructure, attached to the Sincerely Services app, and are **not managed by this codebase**. Recorded here so they don't get rediscovered later as surprises.

- **Active `createReportSchedule` rule** (discovered 2026-04-21 via `getReportSchedules`):
  - `reportScheduleId = 50024019947`
  - `reportType = GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA`
  - `period = PT4H`, `marketplaceIds = [ATVPDKIKX0DER]`
  - Origin unknown — predates this monorepo.
- **External integration in account `637445353164` (prod)** is actively using the Sincerely Services app with SH's credentials. Evidence:
  - Pre-existing notification destination `SH-OrderChange-Queue` (destinationId `6f5f7648-a6d5-41cc-8916-7c470f48c22c`) points at an `OrderChangesQueue` in that account.
  - 24h `getReports` audit shows ~30 `GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA` and ~4 `GET_FLAT_FILE_OPEN_LISTINGS_DATA` reports per day that this monorepo did not request, at a cadence higher than the PT4H schedule would produce (implies direct `createReport` calls from that integration in addition to the schedule).
  - That integration does **not** appear to use SP-API Notifications — our `REPORT_PROCESSING_FINISHED` subscription succeeded at creation time, and SP-API allows only one subscription per `(seller × app × notificationType)`, so no prior subscription existed. It likely polls `getReports` to discover completed reports.
- **Runtime consequence**: every report the external integration requests via the Sincerely Services app for SH produces a `REPORT_PROCESSING_FINISHED` event that Amazon routes to our `sp-api-report-ready` queue, regardless of which tool issued the underlying `createReport`. `ReportProcessor` catches the `KeyError` from `dynamodb.get_job` on these unknown report_ids, emits a `WARN` log, and drops the SQS message — no DDB write, no S3 write, no email. Expect roughly 30 such skips per day in dev. SQS costs are trivial (<$0.01/mo at that volume).
- **Eventual cleanup options** (not required for current operation): (a) track down and migrate the external integration into this monorepo, at which point the "unknown report" skip becomes dead code; (b) leave the two systems running side by side indefinitely; (c) restrict our subscription to narrower notification criteria if SP-API adds filter support.
