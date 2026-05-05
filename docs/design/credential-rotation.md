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

**Proposed refactor (gated on this work landing) — see D2:**

- `sp-api/sincerely-services/app/credentials` — new, app-level: `{client_id, client_secret}`
- `sp-api/sincerely-services/{alias}/refresh-token` — per-seller: `{refresh_token}` only
- `credentials.py` reads both and merges before handing to `python-amazon-sp-api`.

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

1. **SCP carve-out vs `DeploymentRole` assumption.**
   - Option A: Carve a tightly-scoped exception in `ProtectProductionSecrets` — allow `PutSecretValue` on `sp-api/sincerely-services/app/credentials` if the caller is `CredentialRotationProcessorRole`. Preserves the rest of the protection.
   - Option B: Have the processor assume `DeploymentRole`. Keeps the SCP clean but means a Lambda role can mint deploy-grade credentials, weakening `DeploymentRole`'s "only CloudFormation" intent.
   - **Recommend A.** This is exactly the kind of narrow, audited carve-out SCPs are designed for.

2. **Secret layout: refactor app-level vs leave per-seller-duplicated.**
   - Option A: Refactor to `sp-api/sincerely-services/app/credentials` + `sp-api/sincerely-services/{alias}/refresh-token`. One secret rewrite per rotation. Aligns with reality (`client_secret` is app-scoped).
   - Option B: Keep current layout, have the rotation processor write all six per-seller secrets atomically.
   - **Recommend A.** B compounds the SCP write-surface and the onboarding script's per-seller fan-out; A pays the migration cost once.

3. **Rotation policy — what does `ExpiryHandler` do with an expiry warning?**
   - Option A: Auto-rotate immediately on every warning. Fully closed-loop; never a manual step.
   - Option B: Auto-rotate only when `clientSecretExpiryReason` indicates Amazon-forced rotation; for periodic warnings, alert and let an operator decide.
   - Option C: Always alert, never auto-rotate; rotations happen by manual `RotationRequester` invoke.
   - **Recommend A** once dev round-trip is trusted; **start at C** to validate the wiring without auto-firing the rotation API.

4. **Multi-app reuse.**
   Sincerely Services operates four SPP apps (Sincerely Services, SincerelySaaS, Dicksons SKU Checker, BobNathan-Test). Each has its own `client_secret` and its own pair of Developer Console preference rows. **Recommend** building for "Sincerely Services" only now, but parametrizing the Lambda's destination secret name so the same handler works for future apps.

5. **One pair of queues per app, or shared?**
   Each SPP app has its own pair of Developer Console rows pointing at queue ARNs. We could give every app its own pair of queues, or have all apps point at one shared pair and branch on `notificationMetadata.applicationId` in the processor. **Recommend per-app queue pairs** — simpler IAM, simpler ops, and any future app rotation runs through its own DLQ rather than poisoning a shared one.

6. **Verification before promotion.**
   The `CredentialRotationProcessor` should exchange the new `client_secret` for an LWA token *before* writing it to Secrets Manager — fail-closed. If the new secret doesn't work, keep the old one, alert, and DLQ. Write-then-verify is unsafe (callers using the secret between write and verify could fail). **Recommend** verify-then-write.

7. **Old-secret overlap window monitoring.**
   The 7-day overlap is real but tight. Record the old-secret expiry timestamp in DynamoDB and emit an alert when ≤ 24h remains. Catches integrations that didn't pick up the new secret before the cliff.

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

## Smoke-test path (mirrors how we validated reports)

1. Refactor secret layout per D2 (split app-level secret out, update `credentials.py`, migrate the six dev secrets, redeploy). Verify all six sellers still succeed end-to-end before adding rotation.
2. Add `sp-api-app-secret-expiry` and `sp-api-app-new-secret` SQS queues (each with DLQ), DDB `dev-amazon-rotation-events` table, and the three Lambdas to `platforms/amazon/template.yaml`. Deploy.
3. In Developer Console → Sincerely Services app → Notification Preferences, register the two dev queue ARNs against their respective rows. Capture both `notificationType` enum strings and full payload examples — store in `platforms/amazon/CLAUDE.md` under environmental knowns.
4. Manually invoke `RotationRequester` from dev.
5. Verify: `sp-api-app-new-secret` receives a message → processor verifies + writes secret + DDB row + SES alert.
6. Run `python-amazon-sp-api` against any seller (e.g. SH) using the rotated secret — confirm `getReportDocument` succeeds.
7. Wait until the old-secret expiry stamp passes (7 days); confirm a forced call with the old secret fails. Optionally: confirm `APPLICATION_OAUTH_CLIENT_SECRET_EXPIRY` warnings arrived on the expiry queue during the 7-day window.
8. Try `createSubscription` for both notification types (C5). Document the result.

---

## Suggested build sequence

1. Resolve D1 (SCP carve-out) and D2 (secret refactor) on paper. These set the IAM and storage shape everything else hangs on.
2. Land the secret-layout refactor as a self-contained change. Verify all six sellers still succeed end-to-end.
3. Add the two queues + DLQs + DDB table + three Lambdas to `platforms/amazon/template.yaml`.
4. Register the two dev queues in SPP Developer Console (manual UI step). Capture the new-secret notification type name and payload schema.
5. Smoke-test per the path above starting in policy mode C from D3 (alert-only, no auto-rotate). Once verified, graduate to A.
6. Plan the prod cutover alongside the broader prod cutover already pending in `platforms/amazon/CLAUDE.md`.

---

## Out of scope (for this doc)

- Rotation for the other three SPP apps — design is parametrized for them (D4) but not built.
- Refresh-token rotation (different mechanism: seller-initiated re-authorization in SPP, not API-driven).
- AWS-side IAM credential rotation for the existing `rarrington` IAM user — covered by the broader Identity Center migration, not this workstream.
