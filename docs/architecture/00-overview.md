# Architecture overview

Living architecture documentation for the cloud-monorepo. Each page covers one slice of the system, with mermaid diagrams that render natively on GitHub. Pages are intentionally short — they answer "what's the shape of this thing and what's locked" rather than re-deriving everything from code.

## Pages

| # | Topic | What it answers |
|---|---|---|
| [01](01-organizations.md) | AWS Organizations layout | Account tree, OUs, SCPs, Identity Center assignments |
| [02](02-amazon-runtime.md) | Amazon platform runtime flow | How a scheduled SP-API report goes from EventBridge cron to email-delivered S3 link |
| [03](03-iac-topology.md) | IaC topology | Two-level stack pattern, base ↔ platform exports/imports, sam deploy + DeploymentRole flow |
| [04](04-secrets-and-auth.md) | Secrets and auth | Per-seller secret layout, Lambda execution-role scoping, runtime LWA token exchange |

## Maintenance philosophy

These docs decay if not updated alongside code. Three habits keep them honest:

- **Cross-link from CLAUDE.md.** Each platform's CLAUDE.md should point at the relevant architecture page so future-Claude (and future-you) is reminded these exist when touching the relevant code path.
- **Update in the same PR.** A change that alters a diagram should update the diagram in the same commit. Reviewers should reject otherwise.
- **Authoritative state lives elsewhere.** When something is recorded in code, IaC, or a per-component README, this directory just *points there* — it doesn't duplicate the value. ARNs, account IDs, and applied SCPs live in [infrastructure/org-setup/README.md](../../infrastructure/org-setup/README.md), not here.
