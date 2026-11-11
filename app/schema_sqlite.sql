-- 仅用于本地 SQLite 测试，结构与 schema.sql 对应
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  reg_ip TEXT NOT NULL UNIQUE,
  last_login_ip TEXT, last_login_at TEXT,
  balance NUMERIC NOT NULL DEFAULT 0,
  is_banned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS card_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_code TEXT NOT NULL, plan_name TEXT NOT NULL,
  card_type TEXT NOT NULL DEFAULT 'virtual',
  qty INTEGER NOT NULL DEFAULT 1, issued INTEGER NOT NULL DEFAULT 0,
  price NUMERIC NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  card_order_id INTEGER,
  card_number_enc TEXT NOT NULL, card_hash TEXT NOT NULL UNIQUE,
  last4 TEXT NOT NULL, cvv_enc TEXT NOT NULL, expiry TEXT NOT NULL,
  brand TEXT NOT NULL DEFAULT 'CARD', card_type TEXT NOT NULL DEFAULT 'virtual',
  note TEXT, status TEXT NOT NULL DEFAULT 'available',
  last_user_id INTEGER, assigned_at TEXT, revoked_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recharge_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  amount NUMERIC NOT NULL, fee NUMERIC NOT NULL, pay_amount NUMERIC NOT NULL,
  remark TEXT, txid TEXT, status TEXT NOT NULL DEFAULT 'unpaid',
  created_at TEXT NOT NULL, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS admins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
