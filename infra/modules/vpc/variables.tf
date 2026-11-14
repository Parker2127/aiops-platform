variable "name" {
  description = "Name prefix for VPC resources"
  type        = string
}

variable "region" {
  description = "AWS region (used for VPC endpoint service names)"
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name, used for subnet discovery tags"
  type        = string
}

variable "cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of AZs to spread subnets across"
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 4
    error_message = "EKS needs at least 2 AZs; this layout supports up to 4."
  }
}

variable "enable_interface_endpoints" {
  description = "Create ECR/STS/Logs interface endpoints (~$7/month each per AZ)"
  type        = bool
  default     = false
}
