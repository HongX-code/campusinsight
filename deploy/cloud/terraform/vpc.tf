resource "huaweicloud_vpc" "main" {
  name = "${var.project_name}-vpc"
  cidr = var.vpc_cidr
}

resource "huaweicloud_vpc_subnet" "app" {
  name       = "${var.project_name}-subnet"
  vpc_id     = huaweicloud_vpc.main.id
  cidr       = var.subnet_cidr
  gateway_ip = cidrhost(var.subnet_cidr, 1)
  dns_list   = ["100.125.1.250", "100.125.129.250"]
}

# 应用层安全组：只开放 SSH 与 Web，数据库/缓存端口不对外
resource "huaweicloud_networking_secgroup" "app" {
  name        = "${var.project_name}-app-sg"
  description = "CampusInsight app: SSH + HTTP(S) only"
}

resource "huaweicloud_networking_secgroup_rule" "app_ssh" {
  security_group_id = huaweicloud_networking_secgroup.app.id
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = var.allowed_ssh_cidr
  description       = "SSH"
}

resource "huaweicloud_networking_secgroup_rule" "app_http" {
  security_group_id = huaweicloud_networking_secgroup.app.id
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 80
  port_range_max    = 80
  remote_ip_prefix  = var.allowed_web_cidr
  description       = "HTTP via Nginx"
}

resource "huaweicloud_networking_secgroup_rule" "app_https" {
  security_group_id = huaweicloud_networking_secgroup.app.id
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 443
  port_range_max    = 443
  remote_ip_prefix  = var.allowed_web_cidr
  description       = "HTTPS via Nginx"
}

# 数据层安全组：只允许同 VPC 内的应用访问 MySQL / Redis
resource "huaweicloud_networking_secgroup" "data" {
  name        = "${var.project_name}-data-sg"
  description = "CampusInsight data: MySQL/Redis from VPC only"
}

resource "huaweicloud_networking_secgroup_rule" "data_mysql" {
  security_group_id = huaweicloud_networking_secgroup.data.id
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 3306
  port_range_max    = 3306
  remote_ip_prefix  = var.vpc_cidr
  description       = "MySQL from VPC"
}

resource "huaweicloud_networking_secgroup_rule" "data_redis" {
  security_group_id = huaweicloud_networking_secgroup.data.id
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 6379
  port_range_max    = 6379
  remote_ip_prefix  = var.vpc_cidr
  description       = "Redis from VPC"
}