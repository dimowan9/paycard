#!/usr/bin/env python3
"""管理命令
  python manage.py init                    建表 + 创建默认管理员 admin / a181818（已存在则跳过）
  python manage.py reset-admin <新密码>     重置 admin 密码
"""
import os
import sys

from werkzeug.security import generate_password_hash

from app import config, db
from app.security import now


def init():
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "app", "schema_sqlite.sql") if db.IS_SQLITE else os.path.join(here, "schema.sql")
    db.run_script(script)
    if not db.query("SELECT id FROM admins WHERE username=%s", (config.DEFAULT_ADMIN_USER,), one=True):
        db.execute("INSERT INTO admins (username, password_hash, created_at) VALUES (%s,%s,%s)",
                   (config.DEFAULT_ADMIN_USER, generate_password_hash(config.DEFAULT_ADMIN_PASS), now()))
        db.commit()
        print(f"已创建默认管理员 {config.DEFAULT_ADMIN_USER} / {config.DEFAULT_ADMIN_PASS}")
    print("数据库初始化完成")


def reset_admin(pw):
    if len(pw) < 6:
        sys.exit("密码至少 6 位")
    db.execute("UPDATE admins SET password_hash=%s WHERE username=%s",
               (generate_password_hash(pw), config.DEFAULT_ADMIN_USER))
    db.commit()
    print("管理员密码已重置")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "init":
        init()
    elif len(sys.argv) >= 3 and sys.argv[1] == "reset-admin":
        reset_admin(sys.argv[2])
    else:
        print(__doc__)
