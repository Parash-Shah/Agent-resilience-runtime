locals {
  container_image = var.container_image != "" ? var.container_image : "${aws_ecr_repository.runtime.repository_url}:latest"
  common_environment = [
    { name = "RUNTIME_BACKEND", value = "aws" },
    { name = "TOOL_BACKEND", value = "aws" },
    { name = "AGENT_MODE", value = "live" },
    { name = "AWS_REGION", value = var.aws_region },
    { name = "DYNAMODB_TABLE_NAME", value = aws_dynamodb_table.workflows.name },
    { name = "SQS_QUEUE_URL", value = aws_sqs_queue.tasks.url },
    { name = "SQS_DLQ_URL", value = aws_sqs_queue.dead_letter.url },
    { name = "ECS_CLUSTER", value = var.operations_ecs_cluster_name },
    { name = "CLOUDWATCH_LOG_GROUP_PREFIX", value = var.operations_log_group_prefix },
    { name = "CLOUDWATCH_METRICS_ENABLED", value = "true" },
    { name = "CLOUDWATCH_METRIC_NAMESPACE", value = "AgentResilience" },
    { name = "RUN_WORKER", value = "false" },
  ]
  api_secrets = [
    { name = "OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.openai.arn },
    { name = "ADMIN_API_TOKEN", valueFrom = aws_secretsmanager_secret.admin.arn },
    { name = "VIEWER_API_TOKEN", valueFrom = aws_secretsmanager_secret.viewer.arn },
  ]
  worker_secrets = [
    { name = "OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.openai.arn },
  ]
}

resource "aws_ecr_repository" "runtime" {
  name                 = local.prefix
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "runtime" {
  repository = aws_ecr_repository.runtime.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain the newest 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_secretsmanager_secret" "openai" {
  name                    = "${var.openai_secret_name}/${var.environment}"
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_secretsmanager_secret" "admin" {
  name                    = "${var.admin_secret_name}/${var.environment}"
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_secretsmanager_secret" "viewer" {
  name                    = "${var.viewer_secret_name}/${var.environment}"
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_ecs_cluster" "runtime" {
  name = local.prefix
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  tags = local.tags
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.api_execution.arn
  task_role_arn            = aws_iam_role.api.arn

  container_definitions = jsonencode([{
    name         = "api"
    image        = local.container_image
    command      = ["agent-resilience-api"]
    essential    = true
    environment  = local.common_environment
    secrets      = local.api_secrets
    portMappings = [{ containerPort = 8000, hostPort = 8000, protocol = "tcp" }]
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)\""]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  }])
  tags = local.tags
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.worker_execution.arn
  task_role_arn            = aws_iam_role.worker.arn

  container_definitions = jsonencode([{
    name        = "worker"
    image       = local.container_image
    command     = ["agent-resilience-worker"]
    essential   = true
    environment = local.common_environment
    secrets     = local.worker_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "worker"
      }
    }
  }])
  tags = local.tags
}

resource "aws_ecs_service" "api" {
  name            = "${local.prefix}-api"
  cluster         = aws_ecs_cluster.runtime.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  depends_on = [aws_lb_listener.api, aws_iam_role_policy.api_execution_secrets]
  tags       = local.tags
}

resource "aws_ecs_service" "worker" {
  name            = "${local.prefix}-worker"
  cluster         = aws_ecs_cluster.runtime.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }
  depends_on = [aws_iam_role_policy.worker_execution_secrets]
  tags       = local.tags
}
