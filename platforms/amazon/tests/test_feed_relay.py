"""Tests for the FeedRelay Lambda handler."""

import hashlib
import hmac
import json

import boto3
import httpx
import pytest
from moto import mock_aws


REGION = "us-east-2"
SECRETS_PREFIX = "sp-api/sincerely-services"
WEBHOOK_CODE = "amazon-feed"

# Three sellers, each with a distinct sellerId, secret, and URL.
WEBHOOK_PAYLOADS = {
    "KK": {
        "secret": "kk-hmac-secret",
        "url": "https://odoo.example.com/webhook/amazon-feed-kk",
        "seller_id": "A1KKSELLER",
    },
    "LLG": {
        "secret": "llg-hmac-secret",
        "url": "https://odoo.example.com/webhook/amazon-feed-llg",
        "seller_id": "A2LLGSELLER",
    },
    "CO": {
        "secret": "co-hmac-secret",
        "url": "https://odoo.example.com/webhook/amazon-feed-co",
        "seller_id": "A3COSELLER",
    },
}


def _notification(seller_id: str, **overrides) -> dict:
    base = {
        "sellerId": seller_id,
        "accountId": f"amzn1.merchant.o.{seller_id}",
        "feedId": "FEED-123",
        "feedType": "JSON_LISTINGS_FEED",
        "processingStatus": "DONE",
        "resultFeedDocumentId": "amzn1.tortuga.3.test",
    }
    base.update(overrides)
    return base


def _sqs_event(
    notification: dict, *, notification_type: str = "FEED_PROCESSING_FINISHED"
) -> dict:
    body = {
        "notificationVersion": "2020-09-04",
        "notificationType": notification_type,
        "payloadVersion": "2020-09-04",
        "eventTime": "2026-06-03T08:00:00.000Z",
        "payload": {"feedProcessingFinishedNotification": notification},
    }
    return {
        "Records": [
            {
                "messageId": "test-message-id",
                "body": json.dumps(body),
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:us-east-2:123456789012:dev-sp-api-feed-ready",
                "awsRegion": REGION,
            }
        ]
    }


@pytest.fixture()
def aws_webhooks(monkeypatch):
    """moto-mocked AWS with the three webhook secrets seeded, env vars set."""
    monkeypatch.setenv("WEBHOOK_SELLER_ALIASES", "KK,LLG,CO")
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name=REGION)
        for alias, payload in WEBHOOK_PAYLOADS.items():
            sm.create_secret(
                Name=f"{SECRETS_PREFIX}/{alias}/webhooks/{WEBHOOK_CODE}",
                SecretString=json.dumps(payload),
            )
        yield


@pytest.fixture(autouse=True)
def _reset_caches():
    """Clear lru_cache + the module-level seller-id map between tests so
    env-var changes and secret seeding take effect per test."""
    from handlers import feed_relay
    from sincerelyhers_amazon import odoo_webhook

    odoo_webhook._load_endpoint_cached.cache_clear()
    feed_relay._SELLER_ID_TO_ALIAS = None
    yield
    odoo_webhook._load_endpoint_cached.cache_clear()
    feed_relay._SELLER_ID_TO_ALIAS = None


def _ok_response() -> httpx.Response:
    return httpx.Response(200, request=httpx.Request("POST", "https://x"))


def _response(status: int, text: str = "") -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("POST", "https://x"))


def test_happy_path_posts_to_seller_url(aws_webhooks, mocker):
    from handlers.feed_relay import lambda_handler

    post = mocker.patch(
        "handlers.feed_relay.odoo_webhook.post", return_value=_ok_response()
    )

    lambda_handler(_sqs_event(_notification("A1KKSELLER")), None)

    post.assert_called_once()
    url_arg = post.call_args.args[0]
    assert url_arg == WEBHOOK_PAYLOADS["KK"]["url"]


