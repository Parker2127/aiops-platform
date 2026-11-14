variable "cluster_name" {
  type = string
}

variable "cluster_version" {
  description = "Kubernetes version. Must be in EKS standard support."
  type        = string
  default     = "1.36"
}

variable "subnet_ids" {
  description = "Private subnets for the control plane ENIs and worker nodes"
  type        = list(string)
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the public API endpoint"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "cluster_log_types" {
  description = "Control plane log types to ship to CloudWatch (empty = off, saves cost)"
  type        = list(string)
  default     = []
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t3.medium"]
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 4
}
