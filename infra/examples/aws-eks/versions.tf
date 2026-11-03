terraform {
  required_version = ">= 1.10"

  # A deployment repo adds its own backend here. This example has none and is
  # never applied.

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
