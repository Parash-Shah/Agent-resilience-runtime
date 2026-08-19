# AWS reliability primitives

This module provisions the production storage and messaging primitives: an encrypted SQS queue with a DLQ, a point-in-time-recoverable DynamoDB workflow table, and CloudWatch logging/alarming. The runnable local adapter uses SQLite behind the same checkpoint/queue boundaries; an AWS store adapter is the deployment seam.

```powershell
terraform init
terraform plan -var="environment=dev"
terraform apply -var="environment=dev"
```

Terraform does not create or store an OpenAI key. Inject `OPENAI_API_KEY` at deployment time from your chosen secret manager.
