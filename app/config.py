"""配置：从项目根目录的 .env 读取（install.sh 会自动生成）。"""
import os
from decimal import Decimal

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env():
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def env(key, default=None):
    return os.environ.get(key, default)


SITE_NAME = env("SITE_NAME", "PayCard")
KEFU_URL = env("KEFU_URL", "https://t.me/poobwjj")
USDT_ADDRESS = env("USDT_ADDRESS", "TCB4aq2zhy1Jt4JHKhfWRcuDB8EW2rWan7")

# 充值手续费（2%）与最低充值金额
FEE_RATE = Decimal(env("FEE_RATE", "0.02"))
MIN_RECHARGE = Decimal(env("MIN_RECHARGE", "10"))
MAX_RECHARGE = Decimal(env("MAX_RECHARGE", "100000"))

# 数据库：生产用 mysql（MariaDB），本地测试可设 DB_ENGINE=sqlite
DB_ENGINE = env("DB_ENGINE", "mysql")
DB_HOST = env("DB_HOST", "127.0.0.1")
DB_PORT = int(env("DB_PORT", "3306"))
DB_NAME = env("DB_NAME", "paycard")
DB_USER = env("DB_USER", "paycard")
DB_PASS = env("DB_PASS", "")
SQLITE_PATH = env("SQLITE_PATH", os.path.join(BASE_DIR, "paycard.db"))

SECRET_KEY = env("SECRET_KEY", "dev-secret-change-me")
ADMIN_SECRET_KEY = env("ADMIN_SECRET_KEY", "dev-admin-secret-change-me")
# 卡号 / CVV 加密密钥（Fernet）。丢失后已录入的卡将无法解密，务必备份 .env
CARD_KEY = env("CARD_KEY", "")
COOKIE_SECURE = env("COOKIE_SECURE", "1") == "1"

DEFAULT_ADMIN_USER = env("DEFAULT_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASS = env("DEFAULT_ADMIN_PASS", "a181818")

# 套餐：code -> 配置
PLANS = {
    "v1": dict(name="套餐一 · 虚拟卡", desc="1 张虚拟卡（含开卡费）", price=Decimal("88"),
               card_type="virtual", qty=1),
    "v2": dict(name="套餐二 · 双虚拟卡", desc="2 张虚拟卡（含开卡费）", price=Decimal("158"),
               card_type="virtual", qty=2),
    "p1": dict(name="套餐三 · 实体卡", desc="1 张实体卡（含开卡费）", price=Decimal("158"),
               card_type="physical", qty=1),
}
