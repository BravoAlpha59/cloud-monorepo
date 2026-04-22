# Architecture

Design specification for cloud-monorepo services. Complements [CLAUDE.md](CLAUDE.md) (locked decisions, always loaded) with the detail that would otherwise spread across code comments or bloat always-loaded context.

**Scope today**: Amazon SP-API Reports pipeline for Sincerely Services. Expanded as other platforms are designed.

---

## Amazon SP-API Reports

### Data Flow

```
              ┌────────────────────┐
              │  EventBridge rule  │  cron per (seller_alias × report_type)
              │  (payload below)   │
              └─────────┬──────────┘
                        ▼
              ┌────────────────────┐
              │  ReportRequester   │  <2s; creds from Secrets Manager
              │       Lambda       │  → SP-API create_report
              └─────────┬──────────┘
                        ▼
              DynamoDB AmazonReportJobs   (status = REQUESTED)

              · · · Amazon processes the report (seconds to hours) · · ·

              ┌────────────────────────────────────────────┐
              │  SP-API Notifications API                  │
              │  REPORT_PROCESSING_FINISHED → destination  │
              └────────────────────┬───────────────────────┘
                                   ▼
                 SQS: sp-api-report-ready
                 (vis. timeout 5m; maxReceive 3 → DLQ)
                                   │
                                   ▼
              ┌────────────────────┐
              │  ReportProcessor   │  → SP-API get_report_document
              │       Lambda       │  → download + gunzip
              └─────────┬──────────┘  → S3 PutObject
                        ▼              → DynamoDB UpdateItem
              DynamoDB AmazonReportJobs   (status = COMPLETED)
                        │
                        ▼
                     SES SendEmail  (optional, per rule config)
                        │
                        ▼
              Recipient receives 12-hour pre-signed S3 URL
```

On unhandled exception in either Lambda: `status = FAILED` with `error_message`. SQS retries up to 3× then routes the message to `sp-api-report-ready-dlq`.

---

### Components

#### EventBridge Rules

One rule per `(seller_alias × report_type)`. Cron or rate expression. Payload shape:

```json
{
  "seller_alias": "SH",
  "marketplace_id": "ATVPDKIKX0DER",
  "report_type": "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
  "lookback_days": 1
}
```

Adding a report type or seller is a template change plus a new rule — no handler code changes.

#### ReportRequester Lambda

- Reads credentials JSON from Secrets Manager at `sp-api/sincerely-services/{seller_alias}/credentials` (keys: `client_id`, `client_secret`, `refresh_token`).
- Computes `dataStartTime = now - lookback_days`, `dataEndTime = now`.
- Calls `create_report` via `python-amazon-sp-api`.
- Writes `AmazonReportJobs` row with `status = REQUESTED`.
- Typical runtime <2s. Memory 256 MB, timeout 30s.

#### SP-API Notifications (one-time bootstrap)

Two API calls, each one-shot:

1. `createDestination` — once per SPP app × AWS region × SQS queue. Registers the queue ARN as a delivery target for the app. Grantless call; uses only the app's LWA `client_id` + `client_secret`. Returns a `destinationId` that survives for the life of the app.
2. `createSubscription` — once per seller × notification type. Subscribes the seller's events (currently `REPORT_PROCESSING_FINISHED`) to the destination. Uses the seller's refresh-token creds.

Driven by [scripts/sp_api_notifications.py](scripts/sp_api_notifications.py), a stand-alone CLI (not a runtime component): `list-destinations`, `create-destination <name> <queue-arn>`, `show-subscription <seller> [<type>]`, `create-subscription <seller> <destination-id> [<type>]`. Credentials are pulled from the same Secrets Manager path the Lambdas use (`sp-api/sincerely-services/<alias>/credentials`).

SP-API can only write to the queue if the queue's resource policy grants `sqs:SendMessage` to the SP-API notifications service principal (`arn:aws:iam::437568002678:root`). This is baked into `platforms/amazon/template.yaml` as `ReportReadyQueuePolicy` and applied on every stack deploy.

#### SQS: sp-api-report-ready

- Standard queue (ordering unnecessary).
- Visibility timeout: 5 min (> ReportProcessor Lambda timeout).
- `maxReceiveCount: 3` → DLQ `sp-api-report-ready-dlq`.
- SQS-managed encryption (SSE-SQS).

#### ReportProcessor Lambda

- SQS-triggered (start with batch size 1; tune later).
- Extracts `reportDocumentId`, calls `get_report_document` for a short-lived (~5 min) pre-signed download URL — must download immediately.
- Gunzips if `compressionAlgorithm` is set.
- Writes raw file to S3 (key convention below).
- Updates `AmazonReportJobs` with `status = COMPLETED`, `s3_key`, `completed_at`.
- Optional SES email per rule — uses a freshly-generated pre-signed S3 URL (not the 5-min SP-API one). Current effective expiry is 12 hours (bounded by `ReportProcessorExecutionRole.MaxSessionDuration`); a true 7-day URL would need long-lived IAM user credentials or CloudFront signed URLs and is deferred.
- Memory 512 MB, timeout 4 min (SQS visibility 5 min leaves a 1-min safety margin).

#### S3 Bucket: `sincerelyhers-reports-{env}`

One bucket per environment, cross-platform.

- Key convention: `{platform}/{app-context}/{seller-alias}/{report-type}/{YYYY-MM-DD}/{report-id}.{ext}`
  - Example: `amazon/sincerely-services/SH/GET_FLAT_FILE_OPEN_LISTINGS_DATA/2026-04-20/abc123.tsv`
