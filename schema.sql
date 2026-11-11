-- PayCard 数据库结构（MariaDB / MySQL）
-- install.sh 会自动创建数据库 paycard 并执行本文件，可重复执行

-- 用户表：邮箱 + 密码（哈希）登录；reg_ip 唯一 => 同一 IP 只能注册一个账号
CREATE TABLE IF NOT EXISTS users (
  id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  email          VARCHAR(191)  NOT NULL,
  password_hash  VARCHAR(255)  NOT NULL,
  reg_ip         VARCHAR(45)   NOT NULL,
  last_login_ip  VARCHAR(45)   NULL,
  last_login_at  DATETIME      NULL,
  balance        DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  is_banned      TINYINT(1)    NOT NULL DEFAULT 0,
  failed_logins  INT           NOT NULL DEFAULT 0,
  locked_until   DATETIME      NULL,
  created_at     DATETIME      NOT NULL,
  UNIQUE KEY uk_email (email),
  UNIQUE KEY uk_reg_ip (reg_ip)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 购卡订单（余额购买套餐后生成，等待后台发卡）
CREATE TABLE IF NOT EXISTS card_orders (
  id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  order_no    VARCHAR(32)   NOT NULL,
  user_id     INT UNSIGNED  NOT NULL,
  plan_code   VARCHAR(16)   NOT NULL,
  plan_name   VARCHAR(64)   NOT NULL,
  card_type   VARCHAR(16)   NOT NULL DEFAULT 'virtual',
  qty         INT           NOT NULL DEFAULT 1,
  issued      INT           NOT NULL DEFAULT 0,
  price       DECIMAL(12,2) NOT NULL,
  status      VARCHAR(16)   NOT NULL DEFAULT 'pending',   -- pending / done / cancelled
  created_at  DATETIME      NOT NULL,
  updated_at  DATETIME      NULL,
  UNIQUE KEY uk_order_no (order_no),
  KEY idx_user (user_id),
  KEY idx_status (status),
  CONSTRAINT fk_co_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 卡片表：后台手动录入卡号 / CVV / 有效期；user_id 指向持卡账号
-- 卡号与 CVV 加密存储，card_hash 用于防止重复录入
CREATE TABLE IF NOT EXISTS cards (
  id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  user_id          INT UNSIGNED NULL,
  card_order_id    INT UNSIGNED NULL,
  card_number_enc  TEXT         NOT NULL,
  card_hash        CHAR(64)     NOT NULL,
  last4            CHAR(4)      NOT NULL,
  cvv_enc          TEXT         NOT NULL,
  expiry           VARCHAR(5)   NOT NULL,                  -- MM/YY
  brand            VARCHAR(20)  NOT NULL DEFAULT 'CARD',
  card_type        VARCHAR(16)  NOT NULL DEFAULT 'virtual', -- virtual / physical
  note             VARCHAR(255) NULL,
  status           VARCHAR(16)  NOT NULL DEFAULT 'available', -- available / assigned / revoked
  last_user_id     INT UNSIGNED NULL,
  assigned_at      DATETIME     NULL,
  revoked_at       DATETIME     NULL,
  created_at       DATETIME     NOT NULL,
  UNIQUE KEY uk_card_hash (card_hash),
  KEY idx_user (user_id),
  KEY idx_status (status),
  CONSTRAINT fk_card_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 充值订单：amount 到账金额，fee 2% 手续费，pay_amount 实付 USDT
CREATE TABLE IF NOT EXISTS recharge_orders (
  id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  order_no    VARCHAR(32)   NOT NULL,
  user_id     INT UNSIGNED  NOT NULL,
  amount      DECIMAL(12,2) NOT NULL,
  fee         DECIMAL(12,2) NOT NULL,
  pay_amount  DECIMAL(12,2) NOT NULL,
  remark      VARCHAR(255)  NULL,
  txid        VARCHAR(128)  NULL,
  -- unpaid待付款 / submitted已提交 / confirmed已到账(加余额) / paid已支付·发卡核销(不加余额) / rejected驳回 / expired过期
  status      VARCHAR(16)   NOT NULL DEFAULT 'unpaid',
  created_at  DATETIME      NOT NULL,
  updated_at  DATETIME      NULL,
  UNIQUE KEY uk_order_no (order_no),
  UNIQUE KEY uk_txid (txid),            -- 同一交易哈希只能核销一次，防止一笔转账重复使用
  KEY idx_user (user_id),
  KEY idx_status (status),
  CONSTRAINT fk_ro_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 后台管理员（默认 admin / a181818，首次初始化时创建）
CREATE TABLE IF NOT EXISTS admins (
  id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  username       VARCHAR(64)  NOT NULL,
  password_hash  VARCHAR(255) NOT NULL,
  created_at     DATETIME     NOT NULL,
  UNIQUE KEY uk_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
