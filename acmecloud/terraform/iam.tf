resource "aws_iam_account_password_policy" "strict" {
  minimum_password_length        = 14
  require_symbols                = true
  require_numbers                = true
  require_uppercase_characters   = true
  require_lowercase_characters   = true
  password_reuse_prevention      = 24
  max_password_age               = 90
  allow_users_to_change_password = true
}

resource "aws_iam_role" "auditor" {
  name               = "acmecloud-auditor"
  assume_role_policy = data.aws_iam_policy_document.sso_assume.json
}

resource "aws_iam_role_policy_attachment" "auditor_read_only" {
  role       = aws_iam_role.auditor.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_policy" "require_mfa" {
  name   = "acmecloud-require-mfa"
  policy = file("${path.module}/../iam/mfa_policy.json")
}

resource "aws_iam_group" "admins" {
  name = "acmecloud-admins"
}

resource "aws_iam_group_policy_attachment" "admins_require_mfa" {
  group      = aws_iam_group.admins.name
  policy_arn = aws_iam_policy.require_mfa.arn
}
