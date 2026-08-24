data "aws_iam_policy_document" "github_deploy_assume" {
  count = var.github_repository != "" && var.github_oidc_provider_arn != "" ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count = var.github_repository != "" && var.github_oidc_provider_arn != "" ? 1 : 0

  name               = "${local.prefix}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume[0].json
  tags               = local.tags
}

data "aws_iam_policy_document" "github_deploy" {
  count = var.github_repository != "" && var.github_oidc_provider_arn != "" ? 1 : 0

  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.runtime.arn]
  }
  statement {
    actions   = ["ecs:DescribeServices", "ecs:UpdateService"]
    resources = [aws_ecs_service.api.id, aws_ecs_service.worker.id, aws_ecs_service.demo.id]
  }
  statement {
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"]
    resources = ["*"]
  }
  statement {
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.api.arn,
      aws_iam_role.api_execution.arn,
      aws_iam_role.worker.arn,
      aws_iam_role.worker_execution.arn,
      aws_iam_role.demo.arn,
      aws_iam_role.demo_execution.arn,
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count = var.github_repository != "" && var.github_oidc_provider_arn != "" ? 1 : 0

  name   = "immutable-image-deploy"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy[0].json
}
