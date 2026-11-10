# System state map

A single-page picture of **what's deployed where** across the whole monorepo: the five event-driven pipelines, the shared base stack, the governance foundation, and the pending roadmap. Where the other pages zoom into one slice, this one is the wide shot — read it to orient, then follow the links into [02-amazon-runtime.md](02-amazon-runtime.md), [03-iac-topology.md](03-iac-topology.md), or [04-secrets-and-auth.md](04-secrets-and-auth.md) for depth.

Authoritative sources: [infrastructure/base-stack.yaml](../../infrastructure/base-stack.yaml), [platforms/amazon/template.yaml](../../platforms/amazon/template.yaml), [platforms/amazon/rotation-template.yaml](../../platforms/amazon/rotation-template.yaml), [infrastructure/org-setup/](../../infrastructure/org-setup/). Applied IDs and status live in [infrastructure/org-setup/README.md](../../infrastructure/org-setup/README.md) — not here.

## How to read the colors

Unlike the other pages (which color by service type), this map's job is deployment status, so **fill color = state** and **node shape = kind** (cylinder = datastore/queue, stadium = external-to-AWS, rectangle = compute/rule):

| Color | Meaning |
|---|---|
| 🟩 green | Live in production |
| 🟧 amber (dashed) | Dev only / disabled — not cut over to prod |
| 🟦 blue | Shared base stack (per-env) |
| 🟪 violet | Governance foundation |
| 🟦 teal (dashed) | Pending / roadmap |
| 🟥 pink (stadium) | External to AWS |

## Platform stack — notification relays

Two structurally identical relays, both **live in prod**. Each SP-API notification lands on a per-type SQS queue (own DLQ, 3× redrive), a Lambda reads the per-seller webhook secret and HMAC-signs an HTTPS POST to Odoo, and emits a CloudWatch metric. They differ only in the dispatch key: the feed relay keys on `sellerId`, Data Kiosk on the full `accountId`.

```mermaid
flowchart LR
    subgraph feed["Feed relay — KK · LLG · CO"]
      direction LR
      SPf(["Amazon SP-API<br/>FEED_PROCESSING_FINISHED<br/>dest 6172efe4"])
      Qf["SQS<br/>prod-sp-api-feed-ready<br/>+ DLQ 3x"]
      Lf["FeedRelay<br/>Lambda"]
      Of(["Odoo<br/>amazon_feed_status"])
      SPf -. event .-> Qf --> Lf -. HMAC POST .-> Of
    end

    subgraph dk["Data Kiosk relay — all 6 sellers"]
      direction LR
      SPd(["Amazon SP-API<br/>DATA_KIOSK_QUERY_PROCESSING_FINISHED<br/>dest cb91dc51"])
      Qd["SQS<br/>prod-sp-api-datakiosk-ready<br/>+ DLQ 3x"]
      Ld["DataKioskRelay<br/>Lambda"]
      Od(["Odoo<br/>amazon_datakiosk_status"])
      SPd -. event .-> Qd --> Ld -. HMAC POST .-> Od
    end

    SM[("Secrets Manager<br/>.../webhooks/*")]
    CW["CloudWatch<br/>SincerelyHers/AmazonRelay"]
    Lf -->|GetSecretValue| SM
    Ld -->|GetSecretValue| SM
    Lf -->|PutMetricData| CW
    Ld -->|PutMetricData| CW

    classDef live fill:#dcfce7,stroke:#16a34a,color:#000
    classDef shared fill:#dbeafe,stroke:#2563eb,color:#000
    classDef external fill:#fce7f3,stroke:#db2777,color:#000
    class Qf,Lf,Qd,Ld live
    class SM,CW shared
    class SPf,Of,SPd,Od external
```

## Platform stack — reports pipeline

**Deployed to prod but dev-only in practice** — the six per-seller EventBridge rules ship `DISABLED` and no prod `REPORT_PROCESSING_FINISHED` subscriptions exist, so this round-trip runs only in dev. Full time-ordered behavior (including the idempotent skip on externally-requested reportIds) is in [02-amazon-runtime.md](02-amazon-runtime.md).

