# Integration contract: Amazon `DATA_KIOSK_QUERY_PROCESSING_FINISHED` → `webhook_sh`

**Status:** active · **Owners:** Odoo (`amazon_sh`) + AWS relay (`myAWS` distro) ·
**Canonical copy:** this file; mirror into the `myAWS` repo.

This is the shared source of truth for the seam between two projects. Amazon delivers
`DATA_KIOSK_QUERY_PROCESSING_FINISHED` notifications to an SQS queue in the AWS project;
a relay there forwards each one to this Odoo instance, which updates the matching
Data Kiosk query record and downloads/parses its result. Each side is built and tested
independently against this contract. It is the Data Kiosk sibling of
[`feed_processing_finished_webhook.md`](feed_processing_finished_webhook.md) and reuses
the same transport, auth, and relay design.

## The seam

```
amazon.sh.datakiosk.query.submit  (createQuery)
  → queryId stored on amazon.sh.datakiosk.query (IN_QUEUE)
  → Amazon finishes → DATA_KIOSK_QUERY_PROCESSING_FINISHED → SQS       [AWS]
  → relay POSTs raw notification (HMAC-signed) →                       [AWS → Odoo]
  POST https://<public-host>/webhook/<code>                            [contract]
  → _handle_amazon_datakiosk_status: match queryId, set status,        [Odoo]
    getDocument → download JSONL → parse into amazon.sh.datakiosk.economics,
    chain the next page when pagination.nextToken is present
```

## Transport & auth

| Item | Value |
|---|---|
| Method / URL | `POST https://<public-host>/webhook/<code>` |
| Endpoints | **one per private-app seller** — `amazon-datakiosk-73j`, `-co`, `-kk`, `-llg`, `-oh`, `-sh` |
| Body | **raw** `DATA_KIOSK_QUERY_PROCESSING_FINISHED` notification JSON (SQS message body, no envelope) |
| Content-Type | `application/json` |
| Auth | **HMAC-SHA256** over the raw body, header `X-Hub-Signature-256` (a `sha256=` prefix is accepted) |
| Secret | per-seller; **Odoo `webhook.endpoint.secret` is the source of truth**, mirrored into AWS (Secrets Manager) |
| De-dup | `payload.queryId` |

The endpoints are seeded by `amazon_sh`
(`data/amazon_sh_datakiosk_webhook_endpoint_data.xml`) with `auth_type=hmac` and **no
secret** — an HMAC endpoint with no secret rejects every request (401), so each goes
live only once its secret is set in the UI. A seller whose endpoint is unconfigured
degrades gracefully to the polling cron.

## Payload (raw SP-API notification)

Unlike the feed notification (which nests its fields under
`payload.feedProcessingFinishedNotification`), the Data Kiosk query fields sit **directly
under `payload`**.

```json
{
  "notificationVersion": "2023-11-15",
  "notificationType": "DATA_KIOSK_QUERY_PROCESSING_FINISHED",
  "payloadVersion": "2023-11-15",
  "eventTime": "2023-12-23T21:30:13.713Z",
  "payload": {
    "accountId": "amzn1.merchant.o.ABCD0123456789",
    "queryId": "54517018502",
    "query": "query MyQuery{ ... }",
    "processingStatus": "DONE",
    "dataDocumentId": "amzn1.tortuga.4.na.<...>.REP4567URI9BMZ",
    "errorDocumentId": null,
    "pagination": { "nextToken": "AAMA-..." }
  },
  "notificationMetadata": {
    "applicationId": "amzn1.sellerapps.app.aacc...",
    "subscriptionId": "subscription-id-d0e9e693-...",
    "publishTime": "2023-12-23T21:30:16.903Z",
    "notificationId": "d0e9e693-c3ad-4373-979f-ed4ec98dd746"
  }
}
```

Fields the Odoo handler consumes (all under `payload`):

| Field | Use |
|---|---|
| `queryId` | match key against `amazon.sh.datakiosk.query.query_id`; also the de-dup id |
| `processingStatus` | enum `IN_QUEUE` \| `IN_PROGRESS` \| `DONE` \| `CANCELLED` \| `FATAL` — written to `processing_status` |
| `dataDocumentId` | when present (DONE), downloaded + parsed via `_retrieve_result_document` |
| `errorDocumentId` | when present (FATAL), downloaded + attached (no parse) |
| `pagination.nextToken` | stored; drives the paginated follow-up `createQuery` |
| `accountId` | informational (the endpoint + its secret already identify the seller) |

