terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket = "llm-gateway-terraform-state"
    key    = "eks/terraform.tfstate"
    region = "us-west-2"
  }
}

provider "aws" {
  region = var.region
}
