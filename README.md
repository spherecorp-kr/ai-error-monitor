# AI Error Monitor

AI-powered error monitoring service that automatically:
1. Collects ERROR/CRITICAL logs from CloudWatch Logs (daily)
2. Classifies errors using GPT-5 Nano
3. Analyzes root cause in codebase using Codex-Mini
4. Creates GitHub Issues with analysis and suggested fixes

## Architecture

```
CloudWatch Logs → EventBridge (daily cron)
                      ↓
              Lambda: collector
              (query + fingerprint + dedup)
                      ↓
                  SQS FIFO Queue
                      ↓
              Lambda: analyzer
              (GPT-5 Nano classify → Codex-Mini analyze)
                      ↓
              GitHub Issues (auto-created)
```

## Quick Start

### Prerequisites
- AWS CLI configured
- Terraform >= 1.5
- Python 3.12
- OpenAI API key
- GitHub personal access token (repo scope)

### Local Development
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Set env vars for local testing
export OPENAI_API_KEY="sk-..."
export GITHUB_TOKEN="ghp_..."

# Run tests
pytest tests/
```

### Deploy
```bash
# Build Lambda packages
chmod +x scripts/build.sh
./scripts/build.sh

# Deploy with Terraform
cd terraform
terraform init -backend-config=environments/dev-backend.hcl
terraform plan -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars
```

### Manual Test
```bash
aws lambda invoke \
  --function-name ai-error-monitor-dev-collector \
  --payload '{}' \
  /tmp/response.json
cat /tmp/response.json
```

## Configuration

### `config/targets.yaml`
Define monitoring targets (projects/services). Supports multiple projects with different infra types:
- `eks` — Kubernetes (EKS + Fluent Bit → CloudWatch)
- `ec2-docker` — EC2 + Docker (awslogs driver → CloudWatch)
- `tomcat` — Traditional Tomcat (CloudWatch Agent → CloudWatch)
- `cloudwatch` — Direct CloudWatch log groups

### `config/rules.yaml`
Filtering rules: ignore patterns, severity thresholds, rate limits.

## Cost Estimate
~$6-14/month (100 errors/day): Lambda free tier + CloudWatch Insights ~$3 + OpenAI ~$5-10

## Extending to New Projects
Add a new entry to `config/targets.yaml` with the project's log groups and GitHub repo.
