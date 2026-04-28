# Reporting & Feeds Automation (Workflows 1 + 2)

**Status:** Draft — initial capture from chat on 2026-04-28. Open to in-place edits and clarification rounds.
**Created:** 2026-04-28
**Owner:** Bob (rarrington@sincerelyhers.com)
**Related:**
- [cost-minimization-review.md](cost-minimization-review.md) — overlapping cost-driver analysis for the Amazon platform.
- Future Odoo replacement system — separate design effort, not yet documented. Both workflows below depend on Odoo today, so the replacement timeline materially affects integration depth.

## Why this exists

Bob identified two existing manual workflows that are good candidates for full automation. This doc captures what was discussed in chat, names the new building blocks each workflow needs, and numbers the open decisions and clarifications. Treat each numbered item as a discussion point — edit in place or flag in chat.

Working agreement: Bob fills in the **"Today's flow"** and **"Open clarifications"** sections from real-world knowledge; Claude proposes the technical decision options. Conversations target a specific item by number (e.g. "W1.D2" or "W2.C3").

---

## Cross-cutting context

### Building blocks already in place

- EventBridge schedule rules (per-seller, per-cadence)
- Reports Lambda → SP-API → S3 → DynamoDB → SES pipeline (today: SH and KK in dev, both currently disabled because validated)
- Per-seller credentials in Secrets Manager (`sp-api/sincerely-services/{alias}/credentials`)
- `python-amazon-sp-api` client library
- SAM-based IaC, two-level stack pattern

### Building blocks not yet in place (W1 + W2 combined)

- OneDrive / Microsoft Graph API delivery
- Walmart Marketplace API integration (`platforms/walmart/` is a placeholder)
- AWD Inventory Reports SP-API endpoint
- Odoo data ingress (file landing in AWS)
- Excel workbook generation under automation
- Multi-day workflow orchestration (waiting / polling for AWD availability)

---

## Workflow 1 — Monthly FBA Inventory Cost Report

### Outcome

Excel workbook delivered to the SH CFO each month, combining FBA inventory + AWD inventory + Odoo purchase-cost data for the just-completed reporting month.

### Today's flow (Bob to fill in)

> Walk through who does what, where files land, and how the CFO consumes the workbook today.

_(to be written)_

### Target automated flow

- **Day 1 of month:** retrieve FBA inventory reports for SH, LLG, OH, 73J, CO. Retrieve Odoo purchase-cost data.
- **Day 4 onward:** check daily for AWD inventory data covering day 1 of the month. Continue until present (AWD lags current date by a few days).
- **When all data is present:** run the Excel workbook generation script. Deliver workbook to CFO via OneDrive (or NAS — see W1.D1).

### Sources

| Source | Today | Required for | Owner of integration |
|---|---|---|---|
| Amazon SP-API Reports (FBA all-inventory) | Already wired in dev for SH + KK (now disabled, validated) | Day-1 fetch for SH, LLG, OH, 73J, CO | This monorepo |
| Amazon SP-API Reports (AWD inventory) | Not yet implemented | Day-4-onward poll | This monorepo (new) |
| Odoo purchase-cost data | File on NAS (manually retrieved today?) | Day-1 ingest | Odoo / NAS-side |

### Destination

- **Preferred:** new monthly folder in Microsoft OneDrive, labeled by reporting month/year.
- **Alternative:** share on company NAS.

### Open decisions (W1.D)

1. **OneDrive vs NAS for delivery.** OneDrive is materially easier from Lambda (Microsoft Graph API + OAuth, mirrors SP-API auth pattern). NAS from Lambda requires VPC + on-prem network connectivity and is the more painful path. Recommend OneDrive unless the CFO requires files served from inside the corporate network.
2. **Odoo integration: push or pull.** Strong recommendation: Odoo (or a NAS-side cron) drops a file at a known S3 prefix, Lambda waits for the S3 event. Avoids VPN/VPC complexity, decouples timing, mirrors how Target/Rithum already work. Pull alternative (VPC Lambda + on-prem networking) is more brittle and probably wasted effort given Odoo's expected replacement.
3. **Seller list — is KK intentionally excluded?** The list provided was SH, LLG, OH, 73J, CO; KK was not named. Working assumption: KK is FBM-only. Confirm.
4. **Excel workbook: where does it run?** Existing Python script. If pure Python (`openpyxl`, `pandas`, etc.), fits in Lambda. If it drives Excel itself (xlwings, COM, macros), it doesn't — would need Fargate, scheduled EC2, or a Windows VM. Need to look at the script.
5. **AWD-availability check cadence and orchestrator.** Daily polling Lambda that exits early if AWD data isn't present yet is simplest. Alternative is Step Functions with a wait-state — overkill for a 1–N day wait.
6. **"All data present" trigger.** Two options: (a) the AWD-poll Lambda is the orchestrator and triggers Excel generation when it succeeds; (b) a manifest object in S3 tracks completion of all inputs and a separate Lambda watches the manifest. (a) is simpler; (b) is more flexible if more inputs are added later.
7. **Reporting-month folder naming.** Format? `2026-04` / `April 2026` / `2026-04 FBA Inventory Cost`? Where in OneDrive (root, shared folder, CFO-shared)?
8. **Re-run / backfill behavior.** If a month's report is wrong or incomplete, what's the re-run path? Re-run on the same data or re-fetch from SP-API?