## Odoo handler behavior (`amazon_sh`)

`webhook.endpoint` gains a handler key `amazon_datakiosk_status`:

1. Parse the inner notification; ignore anything that isn't `DATA_KIOSK_QUERY_PROCESSING_FINISHED`.
2. `search(amazon.sh.datakiosk.query, query_id == queryId)` with `active_test=False`
   (so a query archived before its result arrives is still matched). **No match →
   ignore** — the notification fires for *all* queries on the account, so queries we did
   not submit have no record.
3. Set `processing_status = processingStatus` and store `pagination.nextToken`.
4. If `dataDocumentId` present → `_retrieve_result_document` (downloads the JSONL,
   attaches it, parses into `amazon.sh.datakiosk.economics`, sets `RETRIEVED`, and — when
   `next_token` is set — chains the next page via a new `createQuery(paginationToken)`).
   If instead `errorDocumentId` present → download + attach the error document (no parse).
   These are in-request Amazon `getDocument` calls; a failure raises → HTTP 500 → the
   relay retries.
5. `_external_id_amazon_datakiosk_status` returns `queryId` → redeliveries become
   `duplicate` no-ops.

The polling cron `update_datakiosk_query_status` stays enabled as a **backstop** for any
dropped notification (consider lowering its frequency once the push path is trusted).

## Submission-side throttling (`createQuery`)

Independent of the notification/relay path below, the **submission** leg has its own
constraint. Data Kiosk `createQuery` is throttled **per selling account** (per `app × account ×
operation`) with a very low sustained rate and a small burst. Submitting several queries for the
same account back-to-back drains the token bucket: the first succeeds and the rest return
**HTTP 429 `{"code":"QuotaExceeded"}`**.

Observed 2026-07-02: the monthly cron submits one query per single-marketplace seller (fine), but
for **SH** it submits several — US, CA, MX, BR, and an "all" aggregate — in a tight loop; the first
went through and CA/MX/BR/all all 429'd, while KK/CO/73J/OH succeeded. A 429 here is throttling,
**not** a bad `accountId` (that would be a `4xx InvalidInput`).

Requirement on the `amazon_sh` submitter (`cron_submit_monthly_economics` → `create_economics_query`
→ `submit` → `lwa_utils.create_data_kiosk_query`): retry 429 with exponential backoff, honoring the
`x-amzn-RateLimit-Limit` (restore rate, req/s) and `Retry-After` response headers rather than a
hardcoded delay; pace submissions **per selling account**; keep one submission's terminal failure
from aborting the seller's other marketplaces or other sellers. This is submission-side only and
does not touch the notification contract (`queryId`, `accountId`) the relay depends on.

## AWS side responsibilities (`myAWS`)

Implemented in `platforms/amazon/` (sibling of the feed relay):

- **Queue / DLQ**: `${Environment}-sp-api-datakiosk-ready` + `-dlq`, 3× redrive, 90s
  visibility, SQS-managed SSE. Defined in `platforms/amazon/template.yaml`.
- **Relay Lambda**: `${Environment}-DataKioskRelay`
  (`handlers.datakiosk_relay.lambda_handler`, BatchSize 1, 60s timeout). Reads
  `record["body"]` as a string, signs `record["body"].encode("utf-8")` (the exact wire
  bytes — **never** a re-serialized form), POSTs to the seller's URL with
  `X-Hub-Signature-256: sha256=<hex>`. Builds its `accountId → alias` map at cold start
  from the secrets named in `WEBHOOK_SELLER_ALIASES` (default `73J,CO,KK,LLG,OH,SH`).
- **Per-seller config**: secret at
  `sp-api/sincerely-services/{alias}/webhooks/amazon-datakiosk`, shape
  `{secret, url, account_id}`. Note: Data Kiosk matches on `accountId` (merchant customer
  id), which may differ from the feed notification's `sellerId` — capture the value
  actually present on this notification for the seller when populating the map.
