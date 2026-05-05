.PHONY: setup deploy-base-dev deploy-base-prod \
        build-amazon deploy-amazon-dev deploy-amazon-prod test-amazon

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
		--capabilities CAPABILITY_IAM \
		--resolve-s3

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
		--capabilities CAPABILITY_IAM \
		--resolve-s3

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
		--parameter-overrides Environment=prod BaseStackName=sincerelyhers-base-prod \
		--capabilities CAPABILITY_NAMED_IAM \
		--resolve-s3

test-amazon:
	uv run pytest platforms/amazon/tests/ -v
