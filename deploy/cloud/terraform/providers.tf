terraform {
  required_version = ">= 1.5.0"

  required_providers {
    huaweicloud = {
      source  = "huaweicloud/huaweicloud"
      version = ">= 1.60.0, < 2.0.0"
    }
  }
}

# 凭证只从环境变量读取，绝不写进 .tf 或 tfvars：
#   HW_ACCESS_KEY / HW_SECRET_KEY / HW_PROJECT_ID（可选）
provider "huaweicloud" {
  region = var.region
}