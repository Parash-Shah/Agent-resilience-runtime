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
  value = "${var.certificate_arn != "" ? "https" : "http"}://${aws_lb.api.dns_name}"
}

output "api_service_name" {
  value = aws_ecs_service.api.name
}

output "worker_service_name" {
  value = aws_ecs_service.worker.name
}

output "demo_service_name" {
  value = aws_ecs_service.demo.name
}

output "api_task_family" {
  value = aws_ecs_task_definition.api.family
}

output "worker_task_family" {
  value = aws_ecs_task_definition.worker.family
}

output "demo_task_family" {
  value = aws_ecs_task_definition.demo.family
}

output "github_deploy_role_arn" {
  value = try(aws_iam_role.github_deploy[0].arn, null)
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

output "viewer_secret_arn" {
  value = aws_secretsmanager_secret.viewer.arn
}
