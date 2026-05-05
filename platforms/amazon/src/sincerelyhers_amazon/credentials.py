"""Retrieve SP-API credentials from AWS Secrets Manager.

Two secrets are read and merged:

* ``{SECRETS_PREFIX}/app/credentials`` — app-level, shared across all sellers
  for the same SPP app. Stores ``client_id`` and ``client_secret`` (the LWA
  credential pair). Rotated via the SP-API Application Management API.
* ``{SECRETS_PREFIX}/{seller_alias}/credentials`` — per-seller. Stores
  ``refresh_token`` only. Issued at SPP self-authorization time and not
  formally rotatable via API; replaced if the seller re-authorizes.

The split aligns the rotation write surface with reality (``client_secret``
is app-scoped) and lets the rotation processor write a single secret per
event instead of fanning out to one per seller. See
``docs/design/credential-rotation.md`` D2.
"""

import json
import os

import boto3


def get_sp_api_credentials(seller_alias: str) -> dict:
    """Read SP-API credentials for *seller_alias* from Secrets Manager.

    Returns a dict with keys ``lwa_app_id``, ``lwa_client_secret``, and
    ``refresh_token`` — the shape expected by ``python-amazon-sp-api``'s
    ``credentials`` parameter.
    """
    prefix = os.environ.get("SECRETS_PREFIX", "sp-api/sincerely-services")
    client = boto3.client("secretsmanager")

    app_secret = json.loads(
        client.get_secret_value(SecretId=f"{prefix}/app/credentials")["SecretString"]
    )
    seller_secret = json.loads(
        client.get_secret_value(SecretId=f"{prefix}/{seller_alias}/credentials")[
            "SecretString"
        ]
    )

    return {
        "lwa_app_id": app_secret["client_id"],
        "lwa_client_secret": app_secret["client_secret"],
        "refresh_token": seller_secret["refresh_token"],
    }
