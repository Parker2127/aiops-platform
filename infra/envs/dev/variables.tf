variable "region" {
  type    = string
  default = "us-east-1"
}

variable "cluster_version" {
  description = "EKS version — keep in standard support"
  type        = string
  default     = "1.36"
}

variable "api_public_access_cidrs" {
  description = "Lock this down to your IP (x.x.x.x/32) in terraform.tfvars"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "enable_interface_endpoints" {
  description = "ECR/STS/Logs interface endpoints; off by default to save ~$58/month"
  type        = bool
  default     = false
}

variable "app_names" {
  type    = list(string)
  default = ["demo-app", "log-detector", "keda-scaler", "finops-optimizer"]
}
