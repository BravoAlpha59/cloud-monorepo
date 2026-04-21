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
- **Sellers**: five total, each identified internally by a short alias used as the path segment in secret names and S3 keys. `SH` (Sincerely Hers) is seller #1 and the first onboarded. The other four — `KK`, `LLG`, `73J`, `OH` — are pending authorization to the Sincerely Services SPP app (each seller grants the app OAuth access from their Seller Central).
- **Refresh tokens**: one per seller, stored in Secrets Manager (see naming below). Sincerely Hers's refresh token is the first one onboarded.
- **Credentials rule**: never hardcoded. All SP-API and AWS credentials come from Secrets Manager at runtime.

## Secrets Manager Naming

`sp-api/sincerely-services/{seller-alias}/credentials` — one secret per seller alias; stores refresh token and any per-seller credentials.

## First Milestone

**Complete (2026-04-21).** EventBridge cron → `ReportRequester` Lambda → SP-API `createReport` → DynamoDB (`REQUESTED`); then SP-API `REPORT_PROCESSING_FINISHED` → SQS (`sp-api-report-ready`, DLQ 3× redrive) → `ReportProcessor` Lambda → SP-API `getReportDocument` + download + gunzip → S3 (`sincerelyhers-reports-dev/amazon/sincerely-services/SH/.../{reportId}.tsv`) → DynamoDB `COMPLETED`. First full round-trip verified against dev on 2026-04-21 in 39 seconds end-to-end (requested 23:09:51 → completed 23:10:30, 6.2 MB TSV written to S3).

Out of scope for this milestone and still pending:

- SES delivery of a 7-day pre-signed URL. Optional per-rule; wire it when the first recipient list is known.
- Onboard sellers **KK**, **LLG**, **73J**, **OH** to the Sincerely Services SPP app; store each refresh_token at `sp-api/sincerely-services/{alias}/credentials`; add per-seller EventBridge schedule rules to `template.yaml`; and run `scripts/sp_api_notifications.py create-subscription {alias} {destinationId}` for each. Destination can be reused across sellers.
- Prod base and platform stacks (dev exercised first).
