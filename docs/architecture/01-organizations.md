# AWS Organizations layout

The org structure for Sincerely Services AWS workloads. Three accounts under one Organization, two OUs, three SCPs, IAM Identity Center for human access. Authoritative status (applied IDs, attachment dates, last-verified timestamps) lives in [infrastructure/org-setup/README.md](../../infrastructure/org-setup/README.md). This page is the *shape*, not the *state*.

## Account tree, OUs, SCP attachments

OU IDs (referenced by the diagram below):

| OU | ID | Status |
|---|---|---|
| `sincerelyhers-internal` | `<INTERNAL-OU-ID>` | live — holds prod and dev |
| `sincerelyhers-saas` | `<SAAS-OU-ID>` | reserved — empty, future SincerelySaaS workloads |

```mermaid
flowchart TD
    Root(["AWS Organization Root<br/>&lt;ORG-ID&gt;  •  &lt;ORG-ROOT-ID&gt;"])

    Mgmt["sincerelyhers-management<br/>&lt;MGMT-ACCOUNT-ID&gt;<br/>aws-mgmt@sincerelyhers.com<br/><br/>billing, SCP authority,<br/>Identity Center tenant<br/>(no workloads)"]

    subgraph InternalOU["OU: sincerelyhers-internal"]
      direction LR
      Prod["sincerelyhers (PROD)<br/>&lt;PROD-ACCOUNT-ID&gt;<br/>rarrington@sincerelyhers.com"]
      Dev["sincerelyhers-dev (DEV)<br/>&lt;DEV-ACCOUNT-ID&gt;<br/>aws-dev@sincerelyhers.com"]
    end

    subgraph SaasOU["OU: sincerelyhers-saas (reserved)"]
      Future["future SincerelySaaS<br/>workloads (none yet)"]
    end

    Root --> Mgmt
    Root --> InternalOU
    Root --> SaasOU

    %% SCP attachments
    SCPRegion>"SCP: RegionLockdown<br/>deny actions outside<br/>us-east-2 / us-east-1<br/>(except OrganizationAccountAccessRole)"]
    SCPCT>"SCP: ProtectCloudTrail<br/>deny StopLogging,<br/>DeleteTrail, UpdateTrail<br/>for ALL principals"]
    SCPSec>"SCP: ProtectProductionSecrets<br/>deny Secrets Manager writes<br/>on sp-api/* except DeploymentRole"]

    SCPRegion -.attached.-> InternalOU
    SCPCT -.attached.-> InternalOU
    SCPSec -.attached.-> Prod

    classDef account fill:#e6f2ff,stroke:#2563eb,color:#000
    classDef mgmt fill:#fef3c7,stroke:#d97706,color:#000
    classDef scp fill:#fee2e2,stroke:#dc2626,color:#000
    class Prod,Dev,Future account
    class Mgmt mgmt
    class SCPRegion,SCPCT,SCPSec scp
```

## What each SCP does

| SCP | Attached to | Effect | Why |
|---|---|---|---|
| `RegionLockdown` | `sincerelyhers-internal` OU | Denies any action whose `aws:RequestedRegion` is not `us-east-2` or `us-east-1`. Excepts callers assuming `OrganizationAccountAccessRole` | Single-region cost discipline + smaller attack surface; us-east-1 carve-out for global services (IAM, STS, CloudFront) that route there |
| `ProtectCloudTrail` | `sincerelyhers-internal` OU | Denies `cloudtrail:StopLogging`, `DeleteTrail`, `UpdateTrail` for **all** principals (admins included) | Tamper-resistance for the audit log. Once a trail exists it cannot be silenced without management-level SCP detach |
| `ProtectProductionSecrets` | `sincerelyhers` (prod) account | Denies `secretsmanager:DeleteSecret`, `PutSecretValue`, `UpdateSecret` on `sp-api/*` unless caller is `DeploymentRole` | Production credential changes go only through `sam deploy` (via CloudFormation assuming DeploymentRole). Humans cannot write prod SP-API secrets even with admin perms |

Authoritative SCP JSON: [infrastructure/org-setup/scp-region-lockdown.json](../../infrastructure/org-setup/scp-region-lockdown.json), [scp-protect-cloudtrail.json](../../infrastructure/org-setup/scp-protect-cloudtrail.json), [scp-protect-production-secrets.json](../../infrastructure/org-setup/scp-protect-production-secrets.json).

## Identity Center — who can do what, where

IAM Identity Center is the only path for human console/CLI access. Tenant lives in `us-east-2`.

| Account | Permission sets assigned to your Identity Center user | What that grants |
|---|---|---|
| `sincerelyhers-dev` | `AdministratorAccess` (AWS managed) | Full perms in dev — for hands-on iteration |
| `sincerelyhers` (PROD) | `DeveloperAccess` (custom inline policy) + `ReadOnlyAccess` (AWS managed) | DeveloperAccess: read-only on stack-managed services + `lambda:InvokeFunction` + `cloudformation:*Stack*` + scoped `iam:PassRole` to DeploymentRole only. ReadOnlyAccess for services DeveloperAccess deliberately omits (notably `cloudtrail:*`) |
| `sincerelyhers-management` | `ReadOnlyAccess` | Org/Identity Center audit APIs that are management-only (`organizations:List*`, `sso-admin:List*`) |

Authoritative custom-policy source: [infrastructure/org-setup/permission-set-developer-access.json](../../infrastructure/org-setup/permission-set-developer-access.json).

## Deploy-time identity

```mermaid
flowchart LR
    Dev[Developer] -- aws sso login --> Portal["AWS access portal<br/>d-9a67598c3b.awsapps.com/start"]
    Portal -- DeveloperAccess role --> CFN["sam deploy<br/>+ --role-arn DeploymentRole"]
    CFN -- assumes --> DR["DeploymentRole<br/>(CFN service role,<br/>per-account)"]
    DR --> Stack["CloudFormation:<br/>creates/updates stack resources<br/>incl. Secrets Manager writes"]

    classDef human fill:#dcfce7,stroke:#16a34a,color:#000
    classDef role fill:#fef3c7,stroke:#d97706,color:#000
    class Dev human
    class DR role
```

The developer never has direct write access to `sp-api/*` Secrets Manager values in prod. Writes flow only through `DeploymentRole`, which is exempted by name in the `ProtectProductionSecrets` SCP.

## What's NOT here

- **Sub-account workload architecture** — see [02-amazon-runtime.md](02-amazon-runtime.md) for the Amazon platform's runtime topology.
- **Numeric state and applied dates** — see [infrastructure/org-setup/README.md](../../infrastructure/org-setup/README.md).
- **Cross-account peering / VPC topology** — none. This is a serverless monorepo; no VPCs are owned by these stacks.
