# Integration contract: Amazon `FEED_PROCESSING_FINISHED` → `webhook_sh`

**Status:** active · **Owners:** Odoo (`amazon_sh`) + AWS relay (`myAWS` distro) ·
**Canonical copy:** this file; mirror into the `myAWS` repo.

This is the shared source of truth for the seam between two projects. Amazon delivers
`FEED_PROCESSING_FINISHED` notifications to an SQS queue in the AWS project; a relay
there forwards each one to this Odoo instance, which updates the matching feed-status
record. Each side is built and tested independently against this contract.

## The seam

```
_submit_fbm_listings_feed (KK, LLG, CO)
  → feedId stored on amazon.sh.spapi.feed.status (IN_PROGRESS)
  → Amazon finishes → FEED_PROCESSING_FINISHED → SQS         [AWS]
  → relay POSTs raw notification (HMAC-signed) →             [AWS → Odoo]
  POST https://<public-host>/webhook/<code>                  [contract]
  → _handle_amazon_feed_status: match feedId, set status,    [Odoo]
    fetch + attach result document
```

## Transport & auth

| Item | Value |
|---|---|
| Method / URL | `POST https://<public-host>/webhook/<code>` |
| Endpoints | **one per seller** — `amazon-feed-kk`, `amazon-feed-llg`, `amazon-feed-co` |
| Body | **raw** `FEED_PROCESSING_FINISHED` notification JSON (SQS message body, no envelope) |
| Content-Type | `application/json` |
| Auth | **HMAC-SHA256** over the raw body, header `X-Hub-Signature-256` (a `sha256=` prefix is accepted) |
| Secret | per-seller; **Odoo `webhook.endpoint.secret` is the source of truth**, mirrored into AWS (Secrets Manager) |
| De-dup | `payload.feedProcessingFinishedNotification.feedId` |

The three endpoints are seeded by `amazon_sh` (`data/amazon_sh_webhook_endpoint_data.xml`)
with `auth_type=hmac` and **no secret** — an HMAC endpoint with no secret rejects every
request (401), so each goes live only once its secret is set in the UI.

## Payload (raw SP-API notification)

