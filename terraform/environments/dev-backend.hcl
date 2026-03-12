bucket         = "ai-error-monitor-terraform-state"
key            = "dev/terraform.tfstate"
region         = "ap-northeast-2"
dynamodb_table = "ai-error-monitor-terraform-lock"
encrypt        = true
