# Generic IRSA role: an IAM role that exactly one Kubernetes service account
# (namespace/name) can assume via the cluster's OIDC provider.

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:${var.namespace}:${var.service_account}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "managed" {
  for_each = toset(var.policy_arns)

  role       = aws_iam_role.this.name
  policy_arn = each.value
}

# for_each over a map whose keys are static at the call site: the policy JSON
# itself is often unknown until apply (it references ARNs), which a
# `count = var.policy_json == null ? 0 : 1` could not plan.
resource "aws_iam_role_policy" "inline" {
  for_each = var.inline_policies

  name   = "${var.name}-${each.key}"
  role   = aws_iam_role.this.id
  policy = each.value
}
