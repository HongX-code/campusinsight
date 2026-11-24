resource "huaweicloud_rds_instance" "mysql" {
  name              = "${var.project_name}-mysql"
  flavor            = var.rds_flavor
  vpc_id            = huaweicloud_vpc.main.id
  subnet_id         = huaweicloud_vpc_subnet.app.id
  security_group_id = huaweicloud_networking_secgroup.data.id

  availability_zone = var.rds_ha ? [var.az_primary, var.az_secondary] : [var.az_primary]

  db {
    type     = "MySQL"
    version  = "8.0"
    password = var.rds_password
    port     = 3306
  }

  volume {
    type = "CLOUDSSD"
    size = var.rds_volume_size
  }

  backup_strategy {
    start_time = "03:00-04:00"
    keep_days  = 7
  }

  # 主备实例使用半同步复制，兼顾一致性与可用性；单机实例忽略该参数
  ha_replication_mode = var.rds_ha ? "semisync" : null

  parameters {
    name  = "character_set_server"
    value = "utf8mb4"
  }

  parameters {
    name  = "collation_server"
    value = "utf8mb4_unicode_ci"
  }

}