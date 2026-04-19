# infrastructure/org-setup

AWS Organizations, OU structure, SCPs, IAM Identity Center, and CloudTrail setup artifacts for the cloud-monorepo.

Authoritative walkthrough: [docs/chat-summaries/08-Organization Setup](../../docs/chat-summaries/08-Organization%20Setup/08-Organization%20Setup.md).

## Status: not yet applied

The Organization, OUs, SCPs, Identity Center, and CloudTrail are planned but not yet configured in AWS. The user is completing Phases 1–9 externally in the AWS console (root login is required at several steps).

### Phases to execute

1. Create the management account (new root email).
2. Enable AWS Organizations — **all features** mode (required for SCPs).
3. Invite `sincerelyhers` (`637445353164`) as a member account.
4. Create OUs: `sincerelyhers-internal`, `sincerelyhers-saas`.
5. Create `sincerelyhers-dev` account under Organizations. Move both member accounts into `sincerelyhers-internal`.
6. Enable and attach SCPs:
   - `RegionLockdown` → `sincerelyhers-internal` OU
   - `ProtectCloudTrail` → `sincerelyhers-internal` OU
   - `ProtectProductionSecrets` → `sincerelyhers` (prod) account only
7. Enable IAM Identity Center in the management account. Create user, permission sets (`AdministratorAccess`, `DeveloperAccess`, `ReadOnlyAccess`), and account assignments.
8. Activate IAM billing access in each member-account root (one root login each, one-time).
9. Enable CloudTrail (all regions) in each member account.

After Phase 7, day-to-day access is via `aws configure sso` using the Identity Center portal URL. No root logins needed thereafter.

## To be added here once applied

- **SCP JSON definitions** (final versions as applied): `scp-region-lockdown.json`, `scp-protect-cloudtrail.json`, `scp-protect-production-secrets.json`.
- **Identity Center permission-set templates** (IaC form if pursued).
- **DeploymentRole** trust policy and permissions (the role assumed during `sam deploy` that owns writes to prod Secrets Manager).
- **OrganizationAccountAccessRole** trust-policy reference for the record.