- Versioning: off (reports are immutable).
- Lifecycle: Standard-IA at 30 days, Glacier at 90 days.
- Public access: fully blocked.
- Encryption: SSE-S3 (move to SSE-KMS only if audit needs it).
- Lives in **base stack**; exported as `SincerelyhersReportsBucketArn` / `SincerelyhersReportsBucketName`.

#### DynamoDB: `AmazonReportJobs`

Amazon-specific schema → lives in **platform stack** (`platforms/amazon/template.yaml`).

| Attribute | Type | Notes |
|---|---|---|
| `report_id` | S | Partition key. From SP-API `createReport` response. |
| `seller_alias` | S | e.g. `SH` |
| `report_type` | S | e.g. `GET_FLAT_FILE_OPEN_LISTINGS_DATA` |
| `marketplace_id` | S | `ATVPDKIKX0DER` for US |
| `status` | S | `REQUESTED` / `PROCESSING` / `COMPLETED` / `FAILED` |
| `requested_at` | S | ISO-8601 |
| `completed_at` | S | ISO-8601; null until `COMPLETED` |
| `document_id` | S | null until notification arrives |
| `s3_key` | S | null until `COMPLETED` |
| `error_message` | S | null unless `FAILED` |

- Billing: pay-per-request (volume is low).
- Future GSI (add only when a dashboard needs it): PK `seller_alias`, SK `requested_at`.

##### Status lifecycle

```
REQUESTED ──▶ PROCESSING ──▶ COMPLETED
    │              │
    ▼              ▼
  FAILED         FAILED
```

- `REQUESTED` — set by ReportRequester on `create_report` success.
- `PROCESSING` — set by ReportProcessor when it begins handling the SQS message.
- `COMPLETED` — set after successful S3 write.
- `FAILED` — any unhandled exception; `error_message` populated.

#### SES

- One verified sender identity per environment (sandbox initially; request production access before cutover).
- Email body: report type, seller, report id, size, clickable pre-signed S3 URL (12-hour expiry; see ReportProcessor section), plain `s3://` path for recipients with direct bucket access.
- Recipient list sourcing TBD — likely a small DynamoDB `NotificationRules` table or CFN parameter when the need arrives.
- Lives in **base stack**; exported as `SincerelyhersSesSenderArn`.

#### IAM Execution Roles

One role per Lambda. Named `{Function}ExecutionRole`. Lives in the platform stack.

**AmazonReportRequesterExecutionRole**
```
secretsmanager:GetSecretValue  on  arn:aws:secretsmanager:us-east-2:*:secret:sp-api/sincerely-services/*
dynamodb:PutItem, UpdateItem    on  AmazonReportJobs
logs:CreateLogGroup, CreateLogStream, PutLogEvents  on  its own log group
```

**AmazonReportProcessorExecutionRole**
```
secretsmanager:GetSecretValue   on  arn:aws:secretsmanager:us-east-2:*:secret:sp-api/sincerely-services/*
sqs:ReceiveMessage, DeleteMessage, GetQueueAttributes  on  sp-api-report-ready
s3:PutObject                    on  {SincerelyhersReportsBucketArn}/amazon/*
dynamodb:GetItem, UpdateItem    on  AmazonReportJobs
ses:SendEmail                   on  {SincerelyhersSesSenderArn}
logs:CreateLogGroup, CreateLogStream, PutLogEvents  on  its own log group
```

Lambdas never receive `AdministratorAccess` or wildcard service policies.

---

## Base Stack ↔ Platform Stack

**Base stack** (`infrastructure/base-stack.yaml`) — deployed once per environment.

Exports:
- `SincerelyhersReportsBucketArn`, `SincerelyhersReportsBucketName`
- `SincerelyhersSesSenderArn`
- (Future) shared KMS key ARN if/when we move off SSE-S3.

**Amazon platform stack** (`platforms/amazon/template.yaml`).

Imports (`Fn::ImportValue`):
- `SincerelyhersReportsBucketArn` / `SincerelyhersReportsBucketName`
- `SincerelyhersSesSenderArn`

Contains:
- `ReportRequester` + `ReportProcessor` Lambdas and their execution roles
- SQS `sp-api-report-ready` + DLQ
- EventBridge schedule rules (per seller × report-type)
- DynamoDB `AmazonReportJobs`

---

## Local Development

- `sam local invoke ReportRequester --event events/request_report.json --profile sincerelyhers-dev`
- `events/` directory holds sample payloads:
  - `events/request_report.json` — EventBridge payload (shape above)
  - `events/report_ready.json` — SQS message from `REPORT_PROCESSING_FINISHED`
- Tests: `uv run pytest platforms/amazon/tests/` using `moto` for AWS mocks and `pytest-mock` for `sp_api` mocks (added to dev dependencies when the first test is written).

---

## Out of Scope (future work)

- **Walmart / Shopify / Target / Faire** — not yet designed. Each platform adds its own section here + its own `platforms/{name}/CLAUDE.md` + platform stack.
- **SincerelySaaS** — customer-facing SaaS. Identity direction locked (Cognito User Pools; see [decisions-log.md](decisions-log.md)); rest deferred. When designed, gets its own account under the `sincerelyhers-saas` OU.
- **Shared data normalization** — `Order` / `OrderLine` / `Product` / `Inventory` / `Report` under `shared/`. Relevant when a second platform comes online.
- **OrderChangeProcessor** — per summary 01, a separate pipeline on an `sp-api-order-changes` queue. Deferred past the first milestone.
