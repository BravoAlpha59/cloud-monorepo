.PHONY: build-amazon deploy-amazon test-amazon

build-amazon:
	uv export --frozen --no-dev --package sincerelyhers-amazon \
		--output-file platforms/amazon/src/requirements.txt
	sam build --template platforms/amazon/template.yaml

deploy-amazon: build-amazon
	sam deploy --template platforms/amazon/template.yaml \
		--stack-name sincerelyhers-amazon-dev \
		--profile sincerelyhers-prod \
		--parameter-overrides Environment=dev \
		--capabilities CAPABILITY_IAM \
		--resolve-s3

test-amazon:
	uv run pytest platforms/amazon/tests/ -v
