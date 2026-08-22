# AWS deployment

This module provisions:

- an encrypted SQS work queue and explicit DLQ;
- a point-in-time-recoverable DynamoDB single table for checkpoints, audit events, approvals, and idempotency results;
- ECR and separate Fargate API/worker task definitions and services;
- an Application Load Balancer and task security groups;
- Secrets Manager entries for `OPENAI_API_KEY` and `ADMIN_API_TOKEN`;
- separate API, worker, and execution roles with resource-scoped policies;
- CloudWatch log groups, alarms, Container Insights, and the runtime dashboard.

## Prerequisites

- Terraform 1.8+
- authenticated AWS CLI/session
- a VPC and at least two subnets
- exact ECS service and CloudWatch Logs ARNs that the operations agent is allowed to access

Copy `terraform.tfvars.example` to `terraform.tfvars` and replace every placeholder. Keep the tfvars file untracked if it contains environment-specific identifiers.

## Safe two-phase bootstrap

ECS desired counts default to zero. This allows infrastructure and empty secret containers to be created before any task attempts to start.

```powershell
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan -out milestone2.tfplan
terraform apply milestone2.tfplan
```

Build and push the application image using an immutable Git commit tag:

```powershell
$repository = terraform output -raw ecr_repository_url
$account = aws sts get-caller-identity --query Account --output text
aws ecr get-login-password | docker login --username AWS --password-stdin "$account.dkr.ecr.us-east-1.amazonaws.com"
docker build -t "${repository}:<git-sha>" ../..
docker push "${repository}:<git-sha>"
```

Publish values from the ignored `.env.local` without putting secret plaintext in Terraform state or command arguments:

```powershell
..\..\.venv\Scripts\python.exe ..\..\scripts\publish_aws_secrets.py `
  --openai-secret-id (terraform output -raw openai_secret_arn) `
  --admin-secret-id (terraform output -raw admin_secret_arn) `
  --region us-east-1
```

Then start redundant API and worker tasks:

```powershell
terraform apply `
  -var="container_image=$repository`:<git-sha>" `
  -var="api_desired_count=2" `
  -var="worker_desired_count=2"
```

## Verification

```powershell
$api = terraform output -raw api_url
Invoke-RestMethod "$api/health"
aws sqs get-queue-attributes --queue-url (terraform output -raw task_queue_url) --attribute-names All
aws cloudwatch get-dashboard --dashboard-name agent-resilience-dev-runtime
```

The HTTP listener is intentionally simple for a portfolio/dev environment. Add an ACM certificate and HTTPS listener, use private Fargate subnets, and add NAT or service VPC endpoints before production use.
