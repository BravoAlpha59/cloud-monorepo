"""Lambda handler — relay FEED_PROCESSING_FINISHED notifications to Odoo.

Reads each SQS record (raw SP-API notification body), looks up the seller
alias from the ``sellerId`` field, signs the **exact body bytes** with that
seller's HMAC secret, and POSTs to the matching Odoo webhook URL.

Error policy:

* 2xx          — success; SQS deletes the message.
* 5xx, network — raise; SQS redelivers (DLQ at 3 attempts).
* 401, 410     — log, emit ``OdooWebhookAuthFailure`` metric, **do not raise**.
                 Auth failure during a secret-rotation window would otherwise
                 silently fill the DLQ; the metric gives operator visibility
                 and Odoo's polling cron is the backstop for any dropped
                 feed-status update.
* other 4xx    — raise; treat as transient (e.g. handler bug worth retrying
                 across an Odoo redeploy).

Idempotency: Odoo de-dups on ``feedId`` (see the handoff doc); no relay-side
de-dup layer is needed.
"""

import json
import logging
import os

import boto3

from sincerelyhers_amazon import odoo_webhook

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

WEBHOOK_CODE = "amazon-feed"
METRIC_NAMESPACE = "SincerelyHers/AmazonRelay"

_SELLER_ID_TO_ALIAS: dict[str, str] | None = None


def _seller_id_to_alias() -> dict[str, str]:
    global _SELLER_ID_TO_ALIAS
    if _SELLER_ID_TO_ALIAS is None:
        aliases = [
            a.strip()
            for a in os.environ["WEBHOOK_SELLER_ALIASES"].split(",")
            if a.strip()
        ]
        _SELLER_ID_TO_ALIAS = odoo_webhook.build_seller_id_to_alias_map(
            aliases, WEBHOOK_CODE
        )
    return _SELLER_ID_TO_ALIAS


def lambda_handler(event: dict, context: object) -> None:
    for record in event["Records"]:
        raw_body: str = record["body"]
        body = json.loads(raw_body)

        notification_type = body.get("notificationType")
        if notification_type != "FEED_PROCESSING_FINISHED":
            logger.warning(
                "Skipping non-feed notification on feed-relay queue: %s",
                notification_type,
            )
            continue

        inner = body["payload"]["feedProcessingFinishedNotification"]
        seller_id: str = inner["sellerId"]
        feed_id: str = inner.get("feedId", "<unknown>")

        alias = _seller_id_to_alias().get(seller_id)
        if alias is None:
            # sellerId we don't have a webhook secret for — e.g. SH/73J/OH
            # traffic that lands here because the destination is app-scoped.
            logger.warning(
                "No alias mapped for sellerId %s (feedId %s); skipping",
                seller_id,
                feed_id,
            )
            continue

        endpoint = odoo_webhook.load_endpoint(alias, WEBHOOK_CODE)
        body_bytes = raw_body.encode("utf-8")
        signature = odoo_webhook.sign(body_bytes, endpoint["secret"])
        response = odoo_webhook.post(endpoint["url"], body_bytes, signature)

        status = response.status_code
        if 200 <= status < 300:
            logger.info(
                "Relayed feedId %s for %s -> %s (HTTP %d)",
                feed_id,
                alias,
                endpoint["url"],
                status,
            )
            continue

        if status in (401, 410):
            logger.error(
                "Odoo rejected feedId %s for %s with HTTP %d; dropping (metric emitted)",
                feed_id,
                alias,
                status,
            )
            _emit_auth_failure_metric(alias, status)
            continue

        # Everything else (other 4xx, 5xx) — raise so SQS redrives.
        raise RuntimeError(
            f"Odoo POST failed for feedId={feed_id} alias={alias} status={status} "
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
