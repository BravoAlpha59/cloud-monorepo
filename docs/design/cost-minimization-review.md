# Cost Minimization Review (Deferred)

**Status:** Open question, not actionable yet.
**Created:** 2026-04-23
**Revisit after:** Full Amazon seller onboarding round complete (LLG, 73J, OH, CO).
**Authoritative cost figures:** Pending — re-derive from Cost Explorer once Phase 8 (IAM billing access) of the AWS Organization setup is finished. Numbers below are list-price estimates, not measured spend.

## Why this exists

Bob raised the cost question on 2026-04-23 right after the KK onboarding wrapped. The hypothesis was that storing data on AWS is expensive and that "deliver and clean up" patterns might cut costs — particularly for reports (always re-requestable) and for upcoming SP-API notification flows. This document captures the analysis so we can act on it after the rest of the seller onboarding lands without changing the current plan.

**Constraint:** Do not refactor the Amazon platform until LLG, 73J, OH, CO are onboarded. Adding a sixth alias to a churning architecture is more painful than re-shaping the architecture once and migrating six sellers in one cutover.

## Current architecture (Amazon platform, dev)

```
EventBridge schedule rule (per seller, daily)
  → ReportRequester Lambda
    → SP-API createReport
    → DynamoDB jobs row (REQUESTED)

SP-API REPORT_PROCESSING_FINISHED event
  → SQS sp-api-report-ready (DLQ 3× redrive)
    → ReportProcessor Lambda
      → SP-API getReportDocument + download + gunzip
      → S3 (sincerelyhers-reports-dev/amazon/sincerely-services/{ALIAS}/.../{reportId}.tsv)
      → DynamoDB jobs row (COMPLETED)
      → SES email with 12-hour pre-signed URL
```

Reference: [platforms/amazon/CLAUDE.md](../../platforms/amazon/CLAUDE.md) — First Milestone section.

## Cost model at current scale (6 sellers, ~1 report/seller/day)

Approximate monthly spend, list price, dev account, no measurement yet:

| Service | Usage | Monthly cost | Notes |
|---|---|---|---|
| EventBridge schedule rules | 6 rules × ~30 fires | $0 | Schedule rules on default bus are free |
| Lambda (ReportRequester + ReportProcessor) | ~360 invocations + low duration | $0 | Well under free tier (1M req/mo + 400K GB-sec) |
| SQS (`sp-api-report-ready` + DLQ) | ~200 messages | $0 | First 1M req/mo free |
| DynamoDB jobs table (on-demand) | ~200 writes/reads, <1 MB | <$0.05 | $1.25/M writes, $0.25/M reads, $0.25/GB-mo |
| S3 reports bucket | ~200 objects, ~1 GB | ~$0.05 | $0.023/GB-mo + negligible request charges |
| SES outbound emails | ~200/mo | ~$0.02 | $0.10/1000 emails |
| **Secrets Manager** | **6 seller secrets + ~2 misc** | **~$3.20** | **$0.40/secret/mo — the actual scaling line item** |
| CloudWatch Logs | Lambda log streams | $0 | Free tier 5 GB ingest/mo |
| **Estimated total (dev)** | | **~$3.30** | Dominated entirely by Secrets Manager |

**Production scaling factor:** Add a SaaS-tier customer count to Secrets Manager (one secret per customer per app). At 100 customers: $40/mo. At 1000: $400/mo. **This is the only line that scales meaningfully with growth.**

## Bob's "deliver and clean up" hypothesis — evaluated

The instinct is sound but mis-targets the cost driver. Two separate ideas inside it:

### Idea A: Skip S3, attach reports to the email directly

**Pros:** No S3 cost, no pre-signed URL machinery, no 12-hour expiry, simpler ops, the email IS the artifact.

