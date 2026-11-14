# finops-optimizer IRSA: read costs + inventory, write reports, narrate with Bedrock.

data "aws_iam_policy_document" "finops" {
  statement {
    sid       = "CostExplorer"
    actions   = ["ce:GetCostAndUsage", "ce:GetCostForecast"]
    resources = ["*"] # Cost Explorer has no resource-level permissions
  }

  statement {
    sid       = "InventoryRead"
    actions   = ["ec2:DescribeVolumes", "ec2:DescribeAddresses", "ec2:DescribeSnapshots"]
    resources = ["*"]
  }

  # Only exercised when DRY_RUN=false. Scoped to this account/region's volumes,
  # and never volumes tagged aiops:keep=true (also enforced in code).
  statement {
    sid       = "CleanupUnattachedVolumes"
    actions   = ["ec2:DeleteVolume"]
    resources = ["arn:aws:ec2:${var.region}:${local.account_id}:volume/*"]

    condition {
      test     = "StringNotEquals"
      variable = "aws:ResourceTag/aiops:keep"
      values   = ["true"]
    }
  }

  statement {
    sid       = "WriteReports"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/finops-reports/*"]
  }

  statement {
    sid       = "ReadSlackWebhook"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.slack.arn]
  }

  statement {
    sid       = "BedrockNarration"
    actions   = ["bedrock:InvokeModel"]
    resources = local.bedrock_resources
  }
}

module "finops_irsa" {
  source = "../../modules/irsa"

  name              = "${local.name}-finops-optimizer"
  namespace         = "finops"
  service_account   = "finops-optimizer"
  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url
  inline_policies   = { main = data.aws_iam_policy_document.finops.json }
}

output "finops_role_arn" {
  value = module.finops_irsa.role_arn
}
