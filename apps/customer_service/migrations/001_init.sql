-- NanoClaw 生产库初始化（生产落地 P0-1.1 步骤 2）
-- 执行方式：mysql -u root -p ecom_db < migrations/001_init.sql
-- 或由 docker-compose.yml 的 MySQL 容器首次启动时自动执行（挂载至 docker-entrypoint-initdb.d）

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    customer_id   VARCHAR(64)  NOT NULL UNIQUE COMMENT '顾客ID',
    username      VARCHAR(64)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL COMMENT 'bcrypt哈希',
    name          VARCHAR(64)  NOT NULL,
    phone         VARCHAR(20)  DEFAULT '',
    level         VARCHAR(32)  DEFAULT '普通会员' COMMENT '会员等级',
    points        INT          DEFAULT 0 COMMENT '积分',
    role          VARCHAR(16)  DEFAULT 'user' COMMENT 'user/admin',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 商品表（字段与 data/goods.json 对齐：sku/highlights/sizes/scenes/link）
CREATE TABLE IF NOT EXISTS goods (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    sku         VARCHAR(64)  NOT NULL UNIQUE COMMENT '商品编号（与goods.json的sku一致）',
    name        VARCHAR(255) NOT NULL,
    category    VARCHAR(64)  DEFAULT '',
    price       DECIMAL(10,2) NOT NULL,
    highlights  VARCHAR(512) DEFAULT '' COMMENT '卖点',
    description TEXT,
    stock       INT          DEFAULT 0,
    sizes       JSON         DEFAULT NULL COMMENT '尺码库存JSON',
    scenes      JSON         DEFAULT NULL COMMENT '适用场景JSON',
    link        VARCHAR(512) DEFAULT '' COMMENT '购买链接',
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_category (category),
    INDEX idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 订单表
CREATE TABLE IF NOT EXISTS orders (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id      VARCHAR(64)  NOT NULL UNIQUE,
    customer_id   VARCHAR(64)  NOT NULL,
    status        VARCHAR(32)  NOT NULL COMMENT '待付款/已付款/已发货/已签收/已完成/已取消',
    total         DECIMAL(10,2) NOT NULL,
    receiver_name VARCHAR(64)  DEFAULT '',
    receiver_phone VARCHAR(20) DEFAULT '',
    address       VARCHAR(512) DEFAULT '',
    items         JSON         DEFAULT NULL COMMENT '商品明细JSON',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    paid_at       DATETIME     NULL,
    shipped_at    DATETIME     NULL,
    signed_at     DATETIME     NULL,
    INDEX idx_customer (customer_id),
    INDEX idx_status (status),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 物流表
CREATE TABLE IF NOT EXISTS logistics (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id    VARCHAR(64)  NOT NULL,
    carrier     VARCHAR(64)  DEFAULT '' COMMENT '快递公司',
    tracking_no VARCHAR(64)  DEFAULT '',
    status      VARCHAR(32)  DEFAULT '待发货',
    traces      JSON         DEFAULT NULL COMMENT '物流轨迹JSON',
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_order (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 工单表
CREATE TABLE IF NOT EXISTS tickets (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    ticket_id     VARCHAR(64)  NOT NULL UNIQUE,
    customer_id   VARCHAR(64)  NOT NULL,
    order_id      VARCHAR(64)  DEFAULT '',
    type          VARCHAR(32)  NOT NULL COMMENT '退款/换货/投诉/咨询/售后',
    status        VARCHAR(32)  NOT NULL DEFAULT '待处理' COMMENT '待处理/处理中/已完成/已关闭',
    priority      TINYINT      DEFAULT 1 COMMENT '1普通 2紧急',
    summary       VARCHAR(512) DEFAULT '',
    assigned_to   VARCHAR(64)  DEFAULT '' COMMENT '处理人工号',
    description   TEXT         COMMENT '问题描述（与 tickets.json 对齐）',
    priority_label VARCHAR(16) DEFAULT '普通' COMMENT '普通/加急',
    records       JSON         DEFAULT NULL COMMENT '处理记录列表',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NULL,
    INDEX idx_customer (customer_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 退款记录表
CREATE TABLE IF NOT EXISTS refund_records (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    refund_id     VARCHAR(64)  NOT NULL UNIQUE,
    order_id      VARCHAR(64)  NOT NULL,
    customer_id   VARCHAR(64)  NOT NULL,
    amount        DECIMAL(10,2) NOT NULL,
    reason        VARCHAR(255) DEFAULT '',
    decided_by    VARCHAR(16)  NOT NULL COMMENT 'auto/manual',
    decision      VARCHAR(16)  NOT NULL COMMENT 'approve/reject',
    rule_hit      VARCHAR(16)  DEFAULT '' COMMENT '命中规则R0-R8',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_customer (customer_id),
    INDEX idx_order (order_id),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 退款审计日志表
CREATE TABLE IF NOT EXISTS refund_audit_log (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    refund_id     VARCHAR(64)  NOT NULL DEFAULT '',
    customer_id   VARCHAR(64)  NOT NULL,
    order_id      VARCHAR(64)  NOT NULL,
    action        VARCHAR(16)  NOT NULL,
    reason        VARCHAR(512) DEFAULT '',
    rule_hit      VARCHAR(16)  DEFAULT '',
    profile_snapshot JSON      DEFAULT NULL COMMENT '决策时用户画像快照',
    detail        TEXT         COMMENT '决策依据明细',
    amount        DECIMAL(10,2) NULL COMMENT '订单金额',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_refund (refund_id),
    INDEX idx_customer (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 用户偏好记忆表
CREATE TABLE IF NOT EXISTS user_preferences (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    customer_id VARCHAR(64)  NOT NULL,
    content     VARCHAR(512) NOT NULL,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_customer (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