```mermaid
flowchart LR
    EB["EventBridge<br/>6 per-seller cron<br/>DISABLED"]
    RR["ReportRequester<br/>Lambda"]
    SP(["Amazon SP-API"])
    DDB[("DynamoDB<br/>amazon-report-jobs")]
    Q["SQS<br/>sp-api-report-ready<br/>+ DLQ 3x"]
    RP["ReportProcessor<br/>Lambda"]
    S3[("S3<br/>sincerelyhers-reports-{env}")]
    SES["SES<br/>report-ready email"]
    SM[("Secrets Manager<br/>.../{alias}/credentials")]

    EB --> RR
    RR <-->|createReport| SP
    RR -->|PutItem REQUESTED| DDB
    SP -. REPORT_PROCESSING_FINISHED .-> Q --> RP
    RP -->|GetItem / UpdateItem| DDB
    RP <-->|getReportDocument| SP
    RP -->|PutObject + presigned URL| S3
    RP -->|SendEmail| SES
    RR -->|GetSecretValue| SM
    RP -->|GetSecretValue| SM

    classDef disabled fill:#fef3c7,stroke:#d97706,color:#000,stroke-dasharray:4 3
    classDef shared fill:#dbeafe,stroke:#2563eb,color:#000
    classDef external fill:#fce7f3,stroke:#db2777,color:#000
    class EB,RR,DDB,Q,RP,S3,SES disabled
    class SM shared
    class SP external
```

## Credential rotation stack

A per-app stack ([rotation-template.yaml](../../platforms/amazon/rotation-template.yaml)), deployed in **dev** for the `services` and `bobnathan` apps, running as policy C (**alert-only** — no auto-rotate). Two SP-API app-management notifications feed two queues; a daily monitor watches for expiring old secrets; the operator triggers an actual rotation by hand. Design history in [docs/design/credential-rotation.md](../design/credential-rotation.md).

```mermaid
flowchart LR
    SPe(["SP-API notif<br/>Client Secret Expiry"])
    Qe["SQS<br/>secret-expiry + DLQ"]
    EH["ExpiryHandler<br/>Lambda"]

    SPn(["SP-API notif<br/>Client New Secret"])
    Qn["SQS<br/>new-secret + DLQ"]
    CRP["CredentialRotationProcessor<br/>Lambda"]

    OP(["Operator<br/>aws lambda invoke"])
    RRq["RotationRequester<br/>Lambda"]

    CRON["EventBridge<br/>old-secret-monitor<br/>daily · ENABLED"]
    OSM["OldSecretMonitor<br/>Lambda"]

    DDB[("DynamoDB<br/>rotation-events<br/>GSI by-app-and-time")]
    SES["SES<br/>rotation alerts"]
    SM[("Secrets Manager<br/>.../app/credentials")]

    SPe -. event .-> Qe --> EH
    SPn -. event .-> Qn --> CRP
    OP --> RRq
    CRON --> OSM
    EH --> DDB
    EH --> SES
    RRq -->|GetSecretValue| SM
    RRq --> DDB
    RRq --> SES
    CRP -->|"GetSecretValue<br/>PutSecretValue"| SM
    CRP --> DDB
    CRP --> SES
    OSM -->|Query GSI| DDB
    OSM --> SES

    classDef disabled fill:#fef3c7,stroke:#d97706,color:#000,stroke-dasharray:4 3
    classDef shared fill:#dbeafe,stroke:#2563eb,color:#000
    classDef external fill:#fce7f3,stroke:#db2777,color:#000
    class Qe,EH,Qn,CRP,RRq,CRON,OSM,DDB disabled
    class SM,SES shared
    class SPe,SPn,OP external
```

## Shared base + governance foundation

The base stack ([base-stack.yaml](../../infrastructure/base-stack.yaml)) is deployed once per environment; platform and rotation stacks import its exports via `Fn::ImportValue`. Underneath sits the governance layer — applied out-of-band from the management account; this repo only stores the policy source. Deep-dives: [03-iac-topology.md](03-iac-topology.md) and [01-organizations.md](01-organizations.md).

