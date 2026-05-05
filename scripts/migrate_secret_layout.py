"""One-shot migration to the two-secret credential layout (D2 in
``docs/design/credential-rotation.md``).

Before:

    sp-api/sincerely-services/{alias}/credentials  →  {client_id, client_secret, refresh_token}   (×6 sellers)

After:

    sp-api/sincerely-services/app/credentials      →  {client_id, client_secret}                  (×1 new)
    sp-api/sincerely-services/{alias}/credentials  →  {refresh_token}                              (×6 trimmed)

The per-seller secret resources are NOT replaced — each is overwritten via
``put-secret-value`` to drop the duplicated app-level fields. The ARN, name,
IAM scope, and refresh_token value are preserved exactly. No re-authorization
required.

Usage (set AWS_PROFILE first):

    export AWS_PROFILE=sincerelyhers-dev
    uv run python scripts/migrate_secret_layout.py plan
    uv run python scripts/migrate_secret_layout.py apply

Idempotent: ``apply`` is safe to re-run. The app secret is created only if
absent; per-seller secrets are trimmed only if they still carry app-level
fields.
"""

import argparse
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError


SECRETS_PREFIX = os.environ.get("SECRETS_PREFIX", "sp-api/sincerely-services")
APP_SECRET_NAME = f"{SECRETS_PREFIX}/app/credentials"
APP_FIELDS = ("client_id", "client_secret")
SELLER_FIELD = "refresh_token"


def _list_seller_secrets(sm) -> list[str]:
    """Return per-seller secret names under SECRETS_PREFIX (excluding the app secret)."""
    paginator = sm.get_paginator("list_secrets")
    names = []
    for page in paginator.paginate(
        Filters=[{"Key": "name", "Values": [f"{SECRETS_PREFIX}/"]}]
    ):
        for entry in page["SecretList"]:
            name = entry["Name"]
            if name == APP_SECRET_NAME:
                continue
            if name.startswith(SECRETS_PREFIX) and name.endswith("/credentials"):
                names.append(name)
    return sorted(names)


def _read_secret(sm, name: str) -> dict:
    return json.loads(sm.get_secret_value(SecretId=name)["SecretString"])


def _ensure_app_secret(sm, source_seller_secret: dict, *, dry_run: bool) -> str:
    """Create app/credentials if missing. Source the values from any existing
    per-seller secret (they're identical across all six)."""
    try:
        existing = _read_secret(sm, APP_SECRET_NAME)
        if all(k in existing for k in APP_FIELDS):
            return f"app secret already present: {APP_SECRET_NAME}"
        return f"app secret present but missing fields {APP_FIELDS}: {APP_SECRET_NAME} (manual fix needed)"
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    payload = {k: source_seller_secret[k] for k in APP_FIELDS}
    if dry_run:
        return f"[plan] CREATE {APP_SECRET_NAME} with keys {list(payload)}"
    sm.create_secret(Name=APP_SECRET_NAME, SecretString=json.dumps(payload))
    return f"[apply] CREATED {APP_SECRET_NAME}"


def _trim_seller_secret(sm, name: str, *, dry_run: bool) -> str:
    secret = _read_secret(sm, name)
    if SELLER_FIELD not in secret:
        return (
            f"[skip] {name} missing {SELLER_FIELD!r} — not a seller credentials secret?"
        )
    extras = [k for k in secret if k != SELLER_FIELD]
    if not extras:
        return f"[skip] {name} already trimmed"

    new_payload = {SELLER_FIELD: secret[SELLER_FIELD]}
    if dry_run:
        return f"[plan] TRIM  {name} (drop {extras})"
    sm.put_secret_value(SecretId=name, SecretString=json.dumps(new_payload))
    return f"[apply] TRIMMED {name} (dropped {extras})"


def _run(dry_run: bool) -> int:
    sm = boto3.client("secretsmanager")
    seller_secrets = _list_seller_secrets(sm)
    if not seller_secrets:
        print(
            f"No per-seller secrets found under {SECRETS_PREFIX}/. "
            "Set AWS_PROFILE / AWS_REGION and try again."
        )
        return 1

    print(f"Found {len(seller_secrets)} per-seller secret(s):")
    for s in seller_secrets:
        print(f"  - {s}")
    print()

    sample_seller = seller_secrets[0]
    sample = _read_secret(sm, sample_seller)
    missing = [k for k in APP_FIELDS if k not in sample]
    if missing:
        print(
            f"ERROR: {sample_seller} is missing app-level field(s) {missing}; "
            "cannot source app secret values from it. Migration may already "
            "be partially applied — inspect manually."
        )
        return 2

    print(_ensure_app_secret(sm, sample, dry_run=dry_run))
    for name in seller_secrets:
        print(_trim_seller_secret(sm, name, dry_run=dry_run))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="Show what would change; no AWS writes.")
    sub.add_parser("apply", help="Execute create/trim. Idempotent.")
    args = parser.parse_args()
    return _run(dry_run=args.command == "plan")


if __name__ == "__main__":
    sys.exit(main())
