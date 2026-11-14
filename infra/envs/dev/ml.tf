# ML + alerting resources: artifacts bucket, SageMaker execution role,
# Slack webhook secret, and the log-detector's IRSA role.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # Bedrock cross-region inference profiles route to the foundation model in
  # several regions, so the model ARN is wildcarded on region.
  bedrock_resources = [
    "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/us.anthropic.claude-haiku-*",
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-*",
  ]
}

# --- Artifacts bucket: training data, source tarballs, models, FinOps reports

resource "aws_s3_bucket" "artifacts" {
  bucket        = "aiops-artifacts-${local.account_id}"
  force_destroy = true # teardown must not stall on leftover objects
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-code-tarballs"
    status = "Enabled"
    filter { prefix = "code/" }
    expiration { days = 30 }
  }

  rule {
    id     = "expire-finops-reports"
    status = "Enabled"
    filter { prefix = "finops-reports/" }
    expiration { days = 90 }
  }
}

# --- SageMaker training execution role -------------------------------------

data "aws_iam_policy_document" "sagemaker_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sagemaker" {
  name               = "${local.name}-sagemaker"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_assume.json
}

data "aws_iam_policy_document" "sagemaker" {
  statement {
    sid       = "ReadInputsWriteModels"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }

  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]
  }

  statement {
    sid = "TrainingLogsAndMetrics"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
      "cloudwatch:PutMetricData",
    ]
    resources = ["*"]
  }

  statement {
    sid = "PullFrameworkImage"
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sagemaker" {
  name   = "${local.name}-sagemaker"
  role   = aws_iam_role.sagemaker.id
  policy = data.aws_iam_policy_document.sagemaker.json
}

# --- Slack webhook secret ---------------------------------------------------
# Only the container is managed here; the value is set out-of-band so it never
# lands in Terraform state:
#   aws secretsmanager put-secret-value --secret-id aiops/slack-webhook \
#     --secret-string 'https://hooks.slack.com/services/...'

resource "aws_secretsmanager_secret" "slack" {
  name                    = "aiops/slack-webhook"
  recovery_window_in_days = 0 # allow immediate re-create after teardown
}

# --- log-detector IRSA ------------------------------------------------------

data "aws_iam_policy_document" "log_detector" {
  statement {
    sid       = "ReadModels"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/models/*"]
  }

  statement {
    sid       = "ReadSlackWebhook"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.slack.arn]
  }

  statement {
    sid       = "BedrockTriage"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = local.bedrock_resources
  }
}

module "log_detector_irsa" {
  source = "../../modules/irsa"

  name              = "${local.name}-log-detector"
  namespace         = "log-detector"
  service_account   = "log-detector"
  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url
  policy_json       = data.aws_iam_policy_document.log_detector.json
}
