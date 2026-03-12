environment               = "prod"
aws_region                = "ap-southeast-7"
openai_api_key_secret_arn = "arn:aws:secretsmanager:ap-southeast-7:665321880316:secret:ai-error-monitor/openai-api-key"
github_token_secret_arn   = "arn:aws:secretsmanager:ap-southeast-7:665321880316:secret:ai-error-monitor/github-token"
schedule_expression       = "cron(0 0 * * ? *)"   # 00:00 KST (Asia/Seoul timezone)
log_query_hours           = 24

# GitHub App (ai-error-monitor[bot])
github_app_id                = "3071498"
github_app_installation_id   = "115770915"
github_app_private_key_arn   = "arn:aws:secretsmanager:ap-southeast-7:665321880316:secret:ai-error-monitor/github-app-private-key"

# PROD VPC - Lambda needs access to Loki inside EKS
vpc_subnet_ids         = ["subnet-0dfe2d53f4557aa9b", "subnet-09f19171ef8c3c679"]
vpc_security_group_ids = ["sg-05a50de4106d37909"]
