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
