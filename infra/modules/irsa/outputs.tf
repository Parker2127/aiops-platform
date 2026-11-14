output "role_arn" {
  description = "Put this in the service account's eks.amazonaws.com/role-arn annotation"
  value       = aws_iam_role.this.arn
}

output "role_name" {
  value = aws_iam_role.this.name
}
