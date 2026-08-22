output "task_queue_url" {
  value = aws_sqs_queue.tasks.url
}

output "dead_letter_queue_url" {
  value = aws_sqs_queue.dead_letter.url
}

output "workflow_table_name" {
  value = aws_dynamodb_table.workflows.name
}

output "runtime_log_group" {
  value = aws_cloudwatch_log_group.runtime.name
}

output "api_url" {
  value = "http://${aws_lb.api.dns_name}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.runtime.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.runtime.name
}

output "openai_secret_arn" {
  value = aws_secretsmanager_secret.openai.arn
}

output "admin_secret_arn" {
  value = aws_secretsmanager_secret.admin.arn
}
