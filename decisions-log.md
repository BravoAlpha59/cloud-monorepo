# Decisions Log

A record of forward-looking or easily-re-opened decisions. One entry per decision. Newest at the top. Present-tense facts belong in [CLAUDE.md](CLAUDE.md); this file is for "decided, don't re-litigate when we get there."

Entry format:

```
## YYYY-MM-DD — <short decision title>

**Context**: what prompted this decision; what's actually being chosen between.
**Decision**: the specific choice.
**Rationale**: why this over the alternatives (1–4 bullets).
**Scope**: where it applies and where it doesn't.
**Revisit if**: conditions under which this should be re-examined.
```

---

## 2026-04-20 — Customer identity for SincerelySaaS: Amazon Cognito User Pools (not IAM)

**Context**: The future SincerelySaaS app (Ready For Publishing in the Solution Provider Portal) will onboard external sellers as paying customers. Those customers need to sign up, log in, manage their accounts, and authorize SincerelySaaS to read their Amazon seller data. Question: can IAM / IAM Identity Center serve that role?

**Decision**: Use **Amazon Cognito User Pools** for all SincerelySaaS customer-facing identity. Keep IAM Identity Center strictly for workforce (internal team) access.

**Rationale**:
- IAM is a workforce tool. IAM users are capped at 5,000 per account and are not designed for self-service sign-up flows. Identity Center is similarly workforce-scoped (pricing, UX, portal model).
- Cognito is the AWS-native customer identity product: hosted/embedded sign-up and sign-in, password reset, MFA, email/SMS verification, federation (Google/Apple/SAML/OIDC), custom attributes, groups. API Gateway and ALB validate Cognito JWTs natively.
- Keeps workforce identity (Identity Center) and customer identity (Cognito) cleanly separated — different trust boundaries, different lifecycles.
- The SP-API LWA OAuth grant (where a customer authorizes SincerelySaaS to read their Amazon data) is **separate** from customer identity. The refresh token obtained via LWA is stored per Cognito user at `sp-api/sincerely-saas/{cognito-user-id}/credentials`, matching the app-isolation namespace already in [CLAUDE.md](CLAUDE.md).

**Scope**:
- Applies to SincerelySaaS only. Sincerely Services (this project's current focus) does not need customer identity — it operates on Sincerely Hers's own four seller accounts.
- Tenant isolation uses a single Cognito User Pool (not one pool per customer), per-user secret prefix, and AWS account-level separation via the `sincerelyhers-saas` OU.
- Does not prescribe frontend framework, authorization model (groups vs. custom attributes vs. ABAC), or billing/plan-tier mechanics — those are product-design decisions for when SincerelySaaS implementation starts.

**Revisit if**:
- Enterprise customers demand SAML/OIDC federation with advanced customization (per-tenant branding, custom flows) that pushes Cognito to its limits.
- Cross-product identity requirements emerge (e.g., shared identity across SincerelySaaS and Dicksons SKU Checker) that would be cleaner with a third-party IdP like Auth0, Clerk, or WorkOS.
- AWS announces a successor or materially changes Cognito pricing/features.
