terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    # Configure per environment in backend.hcl
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  prefix = "${var.project_name}-${var.environment}"
  tags = merge(var.tags, {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  })
}

# ──────────────────────────────────────────────
# DynamoDB - Error fingerprint & analysis store
# ──────────────────────────────────────────────
resource "aws_dynamodb_table" "errors" {
  name         = "${local.prefix}-errors"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "fingerprint"

  attribute {
    name = "fingerprint"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = local.tags
}

# ──────────────────────────────────────────────
# SQS - Collector → Analyzer queue
# ──────────────────────────────────────────────
resource "aws_sqs_queue" "errors" {
  name                       = "${local.prefix}-errors.fifo"
  fifo_queue                 = true
  content_based_deduplication = true
  visibility_timeout_seconds = 900  # 15 min (Lambda max)
  message_retention_seconds  = 86400  # 1 day
  receive_wait_time_seconds  = 5

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.errors_dlq.arn
    maxReceiveCount     = 3
  })

  tags = local.tags
}

resource "aws_sqs_queue" "errors_dlq" {
  name                      = "${local.prefix}-errors-dlq.fifo"
  fifo_queue                = true
  message_retention_seconds = 604800  # 7 days

  tags = local.tags
}

# ──────────────────────────────────────────────
# IAM - Lambda execution role
# ──────────────────────────────────────────────
resource "aws_iam_role" "lambda" {
  name = "${local.prefix}-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "lambda" {
  name = "${local.prefix}-lambda-policy"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:StartQuery",
          "logs:GetQueryResults",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:BatchGetItem",
        ]
        Resource = aws_dynamodb_table.errors.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:SendMessageBatch"]
        Resource = aws_sqs_queue.errors.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.errors.arn
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [var.openai_api_key_secret_arn, var.github_token_secret_arn]
      },
    ]
  })
}

# ──────────────────────────────────────────────
# Lambda - Collector
# ──────────────────────────────────────────────
resource "aws_lambda_function" "collector" {
  function_name = "${local.prefix}-collector"
  role          = aws_iam_role.lambda.arn
  handler       = "lambdas.collector.handler.handler"
  runtime       = "python3.12"
  timeout       = 300  # 5 min
  memory_size   = 256

  filename         = "${path.module}/../dist/collector.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/collector.zip")

  environment {
    variables = {
      AWS_REGION_OVERRIDE = var.aws_region
      SQS_QUEUE_URL       = aws_sqs_queue.errors.url
      DYNAMODB_TABLE      = aws_dynamodb_table.errors.name
      LOG_QUERY_HOURS     = var.log_query_hours
      MAX_ERRORS_PER_RUN  = 200
      DUPLICATE_TTL_HOURS = var.duplicate_ttl_hours
    }
  }

  tags = local.tags
}

# ──────────────────────────────────────────────
# Lambda - Analyzer
# ──────────────────────────────────────────────
resource "aws_lambda_function" "analyzer" {
  function_name = "${local.prefix}-analyzer"
  role          = aws_iam_role.lambda.arn
  handler       = "lambdas.analyzer.handler.handler"
  runtime       = "python3.12"
  timeout       = 900  # 15 min (max)
  memory_size   = 512

  filename         = "${path.module}/../dist/analyzer.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/analyzer.zip")

  environment {
    variables = {
      AWS_REGION_OVERRIDE  = var.aws_region
      DYNAMODB_TABLE       = aws_dynamodb_table.errors.name
      OPENAI_API_KEY_ARN   = var.openai_api_key_secret_arn
      GITHUB_TOKEN_ARN     = var.github_token_secret_arn
      CLASSIFY_MODEL       = "gpt-5-nano"
      ANALYZE_MODEL        = "codex-mini-latest"
    }
  }

  tags = local.tags
}

# SQS trigger for analyzer
resource "aws_lambda_event_source_mapping" "analyzer_sqs" {
  event_source_arn                   = aws_sqs_queue.errors.arn
  function_name                      = aws_lambda_function.analyzer.arn
  batch_size                         = 5
  maximum_batching_window_in_seconds = 60
  enabled                            = true
}

# ──────────────────────────────────────────────
# EventBridge - Daily schedule
# ──────────────────────────────────────────────
resource "aws_scheduler_schedule" "daily_collect" {
  name       = "${local.prefix}-daily-collect"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "Asia/Seoul"

  target {
    arn      = aws_lambda_function.collector.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}

resource "aws_iam_role" "scheduler" {
  name = "${local.prefix}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${local.prefix}-scheduler-invoke"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.collector.arn
    }]
  })
}

resource "aws_lambda_permission" "scheduler" {
  statement_id  = "AllowSchedulerInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.collector.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.daily_collect.arn
}
