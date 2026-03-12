environment               = "dev"
aws_region                = "ap-northeast-2"
openai_api_key_secret_arn = "arn:aws:secretsmanager:ap-northeast-2:665321880316:secret:ai-error-monitor/openai-api-key"
github_token_secret_arn   = "arn:aws:secretsmanager:ap-northeast-2:665321880316:secret:ai-error-monitor/github-token"
schedule_expression       = "cron(0 15 * * ? *)"  # 00:00 KST
log_query_hours           = 24