### Open clarifications (W1.C — Bob to fill in)

1. Today's manual process: step by step, including which person does what.
2. Where does the existing Excel-generation script live? Path / repo / language details. Pure Python or Excel-driven?
3. AWD inventory: which SP-API report type? `GET_FBA_AWD_INVENTORY_REPORT`? Confirm exact name.
4. Odoo purchase-cost data: file format, naming convention, NAS path today.
5. SH CFO delivery: email a link to the workbook, or shared OneDrive folder is enough?
6. Reporting-month definition: calendar month ending on the last day, or some other window?

---

## Workflow 2 — Daily FBM Inventory Quantity Updates

### Outcome

Each marketplace's available FBM inventory quantities stay in sync with Odoo on a daily basis, automatically.

### Today's flow (Bob to confirm / expand)

- Odoo produces daily available-FBM-inventory data residing on the company NAS.
- **Target (via Rithum):** Rithum retrieves the data on its own schedule. Already automated — no work needed in this workflow.
- **Amazon (KK, LLG):** manually pushed to SP-API today.
- **Walmart:** manually pushed to Walmart Marketplace API today.

### Sinks

| Sink | Method | Today | Required |
|---|---|---|---|
| Target (via Rithum) | Rithum-pulled scheduled fetch | ✅ Automated | (no work) |
| Amazon (KK, LLG) | SP-API Feeds (`POST_INVENTORY_AVAILABILITY_DATA`) or Listings Items API | Manual | Daily automated push |
| Walmart | Walmart Marketplace bulk inventory feed | Manual | Daily automated push |

### Open decisions (W2.D)

1. **Odoo ingress channel — same as W1.D2.** If push-to-S3 is chosen for W1, W2 inherits the channel: Odoo writes daily inventory to a known S3 prefix, two Lambdas (Amazon + Walmart) consume it.
2. **Walmart platform first implementation.** `platforms/walmart/` is a placeholder. This workflow is its first real workload. Pattern would mirror Amazon: SAM template, Secrets Manager (`walmart/{seller-alias}/credentials`), Lambdas for feed submit + status polling.
3. **Amazon SP-API surface for FBM inventory updates.** Two options:
   - **Feeds API** with `POST_INVENTORY_AVAILABILITY_DATA` (legacy XML). Battle-tested, bulk-friendly.
   - **Listings Items API** PATCH per SKU. Newer JSON, but per-SKU instead of bulk — many requests for many SKUs.
   For "daily bulk inventory update," Feeds API is the fit.
4. **Full push vs deltas.** Daily, do we send the entire FBM inventory each day or only changes vs yesterday? Full push is simpler and most platforms accept idempotent overwrite. Deltas optimize bandwidth at the cost of "what was sent yesterday" state.
5. **Seller scope confirmation.** W2 only names KK and LLG for Amazon. Confirming the other FBA-only sellers (SH, OH, 73J, CO) don't have FBM inventory to sync.
6. **Walmart account scope.** Single Walmart Marketplace account or multiple seller accounts (mirroring the Amazon per-alias pattern)?
7. **Failure handling and reporting.** If Amazon push succeeds but Walmart fails — retry, alert, partial-failure email?
8. **Cadence and timing.** Daily at a specific hour? After Odoo's data file lands? Window-of-day acceptable?

### Open clarifications (W2.C — Bob to fill in)

1. Today's manual push for Amazon: Seller Central UI, a local script someone runs, a third-party tool?
2. Same for Walmart.
3. Odoo file: format, schema, daily-naming convention, NAS path.
4. Are KK and LLG actually both FBM, or is one of them mixed FBA+FBM?

---

## Out of scope (for this doc)

- The future Odoo replacement system (separate design effort).
- Cost-minimization refactors of the existing Amazon platform — see [cost-minimization-review.md](cost-minimization-review.md).
- Multi-channel sales reporting, P&L, consolidated dashboards — adjacent but not part of these two workflows.
- Production cutover of the Amazon platform itself (currently dev-only).

---

## Suggested build sequence (if W1 leads)

W1 is the natural next step — same SP-API pattern, just more sellers + a couple of new pieces. Tentative order:

1. Re-enable + extend the SP-API report fetch for SH, LLG, OH, 73J, CO on day-1-of-month cadence (mostly EventBridge rules + the existing `ReportRequester`).
2. Stand up the OneDrive delivery Lambda — prove out Microsoft Graph auth and folder creation in isolation.
3. Define the Odoo-push contract (S3 prefix, file format, expected timing). Bob's Odoo / NAS side wires to that contract.
4. Add the AWD daily-poll Lambda (day 4 onward, exits early until data is present, delivers to the same monthly folder).
5. Trigger Excel generation when the folder is "complete" — define "complete" via an explicit manifest or count check (W1.D6).

W2 is genuinely separate — different platforms, different APIs, different cadence. Reasonable to defer until W1 is producing for at least one cycle.
