# cloud-monorepo

Serverless AWS monorepo for Sincerely Services' e-commerce integrations across multiple seller/retailer platforms (Amazon, Walmart, Shopify, Target, Faire). Each platform is a uv workspace member with its own SAM template; cross-platform utilities and normalized data models live under `shared/`.

Authoritative project conventions, locked decisions, and toolchain rules are in [CLAUDE.md](CLAUDE.md). Per-platform conventions are in `platforms/<name>/CLAUDE.md`. Architecture-level diagrams live under [docs/architecture/](docs/architecture/); design docs under [docs/design/](docs/design/).

## Setup

Requirements: [`uv`](https://docs.astral.sh/uv/), Docker (for `sam build --use-container`), AWS SAM CLI, AWS CLI configured for SSO.

```
make setup
```

Equivalent to `uv sync --frozen --all-packages`. The `--all-packages` flag is required: this repo's root `pyproject.toml` declares only the `[tool.uv.workspace]` members, so a plain `uv sync` would skip every workspace package and tests would fail with cryptic `AttributeError: module 'handlers' has no attribute X` errors. `make setup` is the canonical bootstrap.

## Common commands

```
make setup                    # install deps + workspace members (run on a fresh checkout)
make test-amazon              # uv run pytest platforms/amazon/tests/ -v
make build-amazon             # uv export → requirements.txt + sam build (cross-arch via Docker)
make deploy-base-dev          # base stack to dev
make deploy-amazon-dev        # amazon platform stack to dev
make deploy-base-prod         # base stack to prod (interactive confirm)
make deploy-amazon-prod       # amazon platform stack to prod (interactive confirm)
```

For local Lambda invocation:
```
sam local invoke <FunctionName> --event events/<event>.json
```

## CI

Every pull request to `main` runs three jobs in `.github/workflows/ci.yml`:

- **Test** — `uv run pytest`
- **Lint** — `ruff check` + `ruff format --check`
- **Validate** — `sam validate --lint` on `infrastructure/base-stack.yaml` and each platform's `template.yaml`

`main` is protected: PRs only, squash-merge, all three checks must pass.

## Repository layout

- [`infrastructure/`](infrastructure/) — SAM template + IaC for cross-cutting AWS resources (S3, DynamoDB, SES, IAM patterns); platform stacks import its exports. AWS Org / SCP / Identity Center setup notes live in [`infrastructure/org-setup/`](infrastructure/org-setup/).
- [`platforms/amazon/`](platforms/amazon/) — Amazon SP-API integration. The other platform directories are placeholders.
- `shared/` — cross-platform utilities and normalized data models (`Order`, `OrderLine`, `Product`, `Inventory`, `Report`).
- [`docs/`](docs/) — `architecture/` (mermaid diagrams), `design/` (workstream design docs), `handoffs/` (mid-task notes).

## Where to start

- Architecture overview: [`docs/architecture/00-overview.md`](docs/architecture/00-overview.md)
- Amazon runtime: [`docs/architecture/02-amazon-runtime.md`](docs/architecture/02-amazon-runtime.md) and [`platforms/amazon/CLAUDE.md`](platforms/amazon/CLAUDE.md)
- Active workstreams: [`docs/design/`](docs/design/)
