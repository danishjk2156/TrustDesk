resource "aws_cloudtrail" "main" {
  name                          = "acmecloud-audit"
  s3_bucket_name                = "acmecloud-audit-logs"
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/acmecloud/app"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.data.arn
}

resource "aws_cloudwatch_log_group" "flow" {
  name              = "/acmecloud/vpc-flow"
  retention_in_days = 365
}
