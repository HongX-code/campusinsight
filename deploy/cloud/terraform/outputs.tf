output "region" {
  value = var.region
}

output "vpc_id" {
  value = huaweicloud_vpc.main.id
}

output "subnet_id" {
  value = huaweicloud_vpc_subnet.app.id
}

output "app_security_group" {
  value = huaweicloud_networking_secgroup.app.name
}

output "rds_private_ip" {
  value     = try(huaweicloud_rds_instance.mysql.private_ips[0], "")
  sensitive = false
}

output "rds_port" {
  value = 3306
}

output "rds_database" {
  value = "gaoxiao_zhixun"
}

output "redis_private_ip" {
  value = try(huaweicloud_dcs_instance.redis.private_ip, "")
}

output "redis_port" {
  value = 6379
}

# 连接串含密码，标记 sensitive，避免 terraform output 直接打印到日志
output "database_url" {
  value     = "mysql+pymysql://root:${urlencode(var.rds_password)}@${try(huaweicloud_rds_instance.mysql.private_ips[0], "RDS_PRIVATE_IP")}:3306/gaoxiao_zhixun?charset=utf8mb4"
  sensitive = true
}

output "redis_url" {
  value     = "redis://:${urlencode(var.redis_password)}@${try(huaweicloud_dcs_instance.redis.private_ip, "REDIS_PRIVATE_IP")}:6379/0"
  sensitive = true
}