```mermaid
flowchart TD
    subgraph base["Shared base stack (per env)"]
      direction LR
      RB[("S3 ReportsBucket<br/>30d→IA · 90d→Glacier")]
      AB[("S3 DeployArtifactsBucket<br/>SAM zips · expire 30d")]
      SESID["SES sender identity"]
      SEC[("Secrets Manager<br/>sp-api/sincerely-services/*")]
    end

    subgraph gov["Governance foundation"]
      direction LR
      ORG["AWS Organizations<br/>mgmt · prod · dev<br/>+ saas OU (future)"]
      SCP["3 SCPs<br/>RegionLockdown ·<br/>ProtectCloudTrail ·<br/>ProtectProductionSecrets"]
      IDC["IAM Identity Center<br/>DeveloperAccess · SSO profiles"]
      DR["IAM DeploymentRole + STS<br/>sole prod sp-api/* writer"]
      CT["CloudTrail<br/>multi-region · both accounts"]
      CFN["CloudFormation · SAM<br/>two-level stacks"]
    end

    base -->|exports imported by| PLAT["Platform + rotation stacks"]
    CFN -->|sam deploy assumes| DR
    DR -->|writes| SEC
    SCP -.enforces.-> DR
    SCP -.protects.-> CT

    classDef shared fill:#dbeafe,stroke:#2563eb,color:#000
    classDef gov fill:#ede9fe,stroke:#7c3aed,color:#000
    classDef plat fill:#dcfce7,stroke:#16a34a,color:#000
    class RB,AB,SESID,SEC shared
    class ORG,SCP,IDC,DR,CT,CFN gov
    class PLAT plat
```

## Pending & roadmap

Open TODOs, ordered by readiness. The one real dependency: SES must move off the sandbox before the reports feature can go to prod.

```mermaid
flowchart LR
    R1["Extend feed relay<br/>SH · 73J · OH<br/>scoped — additive"]
    R3["SES domain identity<br/>+ production access"]
    R2["Reports pipeline<br/>→ prod cutover"]
    R4["Credential rotation<br/>→ prod (D1 SCP)"]
    R5["Pre-signed URL<br/>12h → 7 days"]
    R3 --> R2

    classDef pending fill:#cffafe,stroke:#0891b2,color:#000,stroke-dasharray:4 3
    class R1,R2,R3,R4,R5 pending
```

| Item | Status | Gate / blocker |
|---|---|---|
| **Extend feed relay to SH · 73J · OH** | Scoped, not started (2026-07-08) | Purely additive — no re-point, no new credentials, reuses feed dest `6172efe4`. Gated on confirming Odoo `amazon_feed_status` tolerates all feed types (`FEED_PROCESSING_FINISHED` can't be filtered by type). |
| **Reports pipeline → prod cutover** | Planned | Enable the 6 `DISABLED` rules + create prod subs. Hits the re-point wall — dev holds all six `REPORT_PROCESSING_FINISHED` subs (one per seller × type), so each is a `delete` then `create`, stopping dev delivery. |
| **SES domain identity + production access** | Prerequisite | Blocks the reports cutover — sandbox mode requires every recipient be individually verified. Move to a domain identity and request production access first. |
| **Credential rotation → prod** | Gated · SCP | Needs the D1 carve-out amending `ProtectProductionSecrets` so `CredentialRotationProcessorRole` can `PutSecretValue` on `app/credentials`, applied from the management account. |
| **Pre-signed URL 12h → 7 days** | Backlog | Needs CloudFront signed URLs (or long-lived IAM creds, which the access model disallows) — not achievable from a Lambda execution role alone. |

## What's NOT here

- **Runtime sequence** of the reports round-trip — see [02-amazon-runtime.md](02-amazon-runtime.md).
- **Deploy-time flow** and DeploymentRole mechanics — see [03-iac-topology.md](03-iac-topology.md).
- **Secret layout and LWA token exchange** — see [04-secrets-and-auth.md](04-secrets-and-auth.md).
- **SCP semantics and account tree** — see [01-organizations.md](01-organizations.md).
