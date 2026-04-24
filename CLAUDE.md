# cloud-monorepo — Cloud E-Commerce APIs w/ AWS

## Project Overview

A serverless AWS monorepo for Sincerely Services' e-commerce integrations across multiple seller/retailer platforms: Amazon, Walmart, Shopify, Target, Faire. Each platform is a uv workspace member. Cross-platform utilities live under `shared/`. Amazon is the first platform to come online; the others are placeholders.

## Repository Layout

- `shared/` — cross-platform utilities and normalized data models (planned: Order, OrderLine, Product, Inventory, Report). See "Shared Data Models" below.
- `platforms/amazon/` — Amazon SP-API integration. Has its own SAM `template.yaml`. See [platforms/amazon/CLAUDE.md](platforms/amazon/CLAUDE.md).
- `platforms/{walmart,shopify,target,faire}/` — placeholders.
- `infrastructure/` — IaC for cross-cutting AWS resources.
  - `base-stack.yaml` — SAM template for shared resources (S3, DynamoDB, SES, IAM patterns). Platform stacks import its exports.
  - `org-setup/` — AWS Organizations setup notes, SCP definitions, Identity Center permission sets.
  - `params/` — (future) per-environment parameter files (`dev.json`, `prod.json`).

Each workspace member has its own `pyproject.toml`; the root `[tool.uv.workspace]` composes them.

## Locked Cross-Cutting Decisions

These are final. Do not re-open them or suggest alternatives.

- **IaC**: AWS SAM (CloudFormation underneath). Two-level stack pattern — see "IaC Layout" below.
- **AWS SDK**: `boto3`.
- **Region**: `us-east-2` for all resources. `us-east-1` is also permitted for global services that route through it (IAM, STS, CloudFront).
- **Accounts**: three-account model under AWS Organizations (Root `r-b2n7`)
  - Management: `sincerelyhers-management` (`504804196123`) — owns the org, billing, SCPs; no workloads. At Root.
  - Prod: `sincerelyhers` (`637445353164`) — in `sincerelyhers-internal` OU (`ou-b2n7-hyxkrhhl`).
  - Dev: `sincerelyhers-dev` (`431412299701`) — in `sincerelyhers-internal` OU.
  - `sincerelyhers-saas` OU (`ou-b2n7-1t37srxw`) is reserved for the future SincerelySaaS public app.
- **Access model**: AWS IAM Identity Center (SSO). `aws configure sso` with named profiles `sincerelyhers-prod` and `sincerelyhers-dev`. No new long-lived IAM users in member accounts; existing `rarrington` user is historical and targeted for deprecation once Identity Center is fully stood up.
- **Prod write protection**: a `DeploymentRole` (assumed by CloudFormation during `sam deploy`) is the only principal allowed to write production Secrets Manager values under `sp-api/*`. Enforced via SCP — see below.
- **Secrets**: AWS Secrets Manager only. No env-var or file-based credentials. Naming includes the SPP app context — see "SP-API App Isolation".
- **Testing**: `pytest`.
- **Python runtime**: 3.14 (AWS Lambda runtime `python3.14`, Amazon Linux 2023).
- **Dependency / Python manager**: `uv`. Lambda packaging uses Option A: `uv export --frozen --no-dev` produces a per-Lambda `requirements.txt` during `make build`.

## AWS Account Context

- **Organization layout**: Root `r-b2n7` → `sincerelyhers-management` (at Root) / `sincerelyhers-internal` OU `ou-b2n7-hyxkrhhl` (prod + dev) / `sincerelyhers-saas` OU `ou-b2n7-1t37srxw` (future).
- **Region**: `us-east-2` (with `us-east-1` carve-out for global services).
- **Prod**: `sincerelyhers` — account ID `637445353164`.
- **Dev**: `sincerelyhers-dev` — account ID `431412299701`.
- **Management**: `sincerelyhers-management` — account ID `504804196123`.
- **Access**: AWS IAM Identity Center; profiles `sincerelyhers-prod` and `sincerelyhers-dev` via `aws configure sso`.
- **Historical IAM user**: `rarrington` in the prod account. Still present while Identity Center is being stood up; destination state is Identity Center only.
- **Secrets Manager naming**: platform-specific; see each platform's CLAUDE.md. Amazon uses `sp-api/sincerely-services/{seller-alias}/credentials`.

