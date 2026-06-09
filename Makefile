.PHONY: setup deploy-base-dev deploy-base-prod \
        build-amazon deploy-amazon-dev deploy-amazon-prod test-amazon \
        deploy-amazon-secrets-prod \
        build-rotation \
        deploy-rotation-services-dev deploy-rotation-bobnathan-dev \
        deploy-rotation-services-prod

# ---- Bootstrap ----
setup:
	uv sync --frozen --all-packages

# ---- Base stack (cross-platform shared resources) ----
deploy-base-dev:
	sam deploy --template infrastructure/base-stack.yaml \
		--stack-name sincerelyhers-base-dev \
		--profile sincerelyhers-dev \
		--region us-east-2 \
		--parameter-overrides Environment=dev \
		--capabilities CAPABILITY_IAM

deploy-base-prod:
	@test -n "$$PROD_ACCOUNT_ID" || (echo "ERROR: PROD_ACCOUNT_ID not set. 'source .identifiers.local' (or export it manually) and retry." && exit 1)
	@echo ""
	@echo "WARNING: About to deploy BASE stack to PROD (account $$PROD_ACCOUNT_ID)."
	@echo "  Stack:   sincerelyhers-base-prod"
	@echo "  Profile: sincerelyhers-prod"
	@echo "  Role:    arn:aws:iam::$$PROD_ACCOUNT_ID:role/DeploymentRole"
	@echo ""
	@bash -c 'read -p "Type '\''deploy prod'\'' to continue: " confirm && [ "$$confirm" = "deploy prod" ] || (echo "Aborted." && exit 1)'
	sam deploy --template infrastructure/base-stack.yaml \
		--stack-name sincerelyhers-base-prod \
		--profile sincerelyhers-prod \
		--role-arn arn:aws:iam::$$PROD_ACCOUNT_ID:role/DeploymentRole \
		--region us-east-2 \
		--parameter-overrides Environment=prod \
		--capabilities CAPABILITY_IAM

# ---- Amazon platform stack ----
build-amazon:
	uv export --frozen --no-dev --no-emit-workspace --package sincerelyhers-amazon \
		--output-file platforms/amazon/src/requirements.txt
	sam build --template platforms/amazon/template.yaml --use-container

deploy-amazon-dev: build-amazon
	sam deploy --template .aws-sam/build/template.yaml \
		--stack-name sincerelyhers-amazon-dev \
		--profile sincerelyhers-dev \
		--region us-east-2 \
		--parameter-overrides Environment=dev BaseStackName=sincerelyhers-base-dev \
		--capabilities CAPABILITY_NAMED_IAM \
		--resolve-s3

deploy-amazon-prod: build-amazon
	@test -n "$$PROD_ACCOUNT_ID" || (echo "ERROR: PROD_ACCOUNT_ID not set. 'source .identifiers.local' (or export it manually) and retry." && exit 1)
	@for a in kk llg co; do test -f secrets/amazon-feed-$$a.json || (echo "ERROR: secrets/amazon-feed-$$a.json missing. Prod webhook secrets are CloudFormation-managed and re-supplied on every prod deploy; stage all three files before deploying." && exit 1); done
	@echo ""
	@echo "WARNING: About to deploy to PROD (account $$PROD_ACCOUNT_ID)."
	@echo "  Stack:   sincerelyhers-amazon-prod"
	@echo "  Profile: sincerelyhers-prod"
	@echo "  Role:    arn:aws:iam::$$PROD_ACCOUNT_ID:role/DeploymentRole"
	@echo ""
	@bash -c 'read -p "Type '\''deploy prod'\'' to continue: " confirm && [ "$$confirm" = "deploy prod" ] || (echo "Aborted." && exit 1)'
	sam deploy --template .aws-sam/build/template.yaml \
		--stack-name sincerelyhers-amazon-prod \
		--profile sincerelyhers-prod \
		--role-arn arn:aws:iam::$$PROD_ACCOUNT_ID:role/DeploymentRole \
		--region us-east-2 \
		--parameter-overrides \
			Environment=prod BaseStackName=sincerelyhers-base-prod \
			"KKWebhookJson=$$(tr -d '\n\r' < secrets/amazon-feed-kk.json)" \
			"LLGWebhookJson=$$(tr -d '\n\r' < secrets/amazon-feed-llg.json)" \
			"COWebhookJson=$$(tr -d '\n\r' < secrets/amazon-feed-co.json)" \
		--capabilities CAPABILITY_NAMED_IAM \
		--s3-bucket sincerelyhers-deploy-artifacts-prod \
		--s3-prefix amazon

