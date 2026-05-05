"""SP-API Notifications bootstrap — one-shot admin operations.

Usage (set AWS_PROFILE first, e.g. `export AWS_PROFILE=sincerelyhers-dev`):

    uv run python scripts/sp_api_notifications.py list-destinations
    uv run python scripts/sp_api_notifications.py create-destination \\
        dev-sp-api-report-ready arn:aws:sqs:us-east-2:<DEV-ACCOUNT-ID>:dev-sp-api-report-ready
    uv run python scripts/sp_api_notifications.py show-subscription SH
    uv run python scripts/sp_api_notifications.py create-subscription SH <destination-id>

Goes directly at the SP-API Notifications REST endpoints via httpx rather than
through python-amazon-sp-api: that library's create_subscription silently omits
the required payloadVersion field, and its 404-handling path mistranslates the
response into `SellingApiForbiddenException(Unauthorized)`, which sent us down
an hours-long auth-debugging rabbit hole. Credentials still come from the same
Secrets Manager secret the Lambdas use (`sp-api/sincerely-services/<alias>/credentials`).
"""

import argparse
import json
import os
import sys

import boto3
import httpx


LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SP_API_ENDPOINT = "https://sellingpartnerapi-na.amazon.com"
GRANTLESS_SCOPE = "sellingpartnerapi::notifications"
PAYLOAD_VERSION = "1.0"


def _load_creds(seller_alias: str) -> dict:
    """Read app-level + per-seller secrets and merge to one creds dict.

    See ``platforms/amazon/src/sincerelyhers_amazon/credentials.py`` for the
    matching read pattern used by the Lambda runtime. Keeps the script and
    the runtime aligned on secret layout.
    """
    prefix = os.environ.get("SECRETS_PREFIX", "sp-api/sincerely-services")
    sm = boto3.client("secretsmanager")
    app = json.loads(
        sm.get_secret_value(SecretId=f"{prefix}/app/credentials")["SecretString"]
    )
    seller = json.loads(
        sm.get_secret_value(SecretId=f"{prefix}/{seller_alias}/credentials")[
            "SecretString"
        ]
    )
    return {
        "client_id": app["client_id"],
        "client_secret": app["client_secret"],
        "refresh_token": seller["refresh_token"],
    }


def _seller_access_token(seller_alias: str) -> str:
    creds = _load_creds(seller_alias)
    response = httpx.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _grantless_access_token(seller_alias: str) -> str:
    """Grantless tokens use client_credentials + a scope; seller refresh_token is unused here."""
    creds = _load_creds(seller_alias)
    response = httpx.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "scope": GRANTLESS_SCOPE,
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _sp_api(
    method: str, path: str, access_token: str, body: dict = None
) -> httpx.Response:
    headers = {"x-amz-access-token": access_token, "accept": "application/json"}
    if body is not None:
        headers["content-type"] = "application/json"
    return httpx.request(
        method, f"{SP_API_ENDPOINT}{path}", headers=headers, json=body, timeout=30.0
    )


def _emit(response: httpx.Response) -> None:
    try:
        print(json.dumps(response.json(), indent=2))
    except ValueError:
        print(response.text)
    if response.status_code >= 400:
        sys.exit(1)


def list_destinations(args) -> None:
    token = _grantless_access_token(args.seller_alias)
    _emit(_sp_api("GET", "/notifications/v1/destinations", token))


def create_destination(args) -> None:
    token = _grantless_access_token(args.seller_alias)
    body = {
        "name": args.name,
        "resourceSpecification": {"sqs": {"arn": args.queue_arn}},
    }
    _emit(_sp_api("POST", "/notifications/v1/destinations", token, body))


def show_subscription(args) -> None:
    token = _seller_access_token(args.seller_alias)
    _emit(
        _sp_api(
            "GET", f"/notifications/v1/subscriptions/{args.notification_type}", token
        )
    )


def create_subscription(args) -> None:
    token = _seller_access_token(args.seller_alias)
    body = {"payloadVersion": PAYLOAD_VERSION, "destinationId": args.destination_id}
    _emit(
        _sp_api(
            "POST",
            f"/notifications/v1/subscriptions/{args.notification_type}",
            token,
            body,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="SP-API Notifications bootstrap")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "list-destinations", help="List destinations registered for this SPP app."
    )
    p.add_argument(
        "--seller-alias",
        default="SH",
        help="Seller whose secret supplies app creds (client_id/client_secret). Default: SH.",
    )
    p.set_defaults(func=list_destinations)

    p = sub.add_parser(
        "create-destination", help="Create an SQS destination for the SPP app."
    )
    p.add_argument("name", help="Human-readable name, e.g. dev-sp-api-report-ready.")
    p.add_argument(
        "queue_arn",
        help="ARN of the target SQS queue (must already allow SP-API SendMessage).",
    )
    p.add_argument("--seller-alias", default="SH")
    p.set_defaults(func=create_destination)

    p = sub.add_parser(
        "show-subscription",
        help="Show a seller's subscription for one notification type.",
    )
    p.add_argument("seller_alias")
    p.add_argument("notification_type", nargs="?", default="REPORT_PROCESSING_FINISHED")
    p.set_defaults(func=show_subscription)

    p = sub.add_parser(
        "create-subscription",
        help="Subscribe seller to a notification type via destination.",
    )
    p.add_argument("seller_alias")
    p.add_argument("destination_id")
    p.add_argument("notification_type", nargs="?", default="REPORT_PROCESSING_FINISHED")
    p.set_defaults(func=create_subscription)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