## Service Control Policies

Attached at the OU or account level from the management account. Do not suggest workarounds.

- **RegionLockdown** (attached to `sincerelyhers-internal` OU) — deny all actions outside `us-east-2` and `us-east-1`, except for `OrganizationAccountAccessRole`.
- **ProtectCloudTrail** (attached to `sincerelyhers-internal` OU) — deny `cloudtrail:StopLogging`, `cloudtrail:DeleteTrail`, `cloudtrail:UpdateTrail`.
- **ProtectProductionSecrets** (attached to the prod account only — dev stays flexible) — deny `secretsmanager:DeleteSecret`, `secretsmanager:PutSecretValue`, `secretsmanager:UpdateSecret` on `sp-api/*` unless the caller is `DeploymentRole`.

Authoritative source: [docs/chat-summaries/08-Organization Setup](docs/chat-summaries/08-Organization%20Setup/08-Organization%20Setup.md). Draft JSON lives in [infrastructure/org-setup/](infrastructure/org-setup/).

## IaC Layout

Two-level SAM / CloudFormation design:

- **Base stack** ([infrastructure/base-stack.yaml](infrastructure/base-stack.yaml)) — deployed once per environment. Contains the shared S3 bucket(s), shared DynamoDB tables, SES configuration, IAM role patterns, and CloudWatch dashboards. Exports ARNs for platform stacks to import.
- **Platform stacks** (`platforms/{platform}/template.yaml`) — deployed independently per platform. Contains only the Lambdas, SQS queues + DLQs, and EventBridge rules specific to that platform. Consumes base-stack exports via `Fn::ImportValue`.

This isolates platform deploys: a broken Faire deploy cannot block an Amazon fix.

Deploy examples:

```
sam deploy --template infrastructure/base-stack.yaml --stack-name sincerelyhers-base-prod --profile sincerelyhers-prod
sam deploy --template platforms/amazon/template.yaml --stack-name sincerelyhers-amazon-prod --profile sincerelyhers-prod
```

## SP-API App Isolation (Solution Provider Portal)

Sincerely Services operates four SPP apps. Each has its own secret-prefix namespace and, when deployed, its own account or OU:

- **Sincerely Services** (private; subject of this monorepo today) — prefix `sp-api/sincerely-services/`. Lives in the `sincerelyhers-internal` OU.
- **SincerelySaaS** (Ready For Publishing; future) — prefix `sp-api/sincerely-saas/`. Must live in a separate account under the `sincerelyhers-saas` OU when deployed. External customer credentials must never share an account with internal operations.
- **Dicksons SKU Checker** (Draft; future) — prefix `sp-api/dicksons/` (TBD). Account/OU decision deferred.
- **BobNathan-Test** (Draft; dev/test only) — not deployed.

Lambda execution roles scope `secretsmanager:GetSecretValue` to a single app prefix (e.g. `sp-api/sincerely-services/*`) so a bug cannot read another app's credentials.

## Shared Data Models

The long-term payoff of the monorepo is normalizing platform-specific data into common internal models under `shared/`:

- `Order`, `OrderLine`, `Product`, `Inventory`, `Report`.

Platform packages parse raw payloads from their source APIs and produce these normalized objects; downstream code (cross-platform P&L, consolidated notifications, unified inventory view) consumes only the normalized layer. Keep the shared layer platform-agnostic — do not add platform-specific fields to these models.

## Coding Conventions