# Prod SP-API credential bootstrap (app + per-seller refresh tokens).
# No build step — pure CloudFormation. Deployed rarely; see
# docs/handoffs/amazon-prod-cutover.md. Dev creds are CLI-created, so
# there is intentionally no -dev counterpart.
deploy-amazon-secrets-prod:
	@test -n "$$PROD_ACCOUNT_ID" || (echo "ERROR: PROD_ACCOUNT_ID not set. 'source .identifiers.local' (or export it manually) and retry." && exit 1)
	@test -f secrets/app-credentials.json || (echo "ERROR: secrets/app-credentials.json missing — stage {client_id, client_secret} before deploying." && exit 1)
	@for a in kk llg co; do test -f secrets/credentials-$$a.json || (echo "ERROR: secrets/credentials-$$a.json missing — stage {refresh_token} before deploying." && exit 1); done
	@echo ""
	@echo "WARNING: About to deploy SP-API credential secrets to PROD (account $$PROD_ACCOUNT_ID)."
	@echo "  Stack:   sincerelyhers-amazon-secrets-prod"
	@echo "  Role:    arn:aws:iam::$$PROD_ACCOUNT_ID:role/DeploymentRole"
	@echo ""
	@echo "Deployed rarely (bootstrap / re-key / seller re-auth). Once the prod"
	@echo "  rotation pipeline is live, do NOT redeploy without the CURRENT"
	@echo "  app/credentials value or it will clobber the rotated secret."
	@echo ""
	@bash -c 'read -p "Type '\''deploy prod'\'' to continue: " confirm && [ "$$confirm" = "deploy prod" ] || (echo "Aborted." && exit 1)'
	sam deploy --template platforms/amazon/secrets-template.yaml \
		--stack-name sincerelyhers-amazon-secrets-prod \
		--profile sincerelyhers-prod \
		--role-arn arn:aws:iam::$$PROD_ACCOUNT_ID:role/DeploymentRole \
		--region us-east-2 \
		--parameter-overrides \
			SecretsPrefix=sp-api/sincerely-services \
			"AppCredentialsJson=$$(tr -d '\n\r' < secrets/app-credentials.json)" \
			"KKCredentialsJson=$$(tr -d '\n\r' < secrets/credentials-kk.json)" \
			"LLGCredentialsJson=$$(tr -d '\n\r' < secrets/credentials-llg.json)" \
			"COCredentialsJson=$$(tr -d '\n\r' < secrets/credentials-co.json)"

test-amazon:
	uv run pytest platforms/amazon/tests/ -v

# ---- Amazon rotation pipeline (per-app stack, deployed once per SPP app) ----
# Build artifacts go to .aws-sam/rotation-build to coexist with the main
# amazon stack's .aws-sam/build (sam defaults to /build, so we override).
build-rotation:
	uv export --frozen --no-dev --no-emit-workspace --package sincerelyhers-amazon \
		--output-file platforms/amazon/src/requirements.txt
	sam build --template platforms/amazon/rotation-template.yaml \
		--build-dir .aws-sam/rotation-build --use-container

deploy-rotation-services-dev: build-rotation
	sam deploy --template .aws-sam/rotation-build/template.yaml \
		--stack-name sincerelyhers-amazon-rotation-services-dev \
		--profile sincerelyhers-dev \
		--region us-east-2 \
		--parameter-overrides Environment=dev AppShortName=services SecretsPrefix=sp-api/sincerely-services BaseStackName=sincerelyhers-base-dev \
		--capabilities CAPABILITY_NAMED_IAM \
		--resolve-s3

deploy-rotation-bobnathan-dev: build-rotation
	sam deploy --template .aws-sam/rotation-build/template.yaml \
		--stack-name sincerelyhers-amazon-rotation-bobnathan-dev \
		--profile sincerelyhers-dev \
		--region us-east-2 \
		--parameter-overrides Environment=dev AppShortName=bobnathan SecretsPrefix=sp-api/bobnathan-test BaseStackName=sincerelyhers-base-dev \
		--capabilities CAPABILITY_NAMED_IAM \
		--resolve-s3

deploy-rotation-services-prod: build-rotation
	@test -n "$$PROD_ACCOUNT_ID" || (echo "ERROR: PROD_ACCOUNT_ID not set. 'source .identifiers.local' (or export it manually) and retry." && exit 1)
	@echo ""
	@echo "WARNING: About to deploy ROTATION pipeline (Sincerely Services) to PROD (account $$PROD_ACCOUNT_ID)."
	@echo "  Stack:   sincerelyhers-amazon-rotation-services-prod"
	@echo "  Role:    arn:aws:iam::$$PROD_ACCOUNT_ID:role/DeploymentRole"
	@echo ""
	@echo "Precondition: ProtectProductionSecrets SCP must be amended (D1) to allow"
	@echo "  PutSecretValue on app/credentials by CredentialRotationProcessorRole."
	@echo "  This stack will deploy regardless, but the processor Lambda will fail"
	@echo "  on PutSecretValue at runtime until the SCP carve-out lands."
	@echo ""
	@bash -c 'read -p "Type '\''deploy prod'\'' to continue: " confirm && [ "$$confirm" = "deploy prod" ] || (echo "Aborted." && exit 1)'
	sam deploy --template .aws-sam/rotation-build/template.yaml \
		--stack-name sincerelyhers-amazon-rotation-services-prod \
		--profile sincerelyhers-prod \
		--role-arn arn:aws:iam::$$PROD_ACCOUNT_ID:role/DeploymentRole \
		--region us-east-2 \
		--parameter-overrides Environment=prod AppShortName=services SecretsPrefix=sp-api/sincerely-services BaseStackName=sincerelyhers-base-prod \
		--capabilities CAPABILITY_NAMED_IAM \
		--s3-bucket sincerelyhers-deploy-artifacts-prod \
		--s3-prefix rotation-services
