# Extend `FEED_PROCESSING_FINISHED` → Odoo relay to SH, 73J, OH

**Status: IN PROGRESS (2026-11-11).** Approved by the user. The gate is cleared: Odoo
confirmed `amazon_feed_status` returns 2xx for feeds it doesn't own, whatever their type
(agent-mail thread `2026-11-10-amazon-sh-notifications`, 002). Tracks 1–2 code is on
branch `feat/feed-relay-sh-73j-oh` (PR #18). **Done 2026-11-11:** secrets deployed (`seller_id`s cross-checked against each seller's Data Kiosk merchant token) and `prod-FeedRelay` running with all six aliases (a canary invoke built the six-secret map). **Pending:** subscriptions (track 3) and verification.

**Re-verified 2026-11-11:** SH/73J/OH still have no `FEED_PROCESSING_FINISHED`
subscription; KK/LLG/CO still point at `6172efe4`; prod has `amazon-feed` secrets for
KK/LLG/CO only; `prod-FeedRelay` runs with `WEBHOOK_SELLER_ALIASES=KK,LLG,CO`. Dev has no
`amazon-feed` secrets at all, so the template default change doesn't affect dev in practice.

The "SH caveat" below is resolved: the external integration was the user's own idle
April `OrderChangesQueue` setup, and SH's report schedules were cancelled on 2026-11-10.

**`make deploy-amazon-secrets-prod` is now partial** (changed on this branch): it passes
only the `secrets/*.json` files that are staged and keeps every other parameter's current
value. So track 1 needs only `secrets/amazon-feed-{sh,73j,oh}.json` staged. It doesn't
need all 16 files, and it doesn't touch `app/credentials` unless
`secrets/app-credentials.json` is staged.

## Goal & rationale

The Amazon feed relay (`FEED_PROCESSING_FINISHED` → Odoo `amazon_feed_status`,
webhook-code `amazon-feed`) is live in prod for **KK/LLG/CO** only, where it's
used for inventory-update feeds. Feeds are generic — Amazon has a dozen-plus feed
types — and there's no per-seller reason the other three sellers (**SH, 73J, OH**)
can't use the same relay. This extends feed coverage to all six sellers.

This is a smaller job than the Data Kiosk cutover: it is **purely additive** (no
re-point), needs **no new credential secrets**, and **reuses the existing prod
feed destination**.

## ⚠️ Gating open question (resolve BEFORE creating subscriptions — track 3)

`FEED_PROCESSING_FINISHED` **cannot be filtered by feed type** — SP-API exposes no
processing filter for it. So once SH/73J/OH are subscribed, the relay forwards
**every** feed-processing-completed event for that seller across **all** feed types
(listings, pricing, fulfillment, images, …), not just inventory.

- **Odoo `amazon_feed_status` must gracefully tolerate feed types it doesn't
  handle** — no-op on unrecognized types, the way `ReportProcessor` drops unknown
  `report_id`s. If it instead errors on unexpected types, that's noise →
  DLQ churn. **Confirm this before enabling.**
- **SH caveat:** SH's account has an active external integration using the same
  SPP app (OrderChange destination + direct `createReport`; see
  `platforms/amazon/CLAUDE.md` → "Environmental knowns"). If that integration also
  submits feeds, those `FEED_PROCESSING_FINISHED` events will land in our feed
  queue too (subscriptions are app-global, one per seller × type — see
  [datakiosk handoff](datakiosk_query_finished_webhook.md) and the re-point lesson).
  Volume/cost is trivial, but Odoo will see feeds we never submitted.

The **relay code is fully generic** (forwards the raw body by `seller_id`, never
parses feed type), so there are **no relay code changes** — the question is
entirely Odoo-side tolerance.

## Verified current state (2026-07-08)

- **SH, 73J, OH have NO `FEED_PROCESSING_FINISHED` subscription** (`NotFound`) →
  this is **create-new**, not re-point. No delete, no dev disruption, and no risk
  to SH's external integration (it has no feed subscription).
- **KK/LLG/CO** feed subscriptions point at the prod feed destination
  `6172efe4-92f7-4ba8-af38-a9d9932f3eea` (name `prod-sp-api-feed-ready`) — **reuse
  it**; no new destination.
- **All six sellers already have prod `sp-api/sincerely-services/{alias}/credentials`
  secrets** (73J/OH/SH were added during the Data Kiosk cutover) → **no new
  credential secrets**.
- Prod feed infra already live: queue `prod-sp-api-feed-ready` (+ DLQ), Lambda
  `prod-FeedRelay`. Dispatch is on `payload…sellerId` via each `amazon-feed`
  secret's `seller_id` field (the bare merchant token — **not** the datakiosk
  `account_id`'s `amzn1.merchant.o.` prefix; the token is the bare part).

## Work items

| Track | Work |
|---|---|
| **1 — Secrets** | Add three `amazon-feed` webhook secret resources (SH/73J/OH) to [`platforms/amazon/secrets-template.yaml`](../../platforms/amazon/secrets-template.yaml): 3 `NoEcho` params + 3 `AWS::SecretsManager::Secret` (`…/{ALIAS}/webhooks/amazon-feed`, shape `{secret, url, seller_id}`), mirroring the KK/LLG/CO feed block. In the [`Makefile`](../../Makefile), `SECRETS_PROD_PARAMS` maps `amazon-feed-{sh,73j,oh}` to the three new parameters (done on this branch; the target is now partial, see Status). Stage `secrets/amazon-feed-{sh,73j,oh}.json` (operator supplies prod Odoo URL, HMAC secret, and `seller_id` = the seller's bare merchant token from `secrets/sellers.md`). Deploy via `make deploy-amazon-secrets-prod` (`DeploymentRole`). Note the `73J` logical-ID leading-digit is fine (validate-template accepts it; datakiosk used the same). |
| **2 — Platform stack** | ⚠️ **A template `Default` change does NOT reach an existing stack**: `sam deploy` reuses each parameter's previous value unless it is passed. Done 2026-11-11 by a CloudFormation change set (`--use-previous-template`, `WebhookSellerAliases=KK,LLG,CO,SH,73J,OH`, every other parameter `UsePreviousValue`); the stored value now persists across later `make deploy-amazon-prod` runs. Original plan: bump `WebhookSellerAliases` `Default` in [`platforms/amazon/template.yaml`](../../platforms/amazon/template.yaml) from `KK,LLG,CO` to all six. Deploy `make deploy-amazon-prod` (from `main`; container build needs `qemu-aarch64` binfmt registered — `docker run --privileged --rm tonistiigi/binfmt --install all` if the build hangs). |
| **3 — Subscriptions** | `uv run python scripts/sp_api_notifications.py create-subscription <alias> 6172efe4-92f7-4ba8-af38-a9d9932f3eea FEED_PROCESSING_FINISHED` for SH, 73J, OH (with `AWS_PROFILE=sincerelyhers-prod AWS_REGION=us-east-2`). **Pure creates** against the existing destination — no `delete-subscription`. |
| **4 — Odoo (sh-3)** | Register `amazon-feed-{sh,73j,oh}` webhook endpoint records + matching HMAC secrets on prod Odoo (`amazon_sh` module upgrade — seed-data records, not UI-editable), else POSTs return `404 Unknown endpoint`. |

## Ordering & traps

1. **Secrets (track 1) before platform (track 2).** The feed relay builds its
   `seller_id→alias` map by reading one `amazon-feed` secret **per alias in
   `WebhookSellerAliases` at cold start**. Bumping the alias list before the
   SH/73J/OH secrets exist makes the cold-start dispatch-map build fail.
2. **Odoo endpoints (track 4) before subscriptions go live (track 3).** Otherwise
   relayed POSTs `404` → SQS redrive → DLQ. (Feeds have no polling backstop like
   Data Kiosk's 5-min cron, so a 404 window here *is* lost delivery until fixed —
   do track 4 first.)
3. **No re-point / no dev disruption** — feed was never subscribed for these three
   in dev, so nothing to delete and dev is unaffected.

## What is NOT needed (vs the Data Kiosk cutover)

- No new SP-API destination (reuse `6172efe4`).
- No new credential secrets (all six already in prod).
- No relay code changes; no `delete-subscription`.

## References

- Live feed relay + prod deploy mechanics: [`platforms/amazon/CLAUDE.md`](../../platforms/amazon/CLAUDE.md) → "Production deployment".
- Sibling cutover (the template/Makefile/subscription pattern to copy): [datakiosk handoff](datakiosk_query_finished_webhook.md).
- Root TODO status: [`CLAUDE.md`](../../CLAUDE.md) → "Pending TODOs".
