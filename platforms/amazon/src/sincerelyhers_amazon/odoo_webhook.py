"""Outbound Odoo webhook helpers — load endpoint config, sign, POST.

Each Odoo webhook endpoint has its own per-seller secret at
``{SECRETS_PREFIX}/{seller_alias}/webhooks/{webhook_code}`` shaped
``{secret, url, seller_id}``:

* ``secret``  — HMAC-SHA256 key, source of truth is the Odoo
  ``webhook.endpoint.secret`` field.
* ``url``     — full https URL the relay POSTs to.
* ``seller_id`` — Amazon merchant ID; used at the relay Lambda to
  dispatch by ``payload.*.sellerId`` to the matching alias.

The signature is computed over the **exact bytes** of the SQS message
body that came in — never over a re-serialized form. Re-serializing
changes whitespace and key ordering and silently breaks the HMAC. See
``handlers.feed_relay`` for the call site that enforces this.
"""

import hashlib
import hmac
import json
import logging
import os
from functools import lru_cache

import boto3
import httpx

logger = logging.getLogger(__name__)


def load_endpoint(seller_alias: str, webhook_code: str) -> dict:
    """Read ``{secret, url, seller_id}`` for *seller_alias* / *webhook_code*.

    Cached per (alias, code) for the lifetime of the Lambda container;
    Secrets Manager reads are not free, and webhook config is steady-state.
    """
    return _load_endpoint_cached(seller_alias, webhook_code)


@lru_cache(maxsize=32)
def _load_endpoint_cached(seller_alias: str, webhook_code: str) -> dict:
    prefix = os.environ.get("SECRETS_PREFIX", "sp-api/sincerely-services")
    secret_id = f"{prefix}/{seller_alias}/webhooks/{webhook_code}"
    raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)[
        "SecretString"
    ]
    return json.loads(raw)


def build_seller_id_to_alias_map(
    seller_aliases: list[str], webhook_code: str
) -> dict[str, str]:
    """Return ``{amazon_seller_id: internal_alias}`` for the given aliases.

    Called once at Lambda cold start. Reads each alias's webhook secret to
    pull out the ``seller_id`` field — that's the canonical place to keep
    the alias ↔ Amazon-merchant-ID mapping (avoids embedding seller IDs
    in code or in the SAM template).
    """
    mapping: dict[str, str] = {}
    for alias in seller_aliases:
        endpoint = load_endpoint(alias, webhook_code)
        mapping[endpoint["seller_id"]] = alias
    return mapping


def sign(body: bytes, secret: str) -> str:
    """Return ``sha256=<hex>`` for the ``X-Hub-Signature-256`` header.

    *body* must be the raw bytes that will be sent on the wire. Pass the
    result of ``record["body"].encode("utf-8")`` directly — do not pass
    a re-serialized form, or the receiver's HMAC check will fail.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def post(
    url: str, body: bytes, signature: str, timeout: float = 10.0
) -> httpx.Response:
    """POST *body* to *url* with the HMAC signature header.

    Returns the response so the caller can branch on status. Network-level
    errors propagate (``httpx.HTTPError`` subclasses) and the relay
    surfaces those to SQS for redelivery.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature,
    }
    return httpx.post(url, content=body, headers=headers, timeout=timeout)
