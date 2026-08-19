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
