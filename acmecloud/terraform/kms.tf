resource "aws_kms_key" "data" {
  description             = "Customer data at rest (RDS, S3)"
  key_usage               = "ENCRYPT_DECRYPT"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "data" {
  name          = "alias/acmecloud-data"
  target_key_id = aws_kms_key.data.key_id
}
