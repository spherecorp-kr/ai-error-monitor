variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "ai-error-monitor"
}

variable "environment" {
  description = "Environment (dev, prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-southeast-7"
}

variable "openai_api_key_secret_arn" {
  description = "ARN of AWS Secrets Manager secret containing OpenAI API key"
  type        = string
}

variable "github_token_secret_arn" {
  description = "ARN of AWS Secrets Manager secret containing GitHub token"
  type        = string
}

variable "schedule_expression" {
  description = "EventBridge schedule expression (default: daily midnight KST = 15:00 UTC)"
  type        = string
  default     = "cron(0 15 * * ? *)"
}

variable "log_query_hours" {
  description = "How many hours back to query logs"
  type        = number
  default     = 24
}

variable "duplicate_ttl_hours" {
  description = "TTL for deduplication fingerprints"
  type        = number
  default     = 72
}

variable "github_app_id" {
  description = "GitHub App ID"
  type        = string
  default     = ""
}

variable "github_app_installation_id" {
  description = "GitHub App Installation ID"
  type        = string
  default     = ""
}

variable "github_app_private_key_arn" {
  description = "ARN of Secrets Manager secret containing GitHub App private key"
  type        = string
  default     = ""
}

variable "vpc_subnet_ids" {
  description = "Private subnet IDs for Lambda VPC access (required for Loki queries)"
  type        = list(string)
  default     = []
}

variable "vpc_security_group_ids" {
  description = "Security group IDs for Lambda VPC access"
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional tags"
  type        = map(string)
  default     = {}
}
