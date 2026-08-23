data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api_execution" {
  name               = "${local.prefix}-api-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "api_execution" {
  role       = aws_iam_role.api_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "worker_execution" {
  name               = "${local.prefix}-worker-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "worker_execution" {
  role       = aws_iam_role.worker_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "api_execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.openai.arn,
      aws_secretsmanager_secret.admin.arn,
      aws_secretsmanager_secret.viewer.arn,
    ]
  }
}

resource "aws_iam_role_policy" "api_execution_secrets" {
  name   = "secrets"
  role   = aws_iam_role.api_execution.id
  policy = data.aws_iam_policy_document.api_execution_secrets.json
}

data "aws_iam_policy_document" "worker_execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.openai.arn]
  }
}

resource "aws_iam_role_policy" "worker_execution_secrets" {
  name   = "secrets"
  role   = aws_iam_role.worker_execution.id
  policy = data.aws_iam_policy_document.worker_execution_secrets.json
}

resource "aws_iam_role" "api" {
  name               = "${local.prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "api" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.workflows.arn, "${aws_dynamodb_table.workflows.arn}/index/*"]
  }
  statement {
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.tasks.arn, aws_sqs_queue.dead_letter.arn]
  }
  statement {
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage"]
    resources = [aws_sqs_queue.dead_letter.arn]
  }
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["AgentResilience"]
    }
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "runtime"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_iam_role" "worker" {
  name               = "${local.prefix}-worker"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "worker" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.workflows.arn]
  }
  statement {
    actions = [
      "sqs:ChangeMessageVisibility", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
      "sqs:ReceiveMessage", "sqs:SendMessage"
    ]
    resources = [aws_sqs_queue.tasks.arn, aws_sqs_queue.dead_letter.arn]
  }
  statement {
    actions   = ["logs:FilterLogEvents"]
    resources = var.operations_log_group_arns
  }
  statement {
    actions   = ["cloudwatch:DescribeAlarms", "cloudwatch:GetMetricStatistics"]
    resources = ["*"]
  }
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["AgentResilience"]
    }
  }
  statement {
    actions   = ["ecs:UpdateService"]
    resources = var.operations_service_arns
  }
  statement {
    actions   = ["ecs:DescribeServices"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "runtime-and-tools"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}
