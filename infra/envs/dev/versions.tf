terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Bucket comes from infra/bootstrap. Native S3 locking (no DynamoDB table).
  backend "s3" {
    bucket       = "aiops-tfstate-818651034041"
    key          = "aiops/dev/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "aiops-platform"
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}
