# infrastructure/org-setup

AWS Organizations, OU structure, SCPs, IAM Identity Center, and CloudTrail setup artifacts for the cloud-monorepo.

Authoritative walkthrough: [docs/chat-summaries/08-Organization Setup](../../docs/chat-summaries/08-Organization%20Setup/08-Organization%20Setup.md).

## Status: partially applied (Phases 1–6 done)

### Applied

- **Organization**: `o-kx9xl1ypyl`
- **Root**: `r-b2n7`
- **Management**: `sincerelyhers-management` — `504804196123` (aws-mgmt@sincerelyhers.com)
- **OUs**:
  - `sincerelyhers-internal` — `ou-b2n7-hyxkrhhl`
  - `sincerelyhers-saas` — `ou-b2n7-1t37srxw` (empty; reserved)
- **Member accounts, both in `sincerelyhers-internal`**:
  - `sincerelyhers` — `637445353164` (rarrington@sincerelyhers.com) — PROD, joined 2026/04/17
  - `sincerelyhers-dev` — `431412299701` (aws-dev@sincerelyhers.com) — DEV, created 2026/04/21
- **SCPs attached to `sincerelyhers-internal`**: `RegionLockdown`, `ProtectCloudTrail` (alongside inherited `FullAWSAccess`).
- **SCP attached to `sincerelyhers` (prod) account**: `ProtectProductionSecrets`.
- **DeploymentRole deployed in prod**: `arn:aws:iam::637445353164:role/DeploymentRole` (CloudFormation stack `DeploymentRole`, bootstrapped 2026-04-21 via the rarrington IAM user + console upload).

### Phases to execute

1. ~~Create the management account (new root email).~~ **Done.**
2. ~~Enable AWS Organizations — **all features** mode (required for SCPs).~~ **Done.**
3. ~~Invite `sincerelyhers` (`637445353164`) as a member account.~~ **Done.**
4. ~~Create OUs: `sincerelyhers-internal`, `sincerelyhers-saas`.~~ **Done.**
5. ~~Create `sincerelyhers-dev` account under Organizations. Move both member accounts into `sincerelyhers-internal`.~~ **Done.**
6. ~~Enable and attach SCPs (RegionLockdown + ProtectCloudTrail on `sincerelyhers-internal`; ProtectProductionSecrets on prod after DeploymentRole bootstrap).~~ **Done.**
7. **Next:** Enable IAM Identity Center in the management account. Create user, permission sets (`AdministratorAccess`, `DeveloperAccess`, `ReadOnlyAccess`), and account assignments.
8. Activate IAM billing access in each member-account root (one root login each, one-time).
9. Enable CloudTrail (all regions) in each member account.

After Phase 7, day-to-day access is via `aws configure sso` using the Identity Center portal URL. No root logins needed thereafter.

## SCP definitions

- [`scp-region-lockdown.json`](scp-region-lockdown.json) — **attached** to `sincerelyhers-internal` OU (`ou-b2n7-hyxkrhhl`). Denies every action whose `aws:RequestedRegion` is not `us-east-2` or `us-east-1`, except for callers assuming `OrganizationAccountAccessRole`.
- [`scp-protect-cloudtrail.json`](scp-protect-cloudtrail.json) — **attached** to `sincerelyhers-internal` OU. Denies `cloudtrail:StopLogging`, `DeleteTrail`, `UpdateTrail` for every principal, including admins.
- [`scp-protect-production-secrets.json`](scp-protect-production-secrets.json) — **attached** to `sincerelyhers` prod account (`637445353164`) only. Denies Secrets Manager writes on `sp-api/*` except when the caller is `arn:aws:iam::637445353164:role/DeploymentRole`.

When attaching in the console, leave the default `FullAWSAccess` policy attached alongside these — SCPs are deny-only, and removing `FullAWSAccess` turns the attachment into an allowlist (which is not what we want).

## DeploymentRole

[`deployment-role.yaml`](deployment-role.yaml) — CloudFormation template that creates `DeploymentRole` in the account it is deployed into. This is the role `sam deploy` passes via `--role-arn`; in prod it is the only principal the `ProtectProductionSecrets` SCP allows to write `sp-api/*` Secrets Manager values.

**Trust**: `cloudformation.amazonaws.com`, with an `aws:SourceAccount == <this-account>` condition so the role can only be assumed by CloudFormation operating in its own account.

**Permissions**: broad on stack-managed services (`lambda`, `dynamodb`, `sqs`, `events`, `s3`, `ses`, `logs`, `cloudformation`), scoped IAM role management on roles in the same account (for Lambda execution roles etc.), and Secrets Manager writes restricted to `sp-api/*`. This is pragmatic rather than minimal — tighten once deploy surface stabilizes.

### Bootstrap order (one-time per account)

1. Deploy this template using an elevated principal — currently the `rarrington` user in prod, or `OrganizationAccountAccessRole` assumed from management. Use `--region us-east-2` (the `RegionLockdown` SCP will deny anything else).

   ```
   aws cloudformation deploy \
     --template-file infrastructure/org-setup/deployment-role.yaml \
     --stack-name DeploymentRole \
     --capabilities CAPABILITY_NAMED_IAM \
     --region us-east-2
   ```

2. In prod only: attach `ProtectProductionSecrets` to the `sincerelyhers` account via the Organizations console (Policies tab on the account → Attach → `ProtectProductionSecrets`). **Order matters** — the SCP's `ArnNotLike` exception references this role by ARN, so attaching before the role exists locks every principal out of `sp-api/*` writes.

3. Stack outputs the role ARN. From then on, `sam deploy` passes it via `--role-arn`:

   ```
   sam deploy \
     --template platforms/amazon/template.yaml \
     --stack-name sincerelyhers-amazon-prod \
     --role-arn arn:aws:iam::637445353164:role/DeploymentRole \
     --profile sincerelyhers-prod \
     --capabilities CAPABILITY_IAM \
     --resolve-s3
   ```

Deploying the same template in dev (`431412299701`) is optional but recommended for consistency — the `ProtectProductionSecrets` SCP does not apply there, so dev also works without a service role, but keeping the `sam deploy` invocation shape identical across envs avoids foot-guns.

## Still to be added here

- **Identity Center permission-set templates** (IaC form if pursued).
- **OrganizationAccountAccessRole** trust-policy reference for the record.