- **Error policy**: identical to the feed relay — 2xx success; 5xx / network /
  non-401-410 4xx raise (SQS redrives to DLQ at attempt 3); 401 / 410 log + emit
  CloudWatch metric `OdooWebhookAuthFailure` (namespace `SincerelyHers/AmazonRelay`) and
  **do not raise** (avoids filling the DLQ during a secret-rotation window; the Odoo
  polling cron is the backstop).
- **Idempotency**: none on the relay side — Odoo de-dups on `queryId` via
  `_external_id_amazon_datakiosk_status`.

### Operator onboarding steps

Three independent pieces have to land for traffic to flow: the **platform stack**
(queue + Lambda + IAM, via `sam deploy`), the **per-seller secrets** (Secrets Manager),
and the **SP-API subscriptions** (one destination + one subscription per seller).

#### Dev path

1. **Deploy the platform stack** — creates the queue, DLQ, IAM role, and Lambda:
   ```
   make deploy-amazon-dev
   ```
2. **Stage each seller's webhook secret** as a local file in the gitignored `secrets/`
   directory, named to match the Odoo URL path component:
   `secrets/amazon-datakiosk-{alias-lower}.json` (e.g. `secrets/amazon-datakiosk-llg.json`),
   shape `{"secret": "...", "url": "https://<odoo-host>/webhook/amazon-datakiosk-llg",
   "account_id": "amzn1.merchant.o...."}`.
3. **Create the AWS secrets** (one per seller; dev account permits direct CLI writes):
   ```
   aws secretsmanager create-secret \
     --profile sincerelyhers-dev --region us-east-2 \
     --name sp-api/sincerely-services/LLG/webhooks/amazon-datakiosk \
     --secret-string file://secrets/amazon-datakiosk-llg.json
   ```
4. **Subscribe SP-API to the destination** (`create-destination` once — an SQS
   destination; Data Kiosk notifications do **not** require EventBridge — then
   `create-subscription` per seller):
   ```
   uv run python scripts/sp_api_notifications.py create-destination \
       dev-sp-api-datakiosk-ready arn:aws:sqs:us-east-2:<DEV-ACCOUNT-ID>:dev-sp-api-datakiosk-ready
   uv run python scripts/sp_api_notifications.py create-subscription LLG <destination-id> DATA_KIOSK_QUERY_PROCESSING_FINISHED
   # ... repeat per seller: 73J, CO, KK, OH, SH
   ```
5. **Delete the local `secrets/*.json` files** after the AWS secrets are created.

#### Prod path

Same three pieces, but the `ProtectProductionSecrets` SCP blocks Step 3's direct CLI
write — only `DeploymentRole` can write `sp-api/*` secrets in prod. Declare each secret
as an `AWS::SecretsManager::Secret` resource in `platforms/amazon/template.yaml` with
`SecretString` interpolating a `NoEcho: true` SAM parameter, and pass values via
`sam deploy --role-arn arn:aws:iam::<PROD>:role/DeploymentRole --parameter-overrides
"LLGDataKioskJson=$(cat secrets/amazon-datakiosk-llg.json)" ...`. Steps 1 + 4 are
unchanged (use `make deploy-amazon-prod` and the prod queue ARN / `sincerelyhers-prod`
SSO profile).

## Testing

Against the Odoo dev endpoint (set a temporary secret on the endpoint first). Use a
`processingStatus` without a document (e.g. `CANCELLED`) to exercise the match + status
path without making a live Amazon `getDocument` call:

```bash
BODY='{"notificationType":"DATA_KIOSK_QUERY_PROCESSING_FINISHED","payload":{"queryId":"<id>","processingStatus":"CANCELLED"}}'
SIG=$(python3 -c "import hmac,hashlib,sys;print(hmac.new(b'<secret>', sys.argv[1].encode(), hashlib.sha256).hexdigest())" "$BODY")
curl -s -X POST http://localhost:58069/webhook/amazon-datakiosk-llg \
  -H 'Content-Type: application/json' -H "X-Hub-Signature-256: sha256=$SIG" --data "$BODY"
```

To exercise the full download + parse path, submit a real query first (Reports → Request
Economics (Data Kiosk)), then replay a `DONE` notification carrying that query's
`queryId` and real `dataDocumentId`.
