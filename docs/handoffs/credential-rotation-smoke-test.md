# Credential rotation — Layer 1 smoke test

**Goal:** Drop synthetic SP-API notification envelopes onto the deployed rotation queues and verify the Lambdas execute end-to-end against real AWS (DDB / SES / Secrets Manager / CloudWatch). Layer 1 from [docs/design/credential-rotation.md](../design/credential-rotation.md#smoke-test-path).

## Status

- Pipeline code merged on `main` (commit `61fb868`, PR #7).
- Both rotation stacks deployed in dev:
  - `sincerelyhers-amazon-rotation-services-dev` (CREATE_COMPLETE 2026-05-05)
  - `sincerelyhers-amazon-rotation-bobnathan-dev` (CREATE_COMPLETE 2026-05-05)
- App-level secrets exist:
  - `sp-api/sincerely-services/app/credentials` (live; do **not** touch in Layer 1)
  - `sp-api/bobnathan-test/app/credentials` (sandbox target for Layer 1)
- Unit tests under `platforms/amazon/tests/test_rotation.py` pass under moto and cover all four handlers (happy + failure paths). Layer 1 adds *deployed-Lambda-on-real-AWS* coverage on top.

## Next — run these against the **bobnathan** stack

1. **Expiry warning.** `aws sqs send-message` a synthetic `APPLICATION_OAUTH_CLIENT_SECRET_EXPIRY` envelope to `dev-sp-api-bobnathan-secret-expiry`. Verify: `EXPIRY_WARNING` row in `dev-amazon-bobnathan-rotation-events`, SES alert lands at rarrington@sincerelyhers.com, no rotation triggered.

2. **New-secret happy path.** Send synthetic `APPLICATION_OAUTH_CLIENT_NEW_SECRET` envelope where `newClientSecret` = the *current* BobNathan-Test client_secret (read from `sp-api/bobnathan-test/app/credentials`). Verify: LWA grantless exchange succeeds, PutSecretValue idempotently rewrites the secret (creates a new SM version with same content), `ROTATION_COMPLETED` row written, SES alert sent.

3. **New-secret fail-closed.** Same envelope shape, but `newClientSecret = "bogus-does-not-exchange"`. Verify: LWA exchange fails, `ROTATION_VERIFICATION_FAILED` row written, `sp-api/bobnathan-test/app/credentials` *unchanged* (compare AWSCURRENT version), handler raises → message redrives twice more (visibility timeout 300s) → ends up in `dev-sp-api-bobnathan-new-secret-dlq` after maxReceiveCount=3.

4. **OldSecretMonitor.** Seed a `ROTATION_COMPLETED` row in DDB with `old_secret_expires_at` ~12h from now (well within the 24h `EXPIRY_ALERT_HOURS` default). `aws lambda invoke` `dev-amazon-bobnathan-OldSecretMonitor` with `{}` payload. Verify: single SES alert summarizing the at-risk row.

After all four pass, update [credential-rotation.md](../design/credential-rotation.md) build sequence step 4 to "Done", then proceed to step 5 (authorize BobNathan-Test in SPP Developer Console + register dev queues) and Layer 2 — first live rotation.

## Open

- **Scenario 2 versioning concern.** Idempotent rewrite of `bobnathan-test/app/credentials` bumps the AWSCURRENT version pointer with identical content. Acceptable for the sandbox, but flag in chat if Bob wants to skip the write step (would require mocking — defeats the point of an end-to-end smoke).

## Don't

- Don't run any synthetic events against the `services` stack until Layer 2 is in flight — `sp-api/sincerely-services/app/credentials` is the live secret all six sellers depend on.
- Don't add auto-rotation to `ExpiryHandler`. D3 is **policy C** (alert-only); operator triggers `RotationRequester` explicitly. Graduating to A is intentional future work after 3-4 clean manual cycles.
- Don't invoke `RotationRequester` against either app yet — that's a real `POST /applications/.../clientSecret` call against Amazon and starts the 7-day old-secret clock. That's Layer 2 (bobnathan) / Layer 3 (services) territory.
- Don't deploy `rotation-template.yaml` to prod. SCP `ProtectProductionSecrets` must be amended per D1 first, alongside the broader prod cutover.

## Useful commands

```sh
# Queue URLs (output by the rotation stack):
aws cloudformation describe-stacks --profile sincerelyhers-dev --region us-east-2 \
  --stack-name sincerelyhers-amazon-rotation-bobnathan-dev \
  --query 'Stacks[0].Outputs'

# Read the current bobnathan client_secret for scenario 2:
aws secretsmanager get-secret-value --profile sincerelyhers-dev --region us-east-2 \
  --secret-id sp-api/bobnathan-test/app/credentials --query SecretString --output text

# Tail the four Lambdas' logs:
aws logs tail /aws/lambda/dev-amazon-bobnathan-ExpiryHandler --profile sincerelyhers-dev --region us-east-2 --follow
aws logs tail /aws/lambda/dev-amazon-bobnathan-CredentialRotationProcessor --profile sincerelyhers-dev --region us-east-2 --follow
aws logs tail /aws/lambda/dev-amazon-bobnathan-OldSecretMonitor --profile sincerelyhers-dev --region us-east-2 --follow

# DDB scan for rotation events:
aws dynamodb scan --profile sincerelyhers-dev --region us-east-2 \
  --table-name dev-amazon-bobnathan-rotation-events
```
