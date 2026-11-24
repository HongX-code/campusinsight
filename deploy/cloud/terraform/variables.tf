variable "region" {
  type        = string
  description = "华为云 Region，例如 cn-north-4"
  default     = "cn-north-4"
}

variable "project_name" {
  type        = string
  description = "资源名前缀，便于账单与资源组识别"
  default     = "gaoxiao-zhixun"
}

variable "vpc_cidr" {
  type    = string
  default = "192.168.0.0/16"
}

variable "subnet_cidr" {
  type    = string
  default = "192.168.1.0/24"
}

variable "az_primary" {
  type        = string
  description = "主可用区；华北-北京四默认 cn-north-4a"
  default     = "cn-north-4a"
}

variable "az_secondary" {
  type        = string
  description = "备可用区；主备实例必须与主 AZ 不同"
  default     = "cn-north-4b"
}

variable "rds_ha" {
  type        = bool
  description = "true=主备（跨 AZ 自动切换）；false=单机（更便宜）"
  default     = true
}

variable "rds_flavor" {
  type        = string
  description = "RDS 规格；主备用 rds.mysql.n1.large.2.ha，单机用 rds.mysql.n1.large.2（以控制台规格为准）"
  default     = "rds.mysql.n1.large.2.ha"
}

variable "rds_volume_size" {
  type        = number
  description = "RDS 云盘容量（GB）"
  default     = 40
}

variable "rds_password" {
  type        = string
  sensitive   = true
  description = "RDS root 密码：至少 8 位，含大小写字母、数字、特殊字符"
}

variable "redis_password" {
  type        = string
  sensitive   = true
  description = "DCS Redis 密码：至少 8 位，含大小写字母、数字、特殊字符"
}

variable "dcs_capacity" {
  type        = number
  description = "DCS 容量（GB）"
  default     = 1
}

variable "dcs_flavor" {
  type        = string
  description = "DCS 规格；1G 主备常用 redis.ha.xu1.large.r2.1（以控制台规格为准）"
  default     = "redis.ha.xu1.large.r2.1"
}

variable "allowed_ssh_cidr" {
  type        = string
  description = "允许 SSH 的来源网段，建议填公司/学校出口 IP/32"
  default     = "0.0.0.0/0"
}

variable "allowed_web_cidr" {
  type        = string
  description = "允许访问 80/443 的来源网段；演示可先 0.0.0.0/0，正式环境建议收敛"
  default     = "0.0.0.0/0"
}