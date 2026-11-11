import hashlib
import hmac
import re
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation

from cryptography.fernet import Fernet, InvalidToken
from flask import abort, request, session

from . import config

# ---------- CSRF ----------

def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf", "")
        if not token or not hmac.compare_digest(token, session.get("_csrf", "")):
            abort(400, "CSRF 校验失败，请刷新页面后重试")


# ---------- 卡号 / CVV 加密 ----------
_fernet = None


def _f():
    global _fernet
    if _fernet is None:
        if not config.CARD_KEY:
            raise RuntimeError("未配置 CARD_KEY，请检查 .env")
        _fernet = Fernet(config.CARD_KEY.encode())
    return _fernet


def encrypt(text):
    return _f().encrypt(text.encode()).decode()


def decrypt(token):
    try:
        return _f().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, AttributeError):
        return "解密失败"


def card_fingerprint(number):
    """用于卡号去重（不可逆）。"""
    return hmac.new(config.CARD_KEY.encode(), number.encode(), hashlib.sha256).hexdigest()


# ---------- 工具 ----------
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_order_no(prefix):
    return prefix + datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(3).upper()


def parse_money(value):
    try:
        d = Decimal(str(value).strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return d


def fee_of(amount):
    return (amount * config.FEE_RATE).quantize(Decimal("0.01"))


def money(v):
    try:
        return "{:,.2f}".format(Decimal(str(v)))
    except (InvalidOperation, ValueError, TypeError):
        return str(v)


def format_card(number):
    return " ".join(number[i:i + 4] for i in range(0, len(number), 4))


def detect_brand(number):
    if number.startswith("4"):
        return "VISA"
    if number[:2] in {"51", "52", "53", "54", "55"} or (number[:4].isdigit() and 2221 <= int(number[:4]) <= 2720):
        return "MASTERCARD"
    if number[:2] in {"34", "37"}:
        return "AMEX"
    if number.startswith("62"):
        return "UNIONPAY"
    return "CARD"
