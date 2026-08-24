# AWS deployment runbook

This module provisions the complete AgentResilience v1.0 runtime:

- encrypted SQS work queue and DLQ;
- point-in-time-recoverable DynamoDB checkpoints, audit events, approvals, and idempotency ledger;
- immutable ECR repository and separate Fargate API, worker, and controlled checkout services;
- Application Load Balancer with optional ACM HTTPS redirect and optional WAF rate limiting;
- Secrets Manager containers for `OPENAI_API_KEY`, `ADMIN_API_TOKEN`, and `VIEWER_API_TOKEN`;
- separate least-authority execution/task roles and an optional GitHub Actions OIDC deployment role;
- CloudWatch logs, service evidence, alarms, Container Insights, and runtime dashboard.

## Prerequisites

- Terraform 1.8+, Docker, and an authenticated AWS CLI/session;
- a VPC and at least two ALB subnets;
- for hardened networking, two private task subnets with NAT access to the OpenAI API;
- an ACM certificate and restricted ingress CIDRs for HTTPS deployments;
- an existing GitHub OIDC provider only when enabling the deploy role.

Copy `terraform.tfvars.example` to ignored `terraform.tfvars` and replace placeholders. Leave the external operations ARN lists empty when using only the module's controlled `checkout-service`; its exact service and log resources are added to the worker policy automatically.

## Phase 1: bootstrap with no running tasks

Keep all desired counts at zero on the first apply. This creates empty secret containers and ECR without starting tasks that cannot yet authenticate.

```powershell
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan -out bootstrap.tfplan `
  -var="api_desired_count=0" `
  -var="worker_desired_count=0" `
  -var="demo_desired_count=0"
terraform apply bootstrap.tfplan
```

Build and push an immutable commit-tagged image:

```powershell
$repository = terraform output -raw ecr_repository_url
$account = aws sts get-caller-identity --query Account --output text
aws ecr get-login-password | docker login --username AWS --password-stdin "$account.dkr.ecr.us-east-1.amazonaws.com"
$sha = git rev-parse HEAD
docker build --pull -t "${repository}:$sha" ../..
docker push "${repository}:$sha"
```

Publish ignored local values without placing secret plaintext in Terraform state or command arguments:

```powershell
..\..\.venv\Scripts\python.exe ..\..\scripts\publish_aws_secrets.py `
  --openai-secret-id (terraform output -raw openai_secret_arn) `
  --admin-secret-id (terraform output -raw admin_secret_arn) `
  --viewer-secret-id (terraform output -raw viewer_secret_arn) `
  --region us-east-1
```

## Phase 2: start the runtime

Use one worker for the deterministic task-termination proof. Two or more workers are recommended after the demonstration.

```powershell
terraform apply `
  -var="container_image=$repository`:$sha" `
  -var="api_desired_count=2" `
  -var="worker_desired_count=1" `
  -var="demo_desired_count=1" `
  -var="chaos_pause_after_steps=3" `
  -var="chaos_pause_seconds=30"
```

Confirm health and wait for controlled failure evidence:

```powershell
$api = terraform output -raw api_url
Invoke-RestMethod "$api/health"
aws cloudwatch wait alarm-exists --alarm-names checkout-service-elevated-errors
aws cloudwatch describe-alarms --alarm-names checkout-service-elevated-errors
```

The checkout task starts healthy, then emits a 12.5% error rate and connection-pool exhaustion logs after `demo_fail_after_seconds`. An approved ECS forced deployment starts a fresh process, returning metrics to the healthy baseline.

## Phase 3: definitive recovery proof

Run from the repository root. `ADMIN_API_TOKEN` is loaded from the process environment or ignored `.env.local` and is never accepted on the command line.

```powershell
.venv\Scripts\python.exe scripts/verify_milestone4.py `
  --api-url (terraform -chdir=infra/terraform output -raw api_url) `
  --cluster (terraform -chdir=infra/terraform output -raw ecs_cluster_name) `
  --worker-service (terraform -chdir=infra/terraform output -raw worker_service_name)
```

The verifier fails unless all of these are observed:

1. alert, metrics, and logs are checkpointed;
2. the active worker is terminated inside the step-four chaos window;
3. a different ECS worker resumes at dependency health after SQS redelivery;
4. the production restart pauses for administrator approval;
5. ECS restart completes exactly once;
6. recovery evidence completes the workflow and the approval remains in the audit trail.

The sanitized report is written to ignored `evidence/milestone4/latest.json`. After the proof, disable fault injection and restore redundant workers:

```powershell
terraform apply `
  -var="container_image=$repository`:$sha" `
  -var="api_desired_count=2" `
  -var="worker_desired_count=2" `
  -var="demo_desired_count=1" `
  -var="chaos_pause_after_steps=0" `
  -var="chaos_pause_seconds=0"
```

## GitHub OIDC deployment

Set `github_repository` and `github_oidc_provider_arn`, apply Terraform, then configure the GitHub `production` environment variables from outputs:

- `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `ECR_REPOSITORY`, `ECS_CLUSTER`;
- `ECS_API_SERVICE`, `ECS_API_TASK_FAMILY`;
- `ECS_WORKER_SERVICE`, `ECS_WORKER_TASK_FAMILY`;
- `ECS_DEMO_SERVICE`, `ECS_DEMO_TASK_FAMILY`.

The manual `deploy-aws` workflow exchanges GitHub's OIDC token for short-lived AWS credentials, builds an immutable commit image, and deploys new ECS task revisions. No long-lived AWS access key is stored in GitHub.

## Production hardening checklist

- place tasks in private subnets, provide NAT for OpenAI API calls, and set `assign_public_ip=false`;
- set `certificate_arn`, enable WAF, and restrict `api_ingress_cidrs`;
- put an organization identity-aware proxy in front of the dashboard;
- rotate all three Secrets Manager values and force a new ECS deployment;
- send DLQ, worker-empty, API 5xx, and checkout alarms to an on-call SNS destination;
- export audit records to retention-controlled storage if compliance requires it;
- keep DynamoDB point-in-time recovery enabled and test restoration separately.
