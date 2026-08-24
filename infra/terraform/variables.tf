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
  default     = 10
}

variable "vpc_id" {
  description = "VPC hosting the ECS services and load balancer."
  type        = string
}

variable "subnet_ids" {
  description = "At least two subnets for the public ALB."
  type        = list(string)
  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "Provide at least two subnet IDs in different availability zones."
  }
}

variable "task_subnet_ids" {
  description = "Private Fargate subnets. Empty reuses subnet_ids for a simpler development deployment."
  type        = list(string)
  default     = []
  validation {
    condition     = length(var.task_subnet_ids) == 0 || length(var.task_subnet_ids) >= 2
    error_message = "Provide either no task_subnet_ids or at least two private subnets."
  }
}

variable "assign_public_ip" {
  description = "Assign public IPs to tasks. Disable when task_subnet_ids have NAT or the required VPC endpoints."
  type        = bool
  default     = true
}

variable "api_ingress_cidrs" {
  description = "CIDRs permitted to reach the public API load balancer."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "certificate_arn" {
  description = "Optional ACM certificate ARN. When set, HTTP redirects to the HTTPS listener."
  type        = string
  default     = ""
}

variable "enable_waf" {
  description = "Attach an AWS WAF rate-based web ACL to the API ALB."
  type        = bool
  default     = false
}

variable "waf_rate_limit" {
  description = "Maximum requests per five-minute WAF evaluation window per source IP."
  type        = number
  default     = 1000
  validation {
    condition     = var.waf_rate_limit >= 100
    error_message = "waf_rate_limit must be at least 100."
  }
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

variable "demo_desired_count" {
  description = "Number of controlled checkout demo tasks. Set to zero when operating only on an external service."
  type        = number
  default     = 0
}

variable "demo_fail_after_seconds" {
  description = "Seconds after demo process start before it emits elevated errors; a forced deployment resets it."
  type        = number
  default     = 180
}

variable "chaos_pause_tool" {
  description = "Optional tool name at which workers pause, providing a deterministic worker-termination window."
  type        = string
  default     = ""
}

variable "chaos_pause_after_steps" {
  description = "Optional completed-step count at which the next tool pauses, independent of agent-selected tool order."
  type        = number
  default     = 0
}

variable "chaos_pause_seconds" {
  description = "Length of the opt-in chaos pause. Keep zero outside an intentional recovery demonstration."
  type        = number
  default     = 0
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
  default     = ""
}

variable "operations_service_arns" {
  description = "Exact ECS service ARNs the worker may redeploy."
  type        = list(string)
  default     = []
}

variable "operations_log_group_arns" {
  description = "Exact CloudWatch Logs group ARNs the worker may read."
  type        = list(string)
  default     = []
}

variable "operations_log_group_prefix" {
  type    = string
  default = ""
}

variable "github_repository" {
  description = "Optional owner/repository allowed to assume the image deployment role from GitHub Actions."
  type        = string
  default     = ""
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub Actions OIDC provider ARN. Required when github_repository is set."
  type        = string
  default     = ""
}
