"""Lambda handler — relay DATA_KIOSK_QUERY_PROCESSING_FINISHED to Odoo.

The Data Kiosk sibling of ``handlers.feed_relay``. Reads each SQS record
(raw SP-API notification body), looks up the seller alias from the
``accountId`` field, signs the **exact body bytes** with that seller's HMAC
secret, and POSTs to the matching Odoo webhook URL.

Two shape differences from the feed relay:

* The Data Kiosk query fields sit **directly under** ``payload`` — the feed
  notification nests them under ``payload.feedProcessingFinishedNotification``.
* Dispatch is on ``accountId`` (the merchant customer id), not ``sellerId``;
  the two can differ for the same seller, so the webhook secret carries
  ``account_id`` rather than ``seller_id``.

Error policy (identical to the feed relay):

* 2xx          — success; SQS deletes the message.
* 5xx, network — raise; SQS redelivers (DLQ at 3 attempts).
* 401, 410     — log, emit ``OdooWebhookAuthFailure`` metric, **do not raise**.
                 Auth failure during a secret-rotation window would otherwise
                 silently fill the DLQ; the metric gives operator visibility
                 and Odoo's polling cron is the backstop for any dropped
                 query-status update.
* other 4xx    — raise; treat as transient (e.g. handler bug worth retrying
                 across an Odoo redeploy).

Idempotency: Odoo de-dups on ``queryId`` (see the handoff doc); no relay-side
de-dup layer is needed.
"""

import json
import logging
import os

import boto3

from sincerelyhers_amazon import odoo_webhook

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

WEBHOOK_CODE = "amazon-datakiosk"
METRIC_NAMESPACE = "SincerelyHers/AmazonRelay"

_ACCOUNT_ID_TO_ALIAS: dict[str, str] | None = None


def _account_id_to_alias() -> dict[str, str]:
    global _ACCOUNT_ID_TO_ALIAS
    if _ACCOUNT_ID_TO_ALIAS is None:
        aliases = [
            a.strip()
            for a in os.environ["WEBHOOK_SELLER_ALIASES"].split(",")
            if a.strip()
        ]
        _ACCOUNT_ID_TO_ALIAS = odoo_webhook.build_dispatch_map(
            aliases, WEBHOOK_CODE, id_field="account_id"
        )
    return _ACCOUNT_ID_TO_ALIAS


def lambda_handler(event: dict, context: object) -> None:
    for record in event["Records"]:
        raw_body: str = record["body"]
        body = json.loads(raw_body)

        notification_type = body.get("notificationType")
        if notification_type != "DATA_KIOSK_QUERY_PROCESSING_FINISHED":
            logger.warning(
                "Skipping non-datakiosk notification on datakiosk-relay queue: %s",
                notification_type,
            )
            continue

        payload = body["payload"]
        account_id: str = payload["accountId"]
        query_id: str = payload.get("queryId", "<unknown>")

        alias = _account_id_to_alias().get(account_id)
        if alias is None:
            # accountId we don't have a webhook secret for — traffic that
            # lands here because the destination is app-scoped and the
            # notification fires for all queries on the account.
            logger.warning(
                "No alias mapped for accountId %s (queryId %s); skipping",
                account_id,
                query_id,
            )
            continue

        endpoint = odoo_webhook.load_endpoint(alias, WEBHOOK_CODE)
        body_bytes = raw_body.encode("utf-8")
        signature = odoo_webhook.sign(body_bytes, endpoint["secret"])
        response = odoo_webhook.post(endpoint["url"], body_bytes, signature)

        status = response.status_code
        if 200 <= status < 300:
            logger.info(
                "Relayed queryId %s for %s -> %s (HTTP %d)",
                query_id,
                alias,
                endpoint["url"],
                status,
            )
            continue

        if status in (401, 410):
            logger.error(
                "Odoo rejected queryId %s for %s with HTTP %d; dropping (metric emitted)",
                query_id,
                alias,
                status,
            )
            _emit_auth_failure_metric(alias, status)
            continue

        # Everything else (other 4xx, 5xx) — raise so SQS redrives.
        raise RuntimeError(
            f"Odoo POST failed for queryId={query_id} alias={alias} status={status} "
            f"body={response.text[:200]!r}"
        )


def _emit_auth_failure_metric(alias: str, status: int) -> None:
    boto3.client("cloudwatch").put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=[
            {
                "MetricName": "OdooWebhookAuthFailure",
                "Dimensions": [
                    {"Name": "SellerAlias", "Value": alias},
                    {"Name": "HttpStatus", "Value": str(status)},
                    {"Name": "WebhookCode", "Value": WEBHOOK_CODE},
                ],
                "Value": 1,
                "Unit": "Count",
            }
        ],
    )
