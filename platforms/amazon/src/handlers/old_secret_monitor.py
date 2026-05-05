"""Lambda handler — daily check for old-secret expiry within threshold.

D7: scans the rotation-events table for completed rotations whose
``old_secret_expires_at`` is within EXPIRY_ALERT_HOURS of now; emits an SES
alert per row. Catches integrations that haven't yet picked up the new
secret before the 7-day cliff.

Single SES alert per fire summarizes any rows in the alert window — even
if the cron runs multiple times a day or the alert window shifts between
runs, the operator sees one consolidated message rather than a per-row
spam.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context: object) -> None:
    table_name = os.environ["ROTATION_EVENTS_TABLE"]
    app = os.environ["APP_SHORT_NAME"]
    threshold_hours = float(os.environ.get("EXPIRY_ALERT_HOURS", "24"))

    now = datetime.now(timezone.utc)
    deadline = now + timedelta(hours=threshold_hours)

    # Query the by-app-and-time GSI for this app's events; last 30 days
    # keeps the scan bounded (a real rotation event rolls every 6+ months).
    table = boto3.resource("dynamodb").Table(table_name)
    response = table.query(
        IndexName="by-app-and-time",
        KeyConditionExpression=Key("app_short_name").eq(app)
        & Key("created_at").gte((now - timedelta(days=30)).isoformat()),
    )

    at_risk = []
    for item in response.get("Items", []):
        if item.get("event_type") != "ROTATION_COMPLETED":
            continue
        expires_raw = item.get("old_secret_expires_at")
        if not expires_raw or expires_raw == "unknown":
            continue
        try:
            expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "Could not parse old_secret_expires_at=%r on event %s",
                expires_raw,
                item["event_id"],
            )
            continue
        if now < expires_at <= deadline:
            at_risk.append((item, expires_at))

    if not at_risk:
        logger.info(
            "OldSecretMonitor: no rows within %.1fh expiry threshold for app=%s",
            threshold_hours,
            app,
        )
        return

    body_lines = [
        f"App: {app}",
        f"Window: {now.isoformat()} → {deadline.isoformat()}",
        "",
        f"{len(at_risk)} rotation event(s) with old_secret_expires_at in this window:",
        "",
    ]
    for item, expires_at in sorted(at_risk, key=lambda t: t[1]):
        hours_left = (expires_at - now).total_seconds() / 3600
        body_lines.append(
            f"  - event_id={item['event_id']}  client_id={item.get('client_id')}  "
            f"expires={expires_at.isoformat()}  ({hours_left:.1f}h remaining)"
        )
    body_lines.append("")
    body_lines.append(
        "If any consumer of this app's client_secret is still using the old\n"
        "value, it will start failing at the expiry timestamp above. Verify\n"
        "downstream secret stores have been updated."
    )

    # Late import to keep boto3.client() out of cold-start when there's
    # nothing to alert on.
    from sincerelyhers_amazon import rotation

    rotation.send_alert(
        subject=f"[{app}] {len(at_risk)} old-secret(s) expire within {threshold_hours:.0f}h",
        body="\n".join(body_lines),
    )
