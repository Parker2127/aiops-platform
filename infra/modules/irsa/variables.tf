variable "name" {
  description = "IAM role name"
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace of the service account"
  type        = string
}

variable "service_account" {
  description = "Kubernetes service account name"
  type        = string
}

variable "oidc_provider_arn" {
  type = string
}

variable "oidc_provider_url" {
  description = "OIDC issuer without the https:// prefix"
  type        = string
}

variable "policy_arns" {
  description = "Managed policy ARNs to attach"
  type        = list(string)
  default     = []
}

variable "policy_json" {
  description = "Optional inline policy document (JSON)"
  type        = string
  default     = null
}
