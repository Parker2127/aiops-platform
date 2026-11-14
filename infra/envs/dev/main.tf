locals {
  name = "aiops-dev"
}

module "vpc" {
  source = "../../modules/vpc"

  name                       = local.name
  region                     = var.region
  cluster_name               = local.name
  cidr                       = "10.0.0.0/16"
  az_count                   = 2
  enable_interface_endpoints = var.enable_interface_endpoints
}

module "eks" {
  source = "../../modules/eks"

  cluster_name        = local.name
  cluster_version     = var.cluster_version
  subnet_ids          = module.vpc.private_subnet_ids
  public_access_cidrs = var.api_public_access_cidrs
  node_instance_types = ["t3.medium"]
  node_desired_size   = 2
  node_min_size       = 2
  node_max_size       = 4
}

# --- EBS CSI driver (PVCs for Prometheus/Grafana) — first IRSA consumer ----

module "ebs_csi_irsa" {
  source = "../../modules/irsa"

  name              = "${local.name}-ebs-csi"
  namespace         = "kube-system"
  service_account   = "ebs-csi-controller-sa"
  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url
  policy_arns       = ["arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"]
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name                = module.eks.cluster_name
  addon_name                  = "aws-ebs-csi-driver"
  service_account_role_arn    = module.ebs_csi_irsa.role_arn
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}
