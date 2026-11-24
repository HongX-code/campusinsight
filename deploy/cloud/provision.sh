#!/usr/bin/env bash
# 华为云 RDS for MySQL + DCS for Redis 开通脚本（Terraform）
#
# 前置条件：
#   1. 已安装 Terraform >= 1.5（https://developer.hashicorp.com/terraform/downloads）
#   2. 已 export HW_ACCESS_KEY / HW_SECRET_KEY（可选 HW_PROJECT_ID）
#   3. 已复制 terraform.tfvars.example 为 terraform.tfvars 并改掉密码/规格
#   4. 执行机可访问 huaweicloud.com（内网/受限网络需放行 API 域名）
#
# 安全约定：脚本只读取环境变量，不回显 AK/SK；连接串写入 cloud.env（600）并被 git 忽略。
set -euo pipefail

cd "$(dirname "$0")/terraform"

: "${HW_ACCESS_KEY:?请先 export HW_ACCESS_KEY（不要在命令行里直接写密钥）}"
: "${HW_SECRET_KEY:?请先 export HW_SECRET_KEY}"

if ! command -v terraform >/dev/null 2>&1; then
  echo "[FAIL] 未找到 terraform，请先安装 Terraform >= 1.5 后重试。" >&2
  exit 1
fi

if [ ! -f terraform.tfvars ]; then
  echo "[FAIL] 缺少 terraform.tfvars。" >&2
  echo "       请执行：cp terraform.tfvars.example terraform.tfvars，然后填写密码与规格。" >&2
  exit 1
fi

echo "[1/4] terraform init"
terraform init -input=false

echo "[2/4] terraform validate"
terraform validate

echo "[3/4] terraform plan"
terraform plan -input=false -out=tfplan

echo "[4/4] terraform apply"
terraform apply -input=false -auto-approve tfplan

# 非敏感信息：区域、VPC/子网、私网 IP、端口
terraform output -json > ../cloud-output.json

# 敏感连接串单独落盘，权限 600，且已在 .gitignore 中忽略
umask 077
{
  echo "# 由 provision.sh 生成于 $(date '+%Y-%m-%d %H:%M:%S')"
  echo "# 含真实密码，禁止提交、禁止贴到聊天/文档；上传服务器后放在 /etc/gaoxiao-zhixun/env"
  echo "DATABASE_URL=$(terraform output -raw database_url)"
  echo "REDIS_URL=$(terraform output -raw redis_url)"
  echo "DB_NAME=gaoxiao_zhixun"
  echo "REDIS_PORT=6379"
} > ../cloud.env
chmod 600 ../cloud.env

echo
echo "[OK] 资源已创建。"
echo "     非敏感输出：deploy/cloud/cloud-output.json"
echo "     连接配置  ：deploy/cloud/cloud.env（600，已忽略提交）"
echo
echo "下一步："
echo "  1) 初始化数据库：把 cloud.env 上传 ECS 后执行 python scripts/cloud_bootstrap.py --create-db"
echo "  2) 部署应用    ：python .codeartsdoer/skills/auto-deploy/scripts/auto_deploy.py --help"
echo "  3) 清理资源    ：cd deploy/cloud/terraform && terraform destroy"