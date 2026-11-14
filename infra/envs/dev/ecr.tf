# One ECR repo per app under apps/.
resource "aws_ecr_repository" "apps" {
  for_each = toset(var.app_names)

  name                 = "aiops/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # teardown must not get stuck on leftover images

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "apps" {
  for_each = aws_ecr_repository.apps

  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
