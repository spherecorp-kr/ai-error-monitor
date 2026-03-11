output "collector_function_name" {
  value = aws_lambda_function.collector.function_name
}

output "analyzer_function_name" {
  value = aws_lambda_function.analyzer.function_name
}

output "sqs_queue_url" {
  value = aws_sqs_queue.errors.url
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.errors.name
}

output "schedule_arn" {
  value = aws_scheduler_schedule.daily_collect.arn
}
