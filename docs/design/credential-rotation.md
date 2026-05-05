# Credential Rotation (Application Management API)

**Status:** Draft — initial capture from chat on 2026-05-04. Open to in-place edits and clarification rounds.
**Created:** 2026-05-04
**Owner:** Bob (rarrington@sincerelyhers.com)
**Authoritative API spec:** [`amzn/selling-partner-api-models/models/application-management-api-model/application_2023-11-30.json`](https://github.com/amzn/selling-partner-api-models/blob/main/models/application-management-api-model/application_2023-11-30.json)
**Related:**
- [platforms/amazon/CLAUDE.md](../../platforms/amazon/CLAUDE.md) — current Amazon platform conventions and locked decisions.
- [reporting-and-feeds-automation.md](reporting-and-feeds-automation.md) — separate workstream; does not depend on this one.

## Why this exists

The SP-API Application Management API rotates the LWA `client_secret` on a registered SPP app. It is the natural next Amazon workstream: similar SQS-driven notification shape to our existing `REPORT_PROCESSING_FINISHED` pipeline. This doc captures the proposed AWS topology and the decisions that need answers.

Working agreement matches the reporting-and-feeds doc: numbered items below (D1, D2, …) are discussion targets — edit in place or flag in chat.

---

## What the API does

**Single endpoint** (per `application_2023-11-30.json`):

- `POST /applications/2023-11-30/clientSecret` → `204 No Content`
- Host: `https://sellingpartnerapi-na.amazon.com`
- **Grantless** — uses an LWA `client_credentials` token at the app level, *not* a seller refresh token.
- **Rate limit:** 0.0167 req/s, burst 1 (≈ once per minute, hard cap).
- The HTTP response is empty. The new `client_secret` is delivered **asynchronously** via SQS notification.
- `400 InvalidInput` if the SPP app is not enrolled — a destination queue must first be registered for the app's notification preferences.
- **Old credential lifetime:** 7 days after rotation. Quote from the rotation tutorial: *"The credential you use to call rotateApplicationClientSecret expires after seven days."* Update before this cliff.

## Two notifications, not one

The end-to-end loop is driven by **two distinct SP-API notifications**, each with its own Developer Console row:

| Developer Console row | NotificationType | Fires when | Carries |
|---|---|---|---|
| **Application Client Secret Expiry** | `APPLICATION_OAUTH_CLIENT_SECRET_EXPIRY` | Current secret approaches expiry, or after a rotation has been initiated and the old one is now on its 7-day clock | `clientId`, `clientSecretExpiryTime`, `clientSecretExpiryReason` (e.g. `"PERIODIC_ROTATION"`) |
| **Application Client New Secret** | TBD — capture from real payload during smoke test | After a successful `rotateApplicationClientSecret` call | The new `client_secret` value plus standard envelope |

**Standard SP-API notification envelope** on both: `notificationVersion`, `notificationType`, `payloadVersion`, `eventTime`, `payload.<typeSpecific>`, `notificationMetadata.{applicationId, subscriptionId, publishTime, notificationId}`.

**The expiry notification does *not* contain the new secret** — that's the New Secret notification's job. Treating these as one stream conflates two different consumer responsibilities (decide-when-to-rotate vs. install-the-rotated-secret).

## Documented enrollment path

The two tutorials Amazon publishes ([Set up credential rotation notifications](https://developer-docs.amazon.com/sp-api/docs/set-up-credential-rotation-notifications) and [Rotate your application's client secret](https://developer-docs.amazon.com/sp-api/docs/rotate-your-application-client-secret)) describe **Developer Console UI registration only**:

1. Grant SP-API SQS write permission via the AWS console (Principal `437568002678`, Actions `SendMessage` + `GetQueueAttributes`).
2. In Developer Console → Notification Preferences → select the SQS ARN against the relevant row ("Application Client Secret Expiry" or "Application Client New Secret").
3. The app starts emitting that notification type to that queue.

Whether the standard SP-API Notifications API (`createDestination` + `createSubscription`) also accepts these notification types is **undocumented**. The `notifications.json` OpenAPI model treats `notificationType` as a free-form string with no enum, so it can't be settled by reading the spec — only by trying. See C5.

For the first pass, plan around the documented UI-registration path and treat API-driven enrollment as a possible future simplification.

## How this differs from our existing report-ready pipeline

| Aspect | `REPORT_PROCESSING_FINISHED` (existing) | Credential rotation (proposed) |
|---|---|---|
| Subscription registration | SP-API Notifications API (`createDestination` + `createSubscription`) | **Developer Console UI** (per current docs); Notifications API path unverified |
| Scope | per `(seller × app)` | per app (all sellers share one stream) |
| Trigger frequency | continuous | rare — event-driven by expiry warnings, ≪ 1/day |
| Inbound SQS write principal | SP-API service writing cross-account | same — AWS account `437568002678` |
| Number of notification types | 1 | 2 (expiry warning + new secret) |
| What our consumer does | fetch report from SP-API, store in S3, email | call rotate → write Secrets Manager → verify |

## Today's secret layout — the problem this surfaces

[`platforms/amazon/src/sincerelyhers_amazon/credentials.py:24`](../../platforms/amazon/src/sincerelyhers_amazon/credentials.py) shows each of the six per-seller secrets stores all three of `client_id`, `client_secret`, `refresh_token`. The first two are **app-level** and identical across all six secrets. That made onboarding simpler, but means a single rotation event has to fan out to six secret writes — and the prod `ProtectProductionSecrets` SCP makes that fan-out a privileged operation.

**Refactor — D2 resolved 2026-05-05 as Option A, trim-in-place:**

- New: `sp-api/sincerely-services/app/credentials` — app-level: `{client_id, client_secret}`. One secret, one write per rotation.
- Existing per-seller `sp-api/sincerely-services/{alias}/credentials` are **trimmed in place** via `put-secret-value` to contain only `{refresh_token}`. No path renames; no new per-seller resources created (refresh tokens themselves stay valid — what SP-API formally calls a "secret" is `client_secret` paired with `client_id`, and that's what moves to the app-level secret; the refresh token is a per-seller authorization artifact that just stops carrying duplicated app-level fields).
- `credentials.py` reads both secrets and merges before handing to `python-amazon-sp-api`.

The rotation handler then writes **one** secret instead of six, and the SCP-protected write surface aligns with the actual rotation event.

## Proposed AWS resources

All under [`platforms/amazon/template.yaml`](../../platforms/amazon/template.yaml) — extends, not replaces, the existing stack. Two SQS queues (one per notification type), per the locked **per-notification-type queues with DLQs** decision in [platforms/amazon/CLAUDE.md](../../platforms/amazon/CLAUDE.md).

```
Developer Console (manual UI registration of both rows)
   "Application Client Secret Expiry"   →   sp-api-app-secret-expiry
   "Application Client New Secret"      →   sp-api-app-new-secret
                                            (each with own DLQ, 3× redrive)

  sp-api-app-secret-expiry  ─►  ExpiryHandler Lambda
                                  ├── log expiry timestamp + reason → DDB rotation-events
                                  ├── decision: rotate now? (D3)
                                  └── on yes: invoke RotationRequester (or call API directly)

  RotationRequester                 (called by ExpiryHandler, or manual, or cron)
    └── POST /applications/2023-11-30/clientSecret  → 204
                                          (new secret arrives later via SQS)

  sp-api-app-new-secret    ─►  CredentialRotationProcessor Lambda
                                  ├── parse new secret from payload
                                  ├── verify with LWA token exchange (fail-closed)
                                  ├── secretsmanager:PutSecretValue → app/credentials
                                  ├── DynamoDB row marking rotation COMPLETED
                                  └── SES alert: "rotated; old expires {expiryTime}"
```

Three Lambdas total — closely mirrors the existing `ReportRequester` / `ReportProcessor` split. ExpiryHandler is the new piece; it reacts to warnings and decides whether to trigger rotation (D3 controls that policy).

### IAM specifics

- **Inbound SQS write permission (both queues)**: queue resource policy grants `sqs:SendMessage` + `sqs:GetQueueAttributes` to `arn:aws:iam::437568002678:root`. (Verified principal — resolves prior D5.)
- **CredentialRotationProcessor execution role**: scoped to `secretsmanager:PutSecretValue` on `sp-api/sincerely-services/app/credentials` *only* — narrower than today's blanket `GetSecretValue` on `sp-api/sincerely-services/*`.
- **Prod SCP interaction (`ProtectProductionSecrets`)**: the SCP denies `PutSecretValue` on `sp-api/*` unless the caller is `DeploymentRole`. The rotation processor's role is not `DeploymentRole`. See D1.

---

## Open decisions (D)

1. **SCP carve-out vs `DeploymentRole` assumption.** *(Resolved 2026-05-05 as Option A.)*
   - Option A: Carve a tightly-scoped exception in `ProtectProductionSecrets` — allow `PutSecretValue` on `sp-api/sincerely-services/app/credentials` if the caller is `CredentialRotationProcessorRole`. Preserves the rest of the protection.
   - Option B: Have the processor assume `DeploymentRole`. Keeps the SCP clean but means a Lambda role can mint deploy-grade credentials, weakening `DeploymentRole`'s "only CloudFormation" intent.
   - **Resolved A.** SCP edit lands with prod cutover, not the dev refactor.

2. **Secret layout: refactor app-level vs leave per-seller-duplicated.** *(Resolved 2026-05-05 as Option A, trim-in-place.)*
   - Option A: Lift `client_id`/`client_secret` into a new `sp-api/sincerely-services/app/credentials`; trim each existing `sp-api/sincerely-services/{alias}/credentials` (via `put-secret-value`) to `{refresh_token}` only. One secret rewrite per rotation. Aligns with reality (`client_secret` is app-scoped).
   - Option B: Keep current layout, have the rotation processor write all six per-seller secrets atomically.
   - **Resolved A.** Implementation: see PR linked from issue #3. No new per-seller secret resources created; per-seller secret names stay as `{alias}/credentials`, just trimmed.

3. **Rotation policy — what does `ExpiryHandler` do with an expiry warning?** *(Resolved 2026-05-05 as Option C; graduate to A after multiple successful manual rotations.)*
   - Option A: Auto-rotate immediately on every warning. Fully closed-loop; never a manual step.
   - Option B: Auto-rotate only when `clientSecretExpiryReason` indicates Amazon-forced rotation; for periodic warnings, alert and let an operator decide.
   - Option C: Always alert, never auto-rotate; rotations happen by manual `RotationRequester` invoke.
   - **Resolved C.** ExpiryHandler logs the event to DynamoDB and emits an SES alert quoting the `aws lambda invoke` command for RotationRequester. No auto-rotation in current code; graduating to A is a one-line change to the handler once we've watched 3–4 manual cycles complete cleanly.

4. **Multi-app reuse.** *(Resolved 2026-05-05.)*
   Sincerely Services operates four SPP apps (Sincerely Services, SincerelySaaS, Dicksons SKU Checker, BobNathan-Test). Per C1 resolution: Sincerely Services and Dicksons are active; Dicksons runs outside this monorepo today; SincerelySaaS and BobNathan-Test are not in use. **Build with `SECRETS_PREFIX` parametrized from day one** — required to use BobNathan-Test as the smoke-test sandbox before the first live rotation against Sincerely Services. Each app gets its own pair of Developer Console preference rows and its own queue pair (per D5).

5. **One pair of queues per app, or shared?** *(Resolved 2026-05-05 as Option A — per-app queue pairs.)*
   Each SPP app has its own pair of Developer Console rows pointing at queue ARNs. We could give every app its own pair of queues, or have all apps point at one shared pair and branch on `notificationMetadata.applicationId` in the processor. **Resolved A — per-app queue pairs.** Implementation: `platforms/amazon/rotation-template.yaml` is parametrized by `AppShortName` + `SecretsPrefix` and deployed once per SPP app (`sincerelyhers-amazon-rotation-services-dev`, `sincerelyhers-amazon-rotation-bobnathan-dev`).

6. **Verification before promotion.** *(Resolved 2026-05-05 as verify-then-write.)*
   The `CredentialRotationProcessor` should exchange the new `client_secret` for an LWA token *before* writing it to Secrets Manager — fail-closed. If the new secret doesn't work, keep the old one, alert, and DLQ. Write-then-verify is unsafe (callers using the secret between write and verify could fail). **Resolved verify-then-write.** Implementation: the processor calls `grantless_access_token(new_secret)` and only proceeds to `PutSecretValue` if it succeeds; failure raises and the SQS message redrives toward the DLQ with the old secret intact.

7. **Old-secret overlap window monitoring.** *(Resolved 2026-05-05 with 24h default threshold.)*
   The 7-day overlap is real but tight. Record the old-secret expiry timestamp in DynamoDB and emit an alert when ≤ 24h remains. Catches integrations that didn't pick up the new secret before the cliff. **Implemented as `OldSecretMonitor` Lambda — daily EventBridge cron, queries the rotation-events table by app, alerts on `ROTATION_COMPLETED` rows whose `old_secret_expires_at` is within `ExpiryAlertHoursThreshold` (default 24h, parametrized).**

8. **Unsolicited rotations.**
   Rotation notifications can fire without us calling `rotateApplicationClientSecret` (e.g. Amazon force-rotates a compromised secret). The processor must handle either origin identically — design already does, but make it explicit in code comments and tests.

---

## Open clarifications (C — Bob to fill in)

1. **Are any of the four SPP apps (besides Sincerely Services) actively producing traffic today?** If SincerelySaaS / Dicksons / BobNathan-Test still have credentials in active use somewhere outside this monorepo, the multi-app design (D4) needs to widen.
2. **Has the Sincerely Services SPP app already had destination SQS queues registered in Developer Console?** Either row — if yes, where do they point? If they're unmanaged queues from a prior integration, that's a "stop-the-world" finding (similar to the `getReportSchedules` discovery captured in `platforms/amazon/CLAUDE.md`).
3. **Is there a target rotation cadence for compliance reasons (SOC2, customer contract, etc.) or is this purely operational hygiene?** Affects D3.
4. **Does the existing external integration in prod (account `<PROD-ACCOUNT-ID>`)** (the one issuing `GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA` calls outside this monorepo) **also use the Sincerely Services `client_id`/`client_secret`**? If yes, a rotation must coordinate with that integration's secret store too — otherwise rotation breaks them silently.
5. **Empirical: does `createSubscription` accept `APPLICATION_OAUTH_CLIENT_SECRET_EXPIRY` (and the new-secret type)?** Try it in dev once queues exist. If yes, future apps can be enrolled fully via IaC + API rather than the Developer Console UI step.

---

## Smoke-test path

The `rotateApplicationClientSecret` API is operator-callable, rate-limited only at 1/min — we trigger rotations on our schedule rather than waiting for organic expiry. The bottleneck on iteration speed is the 7-day old-secret-expiry clock, which only matters if we want to fully observe the late-stage `APPLICATION_OAUTH_CLIENT_SECRET_EXPIRY` warnings.

Three layers, in order of increasing fidelity:

### Layer 1 — Synthetic SQS messages (consumer-side regression)

Cheapest, fastest, repeatable. Hand-craft an SP-API notification envelope (the `notificationVersion` / `notificationType` / `payload` / `notificationMetadata` shape is documented), drop directly onto each queue via `aws sqs send-message`, watch the Lambda process it.

**Covers:** payload parsing, DDB writes, SES alert formatting, fail-closed behavior on bad-secret verification, error/DLQ paths.

**Doesn't cover:** the actual `rotateApplicationClientSecret` HTTP call, the cross-account SQS write from Amazon, the LWA token exchange against a real new secret, the actual "new secret" notification payload schema (which is TBD until first observed live).

Good for ~80% of regression testing once the pipeline exists. Not a substitute for a live rotation.

### Layer 2 — One controlled live rotation against BobNathan-Test (sandbox app)

BobNathan-Test exists explicitly as a developer-test SPP app (per `platforms/amazon/CLAUDE.md` SP-API App Isolation). One-time setup: authorize it for any seller (SH works), drop creds in Secrets Manager under `sp-api/bobnathan-test/...`, point the rotation pipeline at it (via `SECRETS_PREFIX` env var per D4), then call `rotate`. Exercises the full producer→consumer loop end-to-end against an app that nothing depends on.

The pipeline's `SECRETS_PREFIX` parametrization (see D4) is the enabling primitive — same handler code, different secret prefix, no cross-talk with the active Sincerely Services app.

After one successful round-trip on BobNathan-Test, the next live rotation against Sincerely Services is rehearsed.

### Layer 3 — Live rotation against Sincerely Services in dev

What the original smoke-test path prescribed. Same code path as prod; captures the real `APPLICATION_OAUTH_CLIENT_SECRET_NEW_SECRET` (or whatever the production enum turns out to be) notification payload schema. Confirm any seller still succeeds with the rotated secret. Optionally wait the 7-day window to observe the late-stage expiry warnings.

### Sequence (mirrors the build sequence below)

1. Build the pipeline with `SECRETS_PREFIX` parametrized.
2. Layer 1 in dev — synthetic messages cycle through both Lambdas; verify all happy and failure paths.
3. Layer 2 — one live rotation against BobNathan-Test; capture real new-secret payload, update doc and tests.
4. Layer 3 — one live rotation against Sincerely Services in dev. Verify any seller still succeeds.
5. Try `createSubscription` for both notification types (C5). Document the result.
6. Wait through the 7-day window to confirm late-stage expiry warnings arrive on the expiry queue (optional but completes the round-trip observation).

---

## Suggested build sequence

1. ~~Resolve D1 (SCP carve-out) and D2 (secret refactor) on paper.~~ **Done 2026-05-05** — see issue #3 Phase 1 comment.
2. Land the secret-layout refactor as a self-contained change. Verify all six sellers still succeed end-to-end. **In progress.**
3. ~~Add the two queues + DLQs + DDB table + three Lambdas to `platforms/amazon/template.yaml`. Parametrize `SECRETS_PREFIX` per D4.~~ **Done 2026-05-05** as `platforms/amazon/rotation-template.yaml` (separate stack per D5; four Lambdas including `OldSecretMonitor` per D7). Deployed via `make deploy-rotation-services-dev` and `make deploy-rotation-bobnathan-dev`.
4. Layer 1 smoke test — synthetic SQS messages exercise both Lambdas end-to-end through DDB and SES.
5. Authorize BobNathan-Test, install creds under `sp-api/bobnathan-test/...`, register its dev queues in SPP Developer Console.
6. Layer 2 smoke test — live rotation against BobNathan-Test. Capture the real new-secret notification payload; update tests + this doc with the actual schema.
7. Register Sincerely Services dev queues in SPP Developer Console.
8. Layer 3 smoke test — live rotation against Sincerely Services in dev. Verify any seller still succeeds with the rotated secret.
9. Try `createSubscription` for both notification types (C5).
10. Plan the prod cutover alongside the broader prod cutover already pending in `platforms/amazon/CLAUDE.md`. SCP amendment per D1 lands with prod cutover.

---

## Out of scope (for this doc)

- Rotation for the other three SPP apps — design is parametrized for them (D4) but not built.
- Refresh-token rotation (different mechanism: seller-initiated re-authorization in SPP, not API-driven).
- AWS-side IAM credential rotation for the existing `rarrington` IAM user — covered by the broader Identity Center migration, not this workstream.
