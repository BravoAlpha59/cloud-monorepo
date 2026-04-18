# cloud-monorepo — SP-API Reports System

## Project Overview

A serverless AWS system that uses the Amazon SP-API Reports API to schedule, retrieve, store, and deliver reports for Sincerely Hers and three additional seller accounts. Operated by Sincerely Services as a private SP-API app (not published to the Selling Partner Appstore).

## Architecture — Locked Decisions

These decisions are final. Do not re-open them or suggest alternatives.

- **Report scheduling**: EventBridge rules trigger a Lambda that calls `createReport`. We do **not** use SP-API's native `createReportSchedule` — we need explicit date-window control.
- **Queueing**: Separate SQS queues per notification type, each with its own DLQ.
- **Storage**: S3 for report payloads.
- **Job tracking**: DynamoDB.
- **Email delivery**: SES.
- **IaC**: CloudFormation via AWS SAM.
- **Language / SDK**: Python with the `python-amazon-sp-api` library and `boto3` for AWS.
- **Region**: `us-east-2` for all resources.
- **Accounts**: two-account model
  - Dev: `sincerelyhers-dev` (account ID: TODO)
  - Prod: `sincerelyhers` (account ID: `637445353164`)

## Do Not Suggest

- Do **not** suggest `createReportSchedule` — EventBridge-triggered `createReport` is chosen for date-window control.
- Do **not** suggest a single shared SQS queue — per-notification-type queues with DLQs are the pattern.
- Do **not** suggest alternative IaC (CDK, Terraform, Serverless Framework) — SAM is the standard.
- Do **not** suggest alternative SP-API clients — `python-amazon-sp-api` is the chosen library.
- Do **not** suggest alternative regions — everything lives in `us-east-2`.
- Do **not** suggest storing credentials in env vars, code, or parameter files — Secrets Manager only.

## AWS Account Context

- **Region**: `us-east-2`
- **Dev account**: `sincerelyhers-dev` — account ID TODO
- **Prod account**: `sincerelyhers` — account ID `637445353164`
- **IAM user (current)**: TODO
- **IAM Identity Center**: planned, not yet configured. Until then, operate via the IAM user above.
- **Secrets Manager naming**: `sp-api/sincerely-services/{seller-alias}/credentials`
  - One secret per seller alias, storing refresh token and any per-seller credentials.

## SP-API App Context

- **App type**: private (Sincerely Services internal; not listed on the Appstore)
- **App name**: TODO
- **Client ID (LWA)**: TODO — stored in Secrets Manager, never in code or config
- **Marketplace ID**: `ATVPDKIKX0DER` (US)
- **Sellers**: four total. Sincerely Hers is seller #1; the other three are TODO (aliases to be decided).
- **Refresh tokens**: one per seller, stored under `sp-api/sincerely-services/{seller-alias}/credentials`. Sincerely Hers's refresh token is the first one onboarded.
- **Credentials rule**: never hardcoded. All SP-API and AWS credentials come from Secrets Manager at runtime.

## Repository State

Clean slate — nothing is implemented yet.

**First milestone**: stand up the SAM project skeleton and a single `createReport` → S3 path for Sincerely Hers, driven by an EventBridge rule in the dev account. Downstream pieces (DynamoDB job tracking, SES delivery, per-queue DLQs, additional sellers) come after that round-trip works end to end.

## Coding Conventions

- **Python**: 3.12, type hints preferred.
- **AWS SDK**: `boto3`.
- **SP-API**: `python-amazon-sp-api`.
- **Deployment**: AWS SAM (`sam build` / `sam deploy`).
- **Testing**: `pytest`.
- Follow the Python style rules in `~/.claude/CLAUDE.md` (Ruff formatting, `Optional[T]` over `T | None`, builtin generics, namespace imports for internal functions, direct imports for classes/exceptions, `pathlib.Path` over `os.path`, no shebangs on scripts).
