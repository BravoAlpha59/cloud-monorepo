# Architecture overview

Living architecture documentation for the cloud-monorepo. Each page covers one slice of the system, with mermaid diagrams that render natively on GitHub. Pages are intentionally short — they answer "what's the shape of this thing and what's locked" rather than re-deriving everything from code.

## Pages

| # | Topic | What it answers |
|---|---|---|
| [01](01-organizations.md) | AWS Organizations layout | Account tree, OUs, SCPs, Identity Center assignments |
| [02](02-amazon-runtime.md) | Amazon platform runtime flow | How a scheduled SP-API report goes from EventBridge cron to email-delivered S3 link |
| [03](03-iac-topology.md) | IaC topology | Two-level stack pattern, base ↔ platform exports/imports, sam deploy + DeploymentRole flow |
| [04](04-secrets-and-auth.md) | Secrets and auth | Per-seller secret layout, Lambda execution-role scoping, runtime LWA token exchange |

## Deferred decisions / future direction

This directory captures the architecture **as it is today**. For decisions that are *consciously deferred* — patterns we've evaluated but not yet built — see [docs/design/cost-minimization-review.md](../design/cost-minimization-review.md). Highlights:

- **Routine vs. ad-hoc delivery split.** Today's "everything goes to SES" pattern conflates machine-to-machine and human-request flows. Deferred plan: routine reports go via EventBridge API destinations → webhook to downstream consumer; SES scopes down to explicit human-on-demand requests. Trigger to build: before the first non-human downstream consumer needs report data.
- **Secrets Manager → SSM Parameter Store SecureString.** $0.40/secret/mo scales linearly with seller/customer count. Migration to SSM SecureString (free for standard tier) is open. Trigger: after Cost Explorer gives a real measured baseline.
- **Lean notification-flow template** that skips S3/DDB by default — for high-volume flows like SP-API OrderChange notifications (~1–4M events/mo projected). Trigger: before the second SP-API notification flow is built.
- **ERP-supplant framing.** A significant goal of this monorepo is to supplant the existing in-house ERP for order/inventory data of record. The `shared/` data models (Order, OrderLine, Product, Inventory, Report) are the canonical destination; the legacy ERP is a transient source we will migrate off of. This reshapes "downstream consumer" decisions for the routine webhook path.

These should be re-read before opening any of those questions in a session — to avoid re-deriving conclusions or breaking constraints already chosen.

## Maintenance philosophy

These docs decay if not updated alongside code. Three habits keep them honest:

- **Cross-link from CLAUDE.md.** Each platform's CLAUDE.md should point at the relevant architecture page so future-Claude (and future-you) is reminded these exist when touching the relevant code path.
- **Update in the same PR.** A change that alters a diagram should update the diagram in the same commit. Reviewers should reject otherwise.
- **Authoritative state lives elsewhere.** When something is recorded in code, IaC, or a per-component README, this directory just *points there* — it doesn't duplicate the value. ARNs, account IDs, and applied SCPs live in [infrastructure/org-setup/README.md](../../infrastructure/org-setup/README.md), not here.
