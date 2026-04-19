# platforms/amazon — Amazon SP-API Reports

Scoped context for the Amazon platform. The monorepo-wide [root CLAUDE.md](../../CLAUDE.md) covers shared AWS conventions, region, accounts, toolchain, and Python rules — do not duplicate that here.

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
- **Sellers**: four total. Sincerely Hers is seller #1; the other three aliases are TODO.
- **Refresh tokens**: one per seller, stored in Secrets Manager (see naming below). Sincerely Hers's refresh token is the first one onboarded.
- **Credentials rule**: never hardcoded. All SP-API and AWS credentials come from Secrets Manager at runtime.

## Secrets Manager Naming

`sp-api/sincerely-services/{seller-alias}/credentials` — one secret per seller alias; stores refresh token and any per-seller credentials.

## First Milestone

Stand up the SAM project skeleton and a single `createReport` → S3 path for Sincerely Hers, driven by an EventBridge rule in the dev account. Downstream pieces (DynamoDB job tracking, SES delivery, per-queue DLQs, additional sellers) come after that round-trip works end to end.
