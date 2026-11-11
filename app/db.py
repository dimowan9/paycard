"""极简数据库层：生产 MariaDB（PyMySQL），本地测试 SQLite。SQL 统一使用 %s 占位符。"""
import sqlite3
from decimal import Decimal

from flask import g, has_app_context

from . import config

try:
    import pymysql
    import pymysql.cursors
except ImportError:  # 本地 sqlite 测试时可不装
    pymysql = None

IS_SQLITE = config.DB_ENGINE == "sqlite"
sqlite3.register_adapter(Decimal, lambda d: str(d))

IntegrityError = (sqlite3.IntegrityError,) + ((pymysql.err.IntegrityError,) if pymysql else ())

_standalone = None


def _connect():
    if IS_SQLITE:
        c = sqlite3.connect(config.SQLITE_PATH)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c
    return pymysql.connect(
        host=config.DB_HOST, port=config.DB_PORT, user=config.DB_USER,
        password=config.DB_PASS, database=config.DB_NAME, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def conn():
    global _standalone
    if has_app_context():
        if "db" not in g:
            g.db = _connect()
        return g.db
    if _standalone is None:
        _standalone = _connect()
    return _standalone


def _sql(sql):
    return sql.replace("%s", "?") if IS_SQLITE else sql


def query(sql, args=(), one=False):
    cur = conn().cursor()
    cur.execute(_sql(sql), args)
    rows = [dict(r) for r in cur.fetchall()]
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    cur = conn().cursor()
    cur.execute(_sql(sql), args)
    return cur


def commit():
    conn().commit()


def rollback():
    conn().rollback()


def close(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def init_app(app):
    app.teardown_appcontext(close)


def run_script(path):
    """执行建表脚本（CREATE TABLE IF NOT EXISTS，可重复执行）。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if IS_SQLITE:
        conn().executescript(text)
        conn().commit()
        return
    lines = [l for l in text.splitlines() if not l.strip().startswith("--")]
    for stmt in "\n".join(lines).split(";"):
        if stmt.strip():
            execute(stmt)
    commit()