def test_signature_is_over_raw_sqs_body_bytes(aws_webhooks, mocker):
    """The HMAC must be computed over the **exact bytes** of record["body"],
    not over a re-serialized form. Re-serialization changes whitespace and
    key ordering and silently breaks the receiver's HMAC check."""
    from handlers.feed_relay import lambda_handler

    # Craft a body with non-canonical whitespace so re-serialization would
    # produce a different byte sequence than the original.
    raw_body = (
        '{ "notificationType":"FEED_PROCESSING_FINISHED",'
        '  "payload": {"feedProcessingFinishedNotification":'
        '{"sellerId":"A2LLGSELLER","feedId":"FEED-XYZ",'
        '"feedType":"JSON_LISTINGS_FEED","processingStatus":"CANCELLED"}}}'
    )
    event = {
        "Records": [{"messageId": "m1", "body": raw_body, "eventSource": "aws:sqs"}]
    }
    expected_sig = (
        "sha256="
        + hmac.new(
            WEBHOOK_PAYLOADS["LLG"]["secret"].encode("utf-8"),
            raw_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )

    post = mocker.patch(
        "handlers.feed_relay.odoo_webhook.post", return_value=_ok_response()
    )

    lambda_handler(event, None)

    url_arg, body_arg, sig_arg = post.call_args.args[:3]
    assert url_arg == WEBHOOK_PAYLOADS["LLG"]["url"]
    assert body_arg == raw_body.encode("utf-8")
    assert sig_arg == expected_sig


def test_unknown_seller_id_is_dropped(aws_webhooks, mocker):
    from handlers.feed_relay import lambda_handler

    post = mocker.patch("handlers.feed_relay.odoo_webhook.post")

    lambda_handler(_sqs_event(_notification("A9UNKNOWN")), None)

    post.assert_not_called()


def test_wrong_notification_type_is_dropped(aws_webhooks, mocker):
    from handlers.feed_relay import lambda_handler

    post = mocker.patch("handlers.feed_relay.odoo_webhook.post")

    lambda_handler(
        _sqs_event(
            _notification("A1KKSELLER"),
            notification_type="REPORT_PROCESSING_FINISHED",
        ),
        None,
    )

    post.assert_not_called()


def test_5xx_raises_for_sqs_redrive(aws_webhooks, mocker):
    from handlers.feed_relay import lambda_handler

    mocker.patch(
        "handlers.feed_relay.odoo_webhook.post",
        return_value=_response(500, "internal error"),
    )

    with pytest.raises(RuntimeError, match="status=500"):
        lambda_handler(_sqs_event(_notification("A1KKSELLER")), None)


def test_401_drops_and_emits_metric(aws_webhooks, mocker):
    from handlers.feed_relay import lambda_handler

    mocker.patch("handlers.feed_relay.odoo_webhook.post", return_value=_response(401))
    metric = mocker.patch("handlers.feed_relay._emit_auth_failure_metric")

    lambda_handler(_sqs_event(_notification("A1KKSELLER")), None)  # must not raise

    metric.assert_called_once_with("KK", 401)


def test_410_drops_and_emits_metric(aws_webhooks, mocker):
    from handlers.feed_relay import lambda_handler

    mocker.patch("handlers.feed_relay.odoo_webhook.post", return_value=_response(410))
    metric = mocker.patch("handlers.feed_relay._emit_auth_failure_metric")

    lambda_handler(_sqs_event(_notification("A3COSELLER")), None)  # must not raise

    metric.assert_called_once_with("CO", 410)


def test_422_raises(aws_webhooks, mocker):
    """Non-401/410 4xx is treated as transient — let SQS redrive."""
    from handlers.feed_relay import lambda_handler

    mocker.patch("handlers.feed_relay.odoo_webhook.post", return_value=_response(422))

    with pytest.raises(RuntimeError, match="status=422"):
        lambda_handler(_sqs_event(_notification("A1KKSELLER")), None)


def test_seller_id_map_built_from_all_aliases(aws_webhooks):
    """build_dispatch_map should resolve every configured alias."""
    from sincerelyhers_amazon import odoo_webhook

    mapping = odoo_webhook.build_dispatch_map(
        ["KK", "LLG", "CO"], WEBHOOK_CODE, id_field="seller_id"
    )

    assert mapping == {
        "A1KKSELLER": "KK",
        "A2LLGSELLER": "LLG",
        "A3COSELLER": "CO",
    }


def test_sign_returns_sha256_prefixed_hex():
    from sincerelyhers_amazon import odoo_webhook

    sig = odoo_webhook.sign(b"hello", "topsecret")
    expected = hmac.new(b"topsecret", b"hello", hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"
