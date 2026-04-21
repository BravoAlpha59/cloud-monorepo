.PHONY: build-amazon deploy-amazon-dev deploy-amazon-prod test-amazon

build-amazon:
	uv export --frozen --no-dev --package sincerelyhers-amazon \
		--output-file platforms/amazon/src/requirements.txt
	sam build --template platforms/amazon/template.yaml

deploy-amazon-dev: build-amazon
	sam deploy --template platforms/amazon/template.yaml \
		--stack-name sincerelyhers-amazon-dev \
		--profile sincerelyhers-dev \
		--region us-east-2 \
		--parameter-overrides Environment=dev \
		--capabilities CAPABILITY_NAMED_IAM \
		--resolve-s3

deploy-amazon-prod: build-amazon
	@echo ""
	@echo "WARNING: About to deploy to PROD (account 637445353164)."
	@echo "  Stack:   sincerelyhers-amazon-prod"
	@echo "  Profile: sincerelyhers-prod"
	@echo "  Role:    arn:aws:iam::637445353164:role/DeploymentRole"
	@echo ""
	@bash -c 'read -p "Type '\''deploy prod'\'' to continue: " confirm && [ "$$confirm" = "deploy prod" ] || (echo "Aborted." && exit 1)'
	sam deploy --template platforms/amazon/template.yaml \
		--stack-name sincerelyhers-amazon-prod \
		--profile sincerelyhers-prod \
		--role-arn arn:aws:iam::637445353164:role/DeploymentRole \
		--region us-east-2 \
		--parameter-overrides Environment=prod \
		--capabilities CAPABILITY_NAMED_IAM \
		--resolve-s3

test-amazon:
	uv run pytest platforms/amazon/tests/ -v
