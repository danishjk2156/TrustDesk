resource "aws_db_parameter_group" "main" {
  name   = "acmecloud-postgres16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "main" {
  identifier     = "acmecloud-prod"
  engine         = "postgres"
  engine_version = "16.4"
  instance_class = "db.r6g.large"

  allocated_storage = 200
  storage_encrypted = true
  kms_key_id        = aws_kms_key.data.arn

  backup_retention_period = 35
  backup_window           = "02:00-03:00"
  multi_az                = true
  deletion_protection     = true
  publicly_accessible     = false

  db_subnet_group_name   = aws_db_subnet_group.private.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.main.name
}
