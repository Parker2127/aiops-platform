output "cluster_name" {
  value = module.eks.cluster_name
}

output "region" {
  value = var.region
}

output "oidc_provider_url" {
  value = module.eks.oidc_provider_url
}

output "oidc_provider_arn" {
  value = module.eks.oidc_provider_arn
}

output "node_role_arn" {
  value = module.eks.node_role_arn
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "ecr_repository_urls" {
  value = { for k, r in aws_ecr_repository.apps : k => r.repository_url }
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}
