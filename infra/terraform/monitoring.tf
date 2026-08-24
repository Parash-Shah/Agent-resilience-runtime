resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.prefix}/api"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.prefix}/worker"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "demo" {
  name              = "/ecs/${local.prefix}/checkout-service"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_cloudwatch_dashboard" "runtime" {
  dashboard_name = "${local.prefix}-runtime"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 8, height = 6
        properties = {
          title = "Workflow success rate (%)", region = var.aws_region, stat = "Sum", period = 60
          metrics = [
            ["AgentResilience", "WorkflowsCompleted", { id = "completed", visible = false }],
            [{ expression = "SUM(SEARCH('{AgentResilience} MetricName=\"WorkflowFailures\"', 'Sum', 60))", id = "failed", visible = false }],
            [{ expression = "IF((completed+failed)>0,100*completed/(completed+failed),100)", id = "success", label = "Success rate" }]
          ]
        }
      },
      {
        type = "metric", x = 8, y = 0, width = 8, height = 6
        properties = {
          title = "Retries and tool failures", region = var.aws_region, stat = "Sum", period = 60
          metrics = [
            ["AgentResilience", "QueueDeliveries", "outcome", "retry"],
            [{ expression = "SUM(SEARCH('{AgentResilience} MetricName=\"ToolFailures\"', 'Sum', 60))", label = "Tool failures" }],
            ["AgentResilience", "LoopDetections"]
          ]
        }
      },
      {
        type = "metric", x = 16, y = 0, width = 8, height = 6
        properties = {
          title   = "Decision latency", region = var.aws_region, stat = "Average", period = 60
          metrics = [["AgentResilience", "DecisionLatency"]]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title = "SQS queue depth", region = var.aws_region, stat = "Maximum", period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.tasks.name],
            [".", "ApproximateNumberOfMessagesNotVisible", ".", aws_sqs_queue.tasks.name],
            [".", "ApproximateNumberOfMessagesVisible", ".", aws_sqs_queue.dead_letter.name]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title = "ECS running tasks", region = var.aws_region, stat = "Average", period = 60
          metrics = [
            ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", aws_ecs_cluster.runtime.name, "ServiceName", aws_ecs_service.api.name],
            ["...", aws_ecs_service.worker.name]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6
        properties = {
          title = "Checkout evidence", region = var.aws_region, stat = "Average", period = 10
          metrics = [
            ["AgentResilience/Services", "ErrorRate", "ServiceName", "checkout-service", "Environment", "production"],
            [".", "Latency", ".", ".", ".", ".", { yAxis = "right" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6
        properties = {
          title = "API load balancer failures", region = var.aws_region, stat = "Sum", period = 60
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.api.arn_suffix],
            [".", "RejectedConnectionCount", ".", "."]
          ]
        }
      }
    ]
  })
}

resource "aws_cloudwatch_metric_alarm" "worker_service_empty" {
  count = var.worker_desired_count > 0 ? 1 : 0

  alarm_name          = "${local.prefix}-worker-service-empty"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "RunningTaskCount"
  namespace           = "ECS/ContainerInsights"
  period              = 60
  statistic           = "Minimum"
  threshold           = 1
  dimensions = {
    ClusterName = aws_ecs_cluster.runtime.name
    ServiceName = aws_ecs_service.worker.name
  }
  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "checkout_errors" {
  count = var.demo_desired_count > 0 ? 1 : 0

  alarm_name          = "checkout-service-elevated-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "ErrorRate"
  namespace           = "AgentResilience/Services"
  period              = 10
  statistic           = "Average"
  threshold           = 5
  alarm_description   = "Controlled checkout demo has entered its connection-pool failure mode."
  dimensions = {
    ServiceName = "checkout-service"
    Environment = "production"
  }
  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "api_target_5xx" {
  alarm_name          = "${local.prefix}-api-target-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  dimensions = {
    LoadBalancer = aws_lb.api.arn_suffix
  }
  tags = local.tags
}
