# Amazon platform — production bring-up runbook

**Status:** active · **Owner:** AWS (`cloud-monorepo`) · **Account:** `sincerelyhers` prod (`<PROD-ACCOUNT-ID>`)

The prod account is greenfield for the Amazon SP-API platform — Secrets Manager is
empty under `sp-api/*`, and neither `sincerelyhers-base-prod` nor
`sincerelyhers-amazon-prod` is deployed (only the org-setup `DeploymentRole` stack and a
foreign `SPAPI-Notifications-Stack` from the external integration exist). So shipping the
`FEED_PROCESSING_FINISHED` → Odoo relay to prod is a full platform bring-up, not an
incremental deploy. This runbook is the sequence.

The trigger for writing it was the feed-webhook relay (see
[feed_processing_finished_webhook.md](feed_processing_finished_webhook.md)), but Phases 1–3
stand up the whole prod Amazon foundation; the feed relay rides Phases 3–5.

## Constraints that shape the sequence

- **No CLI writes to prod `sp-api/*`.** The `ProtectProductionSecrets` SCP denies
  `PutSecretValue` / `UpdateSecret` / `DeleteSecret` on `sp-api/*` to everything except
  `DeploymentRole`, and project policy forbids ad-hoc CLI writes regardless. Every prod
  secret is therefore born from a `DeploymentRole`-driven `sam deploy`.
- **Hard ordering:** base → (credentials + platform, either order) → subscriptions →
  verify. The platform stack imports base-stack exports; subscriptions authenticate as
  each seller and so need the credential secrets present.
- **Deploy from `main`.** Merge the relevant PRs before the prod platform deploy.

## Phase 0 — Prep

- `.identifiers.local` present locally with `PROD_ACCOUNT_ID=<PROD-ACCOUNT-ID>` (+ app
  identifiers). `make` prod targets refuse to run without it.
- Stage prod secret material under the gitignored `secrets/` directory:

  | File | Shape | Consumed by |
  |---|---|---|
  | `secrets/app-credentials.json` | `{client_id, client_secret}` | Phase 2 (secrets stack) |
  | `secrets/credentials-{kk,llg,co}.json` | `{refresh_token}` | Phase 2 (secrets stack) |
  | `secrets/amazon-feed-{kk,llg,co}.json` | `{secret, url, seller_id}` | Phase 3 (platform stack) |

  The refresh tokens are the same values used in dev (authorization is Amazon-side; the
  tokens are account-independent). **Fix `secrets/amazon-feed-kk.json` `url`** — it was
  still an ngrok tunnel; it must be the prod Odoo host like CO/LLG.

## Phase 1 — Base stack · **[operator]**

```
source .identifiers.local && make deploy-base-prod
```

Creates the prod reports S3 bucket, the SES sender identity, and the
`sincerelyhers-deploy-artifacts-prod` bucket that Phases 2–3 upload to. The SES identity
triggers a verification email to `rarrington@sincerelyhers.com` — click it. SES
*production-access* (out of the sandbox) is a separate reports-feature concern and does
**not** block the feed relay, which sends no email.

> Deploys use `--s3-bucket sincerelyhers-deploy-artifacts-prod` (created above by
> `DeploymentRole`), **not** `--resolve-s3` — the latter bootstraps a bucket as the human
> SSO identity, which is intentionally not permitted to manage S3 in locked-down prod. So
> base must deploy before the credential/platform stacks.

## Phase 2 — SP-API credential bootstrap · **[operator]**

```
source .identifiers.local && make deploy-amazon-secrets-prod
```

Deploys [`platforms/amazon/secrets-template.yaml`](../../platforms/amazon/secrets-template.yaml)
under `DeploymentRole`: `app/credentials` + per-seller `credentials` for KK/LLG/CO, each
`Retain`-protected. Deployed *rarely* — bootstrap, re-key, or seller re-authorization —
which is why it is a separate stack from the frequently-redeployed platform stack (routine
code deploys never need this secret material on disk).

**Rotation-drift caveat:** once the prod credential-rotation pipeline is live (the D1 SCP
carve-out per [credential-rotation.md](../design/credential-rotation.md)), the
`CredentialRotationProcessorRole` owns the *value* of `app/credentials`. Do not redeploy
this stack thereafter without passing the **current** `app/credentials` value (or drop
`AppCredentials` from the stack at that point), or the deploy clobbers the rotated secret.
The per-seller refresh tokens are not API-rotatable, so they carry no such hazard.

## Phase 3 — Amazon platform stack · **[operator]**

Merge the feed-relay PR to `main` first, then:

```
source .identifiers.local && make deploy-amazon-prod
```

Stands up the queue/DLQ, `FeedRelay` Lambda + IAM, and the three webhook secrets
(`{alias}/webhooks/amazon-feed`, supplied from the `secrets/amazon-feed-*.json` files on
every prod deploy — keep them staged). `ReportRequester` / `ReportProcessor` deploy too
but stay idle: the report EventBridge rule ships `DISABLED` and no report subscriptions
exist. Requires the Phase 1 base exports.

## Phase 4 — SP-API feed subscriptions · **[operator]**

On the `sincerelyhers-prod` profile (so the script reads prod credentials and calls as the
prod sellers):

```
AWS_PROFILE=sincerelyhers-prod uv run python scripts/sp_api_notifications.py \
    create-destination prod-sp-api-feed-ready \
    arn:aws:sqs:us-east-2:<PROD-ACCOUNT-ID>:prod-sp-api-feed-ready
AWS_PROFILE=sincerelyhers-prod uv run python scripts/sp_api_notifications.py \
    create-subscription KK  <destination-id> FEED_PROCESSING_FINISHED
AWS_PROFILE=sincerelyhers-prod uv run python scripts/sp_api_notifications.py \
    create-subscription LLG <destination-id> FEED_PROCESSING_FINISHED
AWS_PROFILE=sincerelyhers-prod uv run python scripts/sp_api_notifications.py \
    create-subscription CO  <destination-id> FEED_PROCESSING_FINISHED
```

## Phase 5 — Verify

Submit one real FBM listings feed per seller; confirm in prod `FeedRelay` CloudWatch logs
that each notification relays and Odoo flips the matching `amazon.sh.spapi.feed.status`
record. Check the DLQ stays empty and no `OdooWebhookAuthFailure` metric fires
(`SincerelyHers/AmazonRelay`).

## Explicitly deferred (not on the feed-webhook critical path)

- **Prod credential-rotation pipeline + the D1 SCP carve-out** — only needed once prod
  rotation is deployed; the feed relay does not require it.
- **SES production-access** — needed for the reports feature's email, not the feed relay.
- **Enabling the report EventBridge rule** — left `DISABLED` until the reports feature is
  cut over to prod.