**Cons / open questions:**
- **SES attachment limit is 40 MB raw / ~30 MB after MIME encoding.** What's the typical TSV size for `GET_FLAT_FILE_OPEN_LISTINGS_DATA`? Unknown — measure on the next 2-3 KK runs before deciding.
- Recipient mail systems sometimes strip large attachments at delivery.
- Loses easy "give me the same report from last week" recovery, but Bob's framing already accepts re-request as the recovery path.
- The DynamoDB jobs table is **still needed** even without S3 — it's load-bearing for idempotency. The platform CLAUDE.md describes catching `KeyError` from `get_job` for unknown report IDs from the *external* integration that shares the SPP app. Removing the jobs table would break that filter and cause us to download/process every external report too.

**Verdict:** Worth exploring for small reports (<25 MB). For larger reports, keep S3 but add a lifecycle policy.

### Idea B: S3 lifecycle policy to auto-expire reports

Trivial change. Pre-signed URL expires at 12h, so the bucket object has zero value after that. A 24-48h expiration rule cleans it up automatically.

**Cost saving:** Pennies.
**Real benefit:** Bucket stays empty-ish, no "is anything sensitive in there I forgot about?" anxiety, no manual cleanup.

**Verdict:** Easy win. Do this regardless of attachment-vs-link decision.

### Idea C: "Deliver and act" pattern for upcoming notification flows

This is where the real architectural payoff lives. Future SP-API Notifications flows (ORDER_CHANGE, FBA_OUTBOUND_SHIPMENT_STATUS_CHANGE, FEED_PROCESSING_FINISHED, etc.) will mostly be:

- Receive event
- Take an external action (send Slack, update an external system, trigger a downstream job)
- Done — no archival value in the event itself

The current report flow's "S3 + DDB + email" template is overweight for these. A leaner template for non-archival flows:

```
SP-API Notification → SQS → Lambda → external action → done
```

No S3, no jobs table for that flow, no email artifact. DLQ catches failures; SQS retention (default 4 days) is the only "history" anyone gets, and that's intentional.

**Verdict:** Bake this in as a second Lambda template before adding the next notification type. Don't retrofit the report flow into it — they have different requirements.

### Idea D: Split delivery into routine (webhook) vs. ad-hoc (SES)

Today the report flow uses SES email + 12-hour pre-signed URL for **every** report — both the daily scheduled runs and any one-off human request. This conflates two genuinely different use cases:

| Aspect | Routine (machine → machine) | Ad-hoc (machine → human) |
|---|---|---|
| Trigger | EventBridge cron (daily per seller) | Human action: CLI, Slack command, future internal web form |
| Destination | Downstream system (ERP, inventory dashboard, billing tool, internal API) | Person's inbox |
| Format | JSON envelope or raw payload | Human-readable email body + link/attachment |
| Retry semantics | Must retry on 5xx, dedupe by report_id, alert on persistent failure | Fire-and-forget; if it bounces, the human asks again |
| Auth | Webhook signing (HMAC) or OAuth | None (recipient is trusted) |
| SLA | "Should land within minutes of report ready" | "Whenever" |

**Recommended split:**

- **Routine path** — `ReportProcessor → EventBridge custom event → API destination → downstream webhook URL`
  - EventBridge **API destinations** is the AWS-native fit. Built-in retry with exponential backoff, throttling, OAuth/API-key/Basic-auth credential storage in Connections. ~$1/M invocations. No retry logic to write or maintain.
  - Direct `httpx.post` from Lambda is cheaper but rebuilds retry/DLQ; not worth the maintenance burden once volume matters.
  - **Self-hosting the sender is overkill.** API destinations does what a self-hosted reverse-proxy webhook sender would do, with no infrastructure to patch. Self-hosting only enters the picture if there is an existing internal system on the *receiving* end that we're plugging into.
  - Per-seller webhook URLs configured in DDB (alongside or in the jobs table) or SSM Parameter Store. If URLs contain a secret token, Secrets Manager — but prefer HMAC signing of the payload over secrets-in-URL.
  - Webhook payload shape options:
    - **Reference**: small JSON envelope `{"report_id": "...", "url": "<signed-S3-URL>", "expires_at": "...", "signature": "..."}` — keeps S3 in loop, downstream pulls when ready
    - **Inline**: full parsed payload — eliminates S3 for small reports
    - Pick per-consumer or per-report-type; both can coexist

