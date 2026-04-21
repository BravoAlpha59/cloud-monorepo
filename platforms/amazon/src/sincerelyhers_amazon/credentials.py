"""Retrieve SP-API credentials from AWS Secrets Manager."""

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
    secret_id = f"{prefix}/{seller_alias}/credentials"

    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_id)
    secret = json.loads(response["SecretString"])

    return {
        "lwa_app_id": secret["client_id"],
        "lwa_client_secret": secret["client_secret"],
        "refresh_token": secret["refresh_token"],
    }
