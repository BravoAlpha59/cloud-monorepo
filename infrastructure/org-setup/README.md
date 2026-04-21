# infrastructure/org-setup

AWS Organizations, OU structure, SCPs, IAM Identity Center, and CloudTrail setup artifacts for the cloud-monorepo.

Authoritative walkthrough: [docs/chat-summaries/08-Organization Setup](../../docs/chat-summaries/08-Organization%20Setup/08-Organization%20Setup.md).

## Status: partially applied (Phases 1–5 done)

### Applied

- **Root**: `r-b2n7`
- **Management**: `sincerelyhers-management` — `504804196123` (aws-mgmt@sincerelyhers.com)
- **OUs**:
  - `sincerelyhers-internal` — `ou-b2n7-hyxkrhhl`
  - `sincerelyhers-saas` — `ou-b2n7-1t37srxw` (empty; reserved)
- **Member accounts, both in `sincerelyhers-internal`**:
  - `sincerelyhers` — `637445353164` (rarrington@sincerelyhers.com) — PROD, joined 2026/04/17
  - `sincerelyhers-dev` — `431412299701` (aws-dev@sincerelyhers.com) — DEV, created 2026/04/21

### Phases to execute

1. ~~Create the management account (new root email).~~ **Done.**
2. ~~Enable AWS Organizations — **all features** mode (required for SCPs).~~ **Done.**
3. ~~Invite `sincerelyhers` (`637445353164`) as a member account.~~ **Done.**
4. ~~Create OUs: `sincerelyhers-internal`, `sincerelyhers-saas`.~~ **Done.**
5. ~~Create `sincerelyhers-dev` account under Organizations. Move both member accounts into `sincerelyhers-internal`.~~ **Done.**
6. **Next:** Enable and attach SCPs:
   - `RegionLockdown` → `sincerelyhers-internal` OU (`ou-b2n7-hyxkrhhl`)
   - `ProtectCloudTrail` → `sincerelyhers-internal` OU
   - `ProtectProductionSecrets` → `sincerelyhers` (`637445353164`) only
7. Enable IAM Identity Center in the management account. Create user, permission sets (`AdministratorAccess`, `DeveloperAccess`, `ReadOnlyAccess`), and account assignments.
8. Activate IAM billing access in each member-account root (one root login each, one-time).
9. Enable CloudTrail (all regions) in each member account.

After Phase 7, day-to-day access is via `aws configure sso` using the Identity Center portal URL. No root logins needed thereafter.

## SCP definitions (drafts — not yet attached)

Ready to paste into the Organizations console during Phase 6. Re-save the final applied version here if anything is edited at attach time.

- [`scp-region-lockdown.json`](scp-region-lockdown.json) — attach to `sincerelyhers-internal` OU (`ou-b2n7-hyxkrhhl`). Denies every action whose `aws:RequestedRegion` is not `us-east-2` or `us-east-1`, except for callers assuming `OrganizationAccountAccessRole` (so the bootstrap cross-account role is never locked out).
- [`scp-protect-cloudtrail.json`](scp-protect-cloudtrail.json) — attach to `sincerelyhers-internal` OU. Denies `cloudtrail:StopLogging`, `DeleteTrail`, `UpdateTrail` for every principal, including admins.
- [`scp-protect-production-secrets.json`](scp-protect-production-secrets.json) — attach to the `sincerelyhers` prod account only (`637445353164`). Denies Secrets Manager writes on `sp-api/*` except when the caller is `arn:aws:iam::637445353164:role/DeploymentRole`.

When attaching in the console, leave the default `FullAWSAccess` policy attached alongside these — SCPs are deny-only, and removing `FullAWSAccess` turns the attachment into an allowlist (which is not what we want).

## Still to be added here

- **Identity Center permission-set templates** (IaC form if pursued).
- **DeploymentRole** trust policy and permissions (the role assumed during `sam deploy` that owns writes to prod Secrets Manager). Must exist before `ProtectProductionSecrets` is attached in prod, or the SCP will block every principal including yourself.
- **OrganizationAccountAccessRole** trust-policy reference for the record.