- **Ad-hoc path** — keep the current SES flow exactly as-is, but **only as the explicit human-request branch**
  - Triggered by an explicit human action (CLI subcommand, Slack slash command, future web form), not by a cron rule
  - SES + S3 + 12-hour signed URL is right for this — the email IS the human-friendly artifact
  - Volume stays low (occasional one-off requests), so cost stays low

**Why this split is the right architectural move:**

- Today's "everything goes to SES" pattern works for testing but does not scale to multiple downstream systems, multiple consumers per report, or any non-human integration
- Splits retry/SLA semantics cleanly — you can be aggressive about webhook retry without spamming someone's inbox
- Lets each path pick its own storage policy: routine path can drop S3 entirely once webhook delivery is acked; ad-hoc path keeps S3 + lifecycle policy for the link-expiry window
- The two paths share most of the upstream pipeline (EventBridge schedule rule for routine, manual trigger for ad-hoc, both feeding the same `ReportRequester → SP-API → ReportProcessor`); they only diverge at the **delivery** stage

**Verdict:** Strongest cost + architecture win in this review. Plan to introduce the routine/webhook path **before** the next downstream consumer needs it (don't build it speculatively, but don't wait until SES is straining either). SES stays as the explicit ad-hoc path indefinitely — it is the right tool for human-request reports.

**Caveat from Bob (2026-04-23):** "SES is a very simple testing mechanism for us now but not likely a long term repeated process." The split above operationalizes that — SES is **not** removed; it is **scoped down** to the use case it actually fits.

## Where the actual savings live

Sorted by impact:

1. **Secrets Manager strategy.** $0.40/secret/mo grows linearly. Two paths:
   - **(a) Consolidate.** Store all seller refresh tokens in a single JSON secret per app (`sp-api/sincerely-services/refresh-tokens` containing `{"SH": "...", "KK": "...", ...}`). Saves $0.40 × (N-1)/mo. **Tradeoffs:** larger blast radius if compromised; rotation becomes per-app not per-seller; IAM `GetSecretValue` policy can no longer scope to one seller (every Lambda that reads any token can read all). Probably **not worth it** for the internal Sincerely Services app — security boundary value > $2/mo savings. **Worth reconsidering** for SaaS tier where customer count makes the math flip.
   - **(b) Use SSM Parameter Store SecureString instead of Secrets Manager.** SSM SecureStrings are free for standard tier (advanced is $0.05/param/mo, still cheaper than Secrets Manager). Tradeoff: lose Secrets Manager's automatic rotation hooks, but SP-API refresh tokens don't auto-rotate anyway. **Possibly worth it.** Worth a deeper look.

2. **S3 lifecycle policy** — auto-delete reports after 48h. Pennies in dev, more meaningful as report volume grows in prod.

3. **Notification-flow template** that skips S3/DDB by default — set the precedent before the next flow is built.

## What to verify before deciding

- [ ] Actual TSV report sizes — pull last 5 reports from `sincerelyhers-reports-dev` and check sizes. Decides Idea A.
- [ ] Real measured spend via Cost Explorer once Phase 8 (IAM billing access) of org setup is done. Validates / corrects the table above.
- [ ] Whether SP-API refresh tokens ever rotate / expire in practice — affects SSM-vs-Secrets-Manager call.
- [ ] Whether the prod SES setup (domain identity + production access, listed as still-pending in platforms/amazon/CLAUDE.md) places any practical limit on attachment size in production.
- [ ] What the *next* notification flow actually is. Don't design the lean template against a hypothetical use case; design it against the first real one.

## Decisions deferred

| Decision | Defer until |
|---|---|
| Attach-vs-link for reports | After report-size measurement |
| Secrets Manager → SSM Parameter Store migration | After Phase 8 + a real Cost Explorer reading |
| Per-flow Lambda template (archival vs. deliver-and-act) | Before second notification flow is built |
| S3 lifecycle policy | Can be done **anytime**; smallest blast radius — possibly fold into final seller-onboarding commit |
| Routine vs. ad-hoc delivery split (webhook + SES) | Before first non-human downstream consumer needs report data |
| Webhook URL config storage (DDB vs. SSM vs. Secrets Manager) | When routine path is built |
| Webhook payload shape (reference vs. inline) | Per-consumer decision when routine path is built |

