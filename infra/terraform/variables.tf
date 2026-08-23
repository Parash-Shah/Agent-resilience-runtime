variable "aws_region" {
  description = "AWS region for durable runtime resources."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Resource name prefix."
  type        = string
  default     = "agent-resilience"
}

variable "environment" {
  description = "Deployment environment."
  type        = string
  default     = "dev"
}

variable "max_receive_count" {
  description = "Deliveries before SQS moves work to the DLQ."
  type        = number
  default     = 5
}

variable "vpc_id" {
  description = "VPC hosting the ECS services and load balancer."
  type        = string
}

variable "subnet_ids" {
  description = "At least two subnets for the ALB and Fargate tasks. Use private task subnets in production."
  type        = list(string)
  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "Provide at least two subnet IDs in different availability zones."
  }
}

variable "api_ingress_cidrs" {
  description = "CIDRs permitted to reach the public API load balancer."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "container_image" {
  description = "Optional immutable API/worker image URI. Empty uses the ECR repository latest tag."
  type        = string
  default     = ""
}

variable "api_desired_count" {
  type    = number
  default = 0
}

variable "worker_desired_count" {
  type    = number
  default = 0
}

variable "task_cpu" {
  type    = number
  default = 512
}

variable "task_memory" {
  type    = number
  default = 1024
}

variable "openai_secret_name" {
  type    = string
  default = "agent-resilience/openai-api-key"
}

variable "admin_secret_name" {
  type    = string
  default = "agent-resilience/admin-api-token"
}

variable "viewer_secret_name" {
  type    = string
  default = "agent-resilience/viewer-api-token"
}

variable "operations_ecs_cluster_name" {
  description = "ECS cluster containing the service the agent may inspect/redeploy."
  type        = string
  default     = "default"
}

variable "operations_service_arns" {
  description = "Exact ECS service ARNs the worker may redeploy."
  type        = list(string)
}

variable "operations_log_group_arns" {
  description = "Exact CloudWatch Logs group ARNs the worker may read."
  type        = list(string)
}

variable "operations_log_group_prefix" {
  type    = string
  default = "/ecs"
}
