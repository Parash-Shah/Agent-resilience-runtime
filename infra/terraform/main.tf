locals {
  prefix                 = "${var.name}-${var.environment}"
  task_subnets           = length(var.task_subnet_ids) > 0 ? var.task_subnet_ids : var.subnet_ids
  operations_cluster     = var.operations_ecs_cluster_name != "" ? var.operations_ecs_cluster_name : aws_ecs_cluster.runtime.name
  operations_log_prefix  = var.operations_log_group_prefix != "" ? var.operations_log_group_prefix : "/ecs/${local.prefix}"
  allowed_service_arns   = concat(var.operations_service_arns, [aws_ecs_service.demo.id])
  allowed_log_group_arns = concat(var.operations_log_group_arns, ["${aws_cloudwatch_log_group.demo.arn}:*"])
  tags = {
    Application = var.name
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

resource "aws_sqs_queue" "dead_letter" {
  name                      = "${local.prefix}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = local.tags
}

resource "aws_sqs_queue" "tasks" {
  name                       = "${local.prefix}-tasks"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = var.max_receive_count
  })
  tags = local.tags
}

resource "aws_dynamodb_table" "workflows" {
  name         = "${local.prefix}-workflows"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "entity_type"
    type = "S"
  }

  attribute {
    name = "updated_at"
    type = "S"
  }

  global_secondary_index {
    name            = "entity_type-updated_at-index"
    projection_type = "ALL"
    key_schema {
      attribute_name = "entity_type"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "updated_at"
      key_type       = "RANGE"
    }
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
  tags = local.tags
}

resource "aws_cloudwatch_log_group" "runtime" {
  name              = "/agent-resilience/${var.environment}/runtime"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.prefix}-dlq-not-empty"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Agent tasks reached the dead-letter queue."
  dimensions = {
    QueueName = aws_sqs_queue.dead_letter.name
  }
  tags = local.tags
}

check "github_oidc_configuration" {
  assert {
    condition = (
      (var.github_repository == "" && var.github_oidc_provider_arn == "") ||
      (var.github_repository != "" && var.github_oidc_provider_arn != "")
    )
    error_message = "github_repository and github_oidc_provider_arn must be set together."
  }
}
