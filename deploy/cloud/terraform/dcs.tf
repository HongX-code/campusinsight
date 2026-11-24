resource "huaweicloud_dcs_instance" "redis" {
  name              = "${var.project_name}-redis"
  engine            = "Redis"
  engine_version    = "5.0"
  capacity          = var.dcs_capacity
  flavor            = var.dcs_flavor
  vpc_id            = huaweicloud_vpc.main.id
  subnet_id         = huaweicloud_vpc_subnet.app.id
  security_group_id = huaweicloud_networking_secgroup.data.id

  availability_zones = [var.az_primary, var.az_secondary]
  password           = var.redis_password

  # 缓存只做加速：关闭持久化也可，这里保留自动备份以便演示回滚
  backup_policy {
    backup_type = "auto"
    save_days   = 7
    period_type = "weekly"
    backup_at   = [1, 2, 3, 4, 5, 6, 7]
    begin_at    = "02:00-04:00"
  }

}