## Don't do (anti-patterns to flag if they come up)

- ❌ Refactor report flow before all six sellers are onboarded.
- ❌ Move secrets to env vars or files to "save money" — the cost-vs-security trade is wildly lopsided. Secrets Manager / SSM SecureString only.
- ❌ Build the "lean notification template" against a hypothetical use case — wait for the first real one.
- ❌ Drop the DynamoDB jobs table even if S3 goes away — it's load-bearing for the external-integration `KeyError` filter described in `platforms/amazon/CLAUDE.md`.
- ❌ Self-host a webhook **sender** to "save money" — EventBridge API destinations costs cents and removes the patching/cert/monitoring burden. Self-hosting only enters the picture for a *receiver* tied to an existing internal system.
- ❌ Remove SES — scope it down to the ad-hoc human-request path, but keep it. Email is the right channel for human-on-demand report requests and that use case will not go away.

## Context for next iteration (2026-04-23 update)

Bob noted at the close of this review that the next architectural question on deck is **SP-API Notifications for OrderChange**, and that two facts will reshape the cost/architecture math materially:

- **Volume**: across all seller accounts, several million orders annually. At ~5–10 OrderChange events per order lifecycle, that's roughly **1–4M events per month** — three to four orders of magnitude above the current ~200 reports/month volume that drove the table in this doc. The conclusion that "S3/DDB are pennies" holds for reports; it must be **re-derived for OrderChange** before being assumed.
  - At 4M/mo: SQS ~$1.20/mo, Lambda invocations ~$0.60/mo + duration, DynamoDB on-demand writes ~$5/mo, EventBridge API destinations ~$4/mo. Still inexpensive in absolute dollars but the per-event design choices (do we persist every event? dedupe how? hot-partition strategy? DLQ behavior at 1% failure = 40K stuck messages) actually start to matter.
- **Existing system of record (transient)**: order data currently lives in an **in-house ERP** (PostgreSQL-backed) instance covering all accounts. Bob clarified on 2026-04-23 that **a significant goal of this monorepo is to supplant that ERP**, not merely integrate with it. The canonical destination for normalized order/inventory data is therefore **this monorepo's own data layer** (the `shared/` Order, OrderLine, Product, Inventory, Report models named in the root CLAUDE.md), not the legacy ERP. The legacy ERP's role is a temporary source we will migrate off of.
  - "Downstream consumer" for the routine/webhook path is therefore phased: during parallel-run, deliveries may go to the legacy ERP to keep system-of-record stable; once the monorepo's data layer is ready, deliveries cut over to it and the legacy ERP is decommissioned. Don't design as if the legacy ERP is a permanent destination.
  - A controlled read-only window into the legacy ERP (replica, VPN, or PrivateLink) is valuable not just for design but for **migration validation and parallel-run reconciliation**. **Do not** propose network changes speculatively; wait until concretely needed and design the access path properly.

### Sizing implication of "supplant ERP" framing

The cost table above (Secrets Manager dominates at $3.30/mo) is right for the *current* scope of work but **wrong as a long-term sizing exercise** if this monorepo is going to take over the ERP role. Re-derive cost and storage architecture for ERP-replacement components against:

- **Volume**: several million orders/year × multiple events/order × multiple platforms (Amazon → Walmart, Shopify, Target, Faire all on the roadmap)
- **Retention**: ERP data has effectively infinite retention requirements — order history is consulted years out for finance, returns, fraud
- **Storage tier choices**: DynamoDB vs. Aurora PostgreSQL vs. S3-with-Athena become live questions, not pennies-vs-pennies
- **First-class concerns** that were afterthoughts at small scale: idempotency, audit trail, point-in-time recovery, schema evolution, multi-account fan-in/fan-out

These are out of scope for *this* review iteration but flagged so the deferred discussion doesn't accidentally lock in a decision that was right for "200 reports/mo" but wrong for "ERP system of record."

All items in this section belong to the next iteration of this review, not this one. Recorded here so they don't have to be re-explained.