- Python 3.14, type hints preferred.
- Follow the Python style rules in `~/.claude/CLAUDE.md` (Ruff formatting, `Optional[T]` over `T | None`, builtin generics, namespace imports for internal functions, direct imports for classes/exceptions, `pathlib.Path` over `os.path`, no shebangs on scripts).

## Toolchain

- Python version managed by `uv` via `.python-version` (currently 3.14).
- Dependencies defined in `pyproject.toml` files — never edit `requirements.txt` manually.
- `requirements.txt` files inside Lambda directories are build artifacts generated by `uv export --frozen --no-dev`; gitignored and regenerated by the per-platform `build-*` Make targets (e.g. `make build-amazon`).
- Run tests: `uv run pytest`
- Add a dependency: `uv add <package>`
- Add a dev dependency: `uv add --dev <package>`
- Build and deploy (per stack): `make deploy-base-dev`, `make deploy-amazon-dev`, and their `-prod` counterparts. There is no generic `make deploy` — base and platform stacks deploy independently on purpose, so a broken one platform can't block a fix on another.
- Local Lambda invocation: `sam local invoke <FunctionName> --event events/<event>.json`
- AWS CLI: always pass `--profile sincerelyhers-dev` or `--profile sincerelyhers-prod` (configured via `aws configure sso`).

## What Claude Code should NOT do

- Do not edit generated `requirements.txt` files.
- Do not use `pip install` — use `uv`.
- Do not suggest switching to poetry, pipenv, or a non-SAM IaC tool (CDK, Terraform, Serverless Framework).
- Do not suggest long-lived IAM users or access keys in dev or prod — Identity Center SSO only for human access, CloudFormation-assumed roles for deploys.
- Do not suggest writing to production Secrets Manager from the CLI — only `DeploymentRole` (via `sam deploy`) is permitted.
- Do not create resources outside `us-east-2` (or `us-east-1` for global services) — the `RegionLockdown` SCP will deny them.
- Do not store credentials in env vars, files, or code — Secrets Manager only.
- Do not re-open per-platform architecture decisions — see each platform's CLAUDE.md.
- Do not add platform-specific fields to `shared/` data models — keep them normalized.

## Repository State

Clean slate — implementation just starting. Amazon is the first platform; see [platforms/amazon/CLAUDE.md](platforms/amazon/CLAUDE.md) for its first milestone.

## Pending TODOs

- **Finish AWS Organization setup.** Phases 1–7 done (org/OUs/accounts, all three SCPs attached, DeploymentRole bootstrapped in prod, Identity Center + permission sets + account assignments, SSO profiles `sincerelyhers-prod` / `sincerelyhers-dev` on WSL2 resolve to correct roles). Still pending: Phase 8 (IAM billing access per member-account root, one-time) and Phase 9 (CloudTrail all-regions per member account — confirmed absent in both prod and dev on 2026-04-21). Authoritative status + applied IDs live in [infrastructure/org-setup/README.md](infrastructure/org-setup/README.md).
- **Add a `sincerelyhers-mgmt` SSO profile.** Assign your Identity Center user `ReadOnlyAccess` on the `sincerelyhers-management` account (if not already) and run `aws configure sso --profile sincerelyhers-mgmt`. Unlocks full org/account audit APIs (`organizations:List*`, `sso-admin:List*`) that are management-only — needed for thorough audits of SCP attachments, Identity Center assignments, and account placements. Low urgency but do it before the next big structural change.
- **Deploy `DeploymentRole` in dev** (optional but recommended for command-shape symmetry with prod). Template is the same [infrastructure/org-setup/deployment-role.yaml](infrastructure/org-setup/deployment-role.yaml).
- **Amazon platform identifier TODOs** — app name and LWA Client ID. Seller aliases: `SH` (Sincerely Hers) and `KK` are onboarded to dev. `LLG`, `73J`, `OH`, `CO` are pending SPP app authorization from each seller + per-seller EventBridge rule + per-seller refresh token in Secrets Manager at `sp-api/sincerely-services/{alias}/credentials`.