Source of truth: [`FeedProcessingFinishedNotification.json`](https://github.com/amzn/selling-partner-api-models/blob/main/schemas/notifications/FeedProcessingFinishedNotification.json).

```json
{
  "notificationVersion": "2020-09-04",
  "notificationType": "FEED_PROCESSING_FINISHED",
  "payloadVersion": "2020-09-04",
  "eventTime": "2020-07-13T19:42:04.284Z",
  "payload": {
    "feedProcessingFinishedNotification": {
      "sellerId": "A3TH9S8BH6GOGM",
      "accountId": "amzn1.merchant.o.A3TH9S8BH6GOGM",
      "feedId": "53347018456",
      "feedType": "POST_PRODUCT_DATA",
      "processingStatus": "DONE",
      "resultFeedDocumentId": "amzn1.tortuga.3.edbcd0d8-...-URUTI57URI9BMZ"
    }
  },
  "notificationMetadata": {
    "applicationId": "amzn1.sellerapps.app.aacc...",
    "subscriptionId": "subscription-id-d0e9e693-...",
    "publishTime": "2020-07-13T19:42:04.284Z",
    "notificationId": "d0e9e693-c3ad-4373-979f-ed4ec98dd746"
  }
}
```

Fields the Odoo handler consumes (all under `payload.feedProcessingFinishedNotification`):

| Field | Use |
|---|---|
| `feedId` | match key against `amazon.sh.spapi.feed.status.feed_id`; also the de-dup id |
| `processingStatus` | enum `DONE` \| `CANCELLED` \| `FATAL` — written to `processing_status` |
| `resultFeedDocumentId` | when present, downloaded + attached via `_retrieve_listings_result_document` |
| `sellerId` | informational (the endpoint + its secret already identify the seller) |

## Odoo handler behavior (`amazon_sh`)

`webhook.endpoint` gains a handler key `amazon_feed_status`:

1. Parse the inner notification; ignore anything that isn't `FEED_PROCESSING_FINISHED`.
2. `search(amazon.sh.spapi.feed.status, feed_id == feedId)`. **No match → ignore** —
   this filters out other feed types/accounts (the notification fires for *all* feeds).
3. Set `processing_status = processingStatus`.
4. If `resultFeedDocumentId` present → `_retrieve_listings_result_document` (downloads,
   attaches, sets `RETRIEVED`). This is an in-request Amazon `getFeedDocument` call; a
   failure raises → HTTP 500 → the relay retries.
5. `_external_id_amazon_feed_status` returns `feedId` → redeliveries become `duplicate`
   no-ops.

The polling cron `update_listings_feed_status` stays enabled as a **backstop** for any
dropped notification (consider lowering its frequency once the push path is trusted).

## AWS side responsibilities (`myAWS`)

Implemented in `platforms/amazon/`:

- **Queue / DLQ**: `${Environment}-sp-api-feed-ready` + `-dlq`, 3× redrive, 90s visibility,
  SQS-managed SSE. Defined in `platforms/amazon/template.yaml`.
- **Relay Lambda**: `${Environment}-FeedRelay` (`handlers.feed_relay.lambda_handler`,
  BatchSize 1, 60s timeout). Reads `record["body"]` as a string, signs
  `record["body"].encode("utf-8")` (the exact wire bytes — **never** a re-serialized form),
  POSTs to the seller's URL with `X-Hub-Signature-256: sha256=<hex>`.
- **Per-seller config**: secret at
  `sp-api/sincerely-services/{alias}/webhooks/amazon-feed`, shape
  `{secret, url, seller_id}`. The Lambda builds a `sellerId → alias` map at cold start by
  reading the three secrets named in the `WEBHOOK_SELLER_ALIASES` env var (default
  `KK,LLG,CO`).
- **Error policy**:
  - 2xx → success.
  - 5xx / network / non-401-410 4xx → raise; SQS redrives to DLQ at attempt 3.
  - 401 / 410 → log + emit CloudWatch metric `OdooWebhookAuthFailure` in namespace
    `SincerelyHers/AmazonRelay` (dimensions: SellerAlias, HttpStatus, WebhookCode), **do
    not raise**. Avoids silently filling the DLQ during a secret-rotation window; the
    Odoo polling cron (`update_listings_feed_status`) is the backstop.
- **Idempotency**: none on the relay side — Odoo de-dups on `feedId` via
  `_external_id_amazon_feed_status`.

### Operator onboarding steps

Three independent pieces have to land for traffic to flow: the **platform stack**
(queue + Lambda + IAM, via `sam deploy`), the **per-seller secrets** (Secrets Manager),
and the **SP-API subscriptions** (one destination + one subscription per seller).
The stack and secrets can land in either order; subscriptions come last because they
require the queue ARN.

#### Dev path

1. **Deploy the platform stack** — creates the queue, DLQ, IAM role, and Lambda:
   ```
   make deploy-amazon-dev
   ```
2. **Stage each seller's webhook secret** as a local file in the gitignored `secrets/`
   directory, named to match the Odoo URL path component:
   `secrets/amazon-feed-{alias-lower}.json` (e.g. `secrets/amazon-feed-kk.json`),
   shape `{"secret": "...", "url": "https://<odoo-host>/webhook/amazon-feed-kk",
   "seller_id": "A1ABCDEFG"}`.
3. **Create the AWS secrets** (one per seller; dev account permits direct CLI writes):
   ```
   aws secretsmanager create-secret \
     --profile sincerelyhers-dev --region us-east-2 \
     --name sp-api/sincerely-services/KK/webhooks/amazon-feed \
     --secret-string file://secrets/amazon-feed-kk.json
   ```
4. **Subscribe SP-API to the destination** (`create-destination` once, then
   `create-subscription` per seller):
   ```
   uv run python scripts/sp_api_notifications.py create-destination \
       dev-sp-api-feed-ready arn:aws:sqs:us-east-2:<DEV-ACCOUNT-ID>:dev-sp-api-feed-ready
   uv run python scripts/sp_api_notifications.py create-subscription KK  <destination-id> FEED_PROCESSING_FINISHED
   uv run python scripts/sp_api_notifications.py create-subscription LLG <destination-id> FEED_PROCESSING_FINISHED
   uv run python scripts/sp_api_notifications.py create-subscription CO  <destination-id> FEED_PROCESSING_FINISHED
   ```
5. **Delete the local `secrets/*.json` files** after the AWS secrets are created.

#### Prod path

Same three pieces, but the `ProtectProductionSecrets` SCP blocks Step 3's direct CLI
write — only `DeploymentRole` can write `sp-api/*` secrets in prod. Workarounds:

- Steps 1 + 4 are unchanged (use `make deploy-amazon-prod` and the prod queue ARN /
  `sincerelyhers-prod` SSO profile).
- Step 3 in prod: declare each secret as an `AWS::SecretsManager::Secret` resource in
  `platforms/amazon/template.yaml` with `SecretString` interpolating a SAM parameter
  marked `NoEcho: true`. Pass values via
  `sam deploy --role-arn arn:aws:iam::<PROD>:role/DeploymentRole --parameter-overrides
  "KKWebhookJson=$(cat secrets/amazon-feed-kk.json)" ...`. The deploy itself satisfies
  the SCP (DeploymentRole is the principal); `NoEcho` keeps the values out of
  `describe-stacks` / `describe-stack-events`.

## Testing

Against the Odoo dev endpoint (set a temporary secret on the endpoint first):

```bash
BODY='{"notificationType":"FEED_PROCESSING_FINISHED","payload":{"feedProcessingFinishedNotification":{"feedId":"<id>","feedType":"JSON_LISTINGS_FEED","processingStatus":"CANCELLED"}}}'
SIG=$(python3 -c "import hmac,hashlib,sys;print(hmac.new(b'<secret>', sys.argv[1].encode(), hashlib.sha256).hexdigest())" "$BODY")
curl -s -X POST http://localhost:58069/webhook/amazon-feed-co \
  -H 'Content-Type: application/json' -H "X-Hub-Signature-256: sha256=$SIG" --data "$BODY"
```

Use a `processingStatus` without a `resultFeedDocumentId` (e.g. `CANCELLED`) to exercise
the match + status path without making a live Amazon `getFeedDocument` call.
