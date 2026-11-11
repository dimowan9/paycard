"""用户站：首页 / 注册 / 登录 / 充值 / 支付 / 我的账户（gunicorn app.web:app，127.0.0.1:5000）"""
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from . import config, db
from .security import (EMAIL_RE, check_csrf, csrf_token, decrypt, fee_of,
                       format_card, money, new_order_no, now, parse_money)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    SESSION_COOKIE_NAME="pc_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=64 * 1024,
)
# nginx 反向代理：取真实客户端 IP / 协议
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
db.init_app(app)
app.jinja_env.filters["money"] = money
app.jinja_env.filters["cardfmt"] = format_card

STATUS_RECHARGE = {"unpaid": "待付款", "submitted": "待确认", "confirmed": "已到账", "rejected": "已驳回"}
STATUS_CARD_ORDER = {"pending": "待发卡", "done": "已发卡", "cancelled": "已取消"}

LOGIN_FAIL_BANNED = "登录失败，请联系客服"


def client_ip():
    return (request.remote_addr or "0.0.0.0")[:45]


@app.before_request
def _before():
    check_csrf()
    g.user = None
    uid = session.get("uid")
    if uid:
        user = db.query("SELECT * FROM users WHERE id=%s", (uid,), one=True)
        if not user or user["is_banned"]:
            session.pop("uid", None)  # 被封禁的账号立即下线
        else:
            g.user = user


@app.context_processor
def _ctx():
    return dict(csrf_token=csrf_token, user=g.get("user"), cfg=config, plans=config.PLANS,
                fee_pct=(config.FEE_RATE * 100).normalize(),
                STATUS_RECHARGE=STATUS_RECHARGE, STATUS_CARD_ORDER=STATUS_CARD_ORDER)


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not g.user:
            flash("请先登录", "info")
            return redirect(url_for("login", next=request.full_path))
        return fn(*a, **kw)
    return wrapper


def _safe_next(target):
    if target and urlparse(target).netloc == "" and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("account")


# ---------------- 页面 ----------------

@app.get("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("account"))
    form = {"email": ""}
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        form["email"] = email
        ip = client_ip()
        err = None
        if not EMAIL_RE.match(email) or len(email) > 191:
            err = "请输入正确的邮箱地址"
        elif not (6 <= len(pw) <= 64):
            err = "密码长度需为 6-64 位"
        elif pw != pw2:
            err = "两次输入的密码不一致"
        elif db.query("SELECT id FROM users WHERE reg_ip=%s", (ip,), one=True):
            err = "当前 IP 已注册过账号，每个 IP 仅限注册一个账号。如有疑问请联系客服"
        elif db.query("SELECT id FROM users WHERE email=%s", (email,), one=True):
            err = "该邮箱已被注册"
        if not err:
            try:
                cur = db.execute(
                    "INSERT INTO users (email, password_hash, reg_ip, last_login_ip, last_login_at, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (email, generate_password_hash(pw), ip, ip, now(), now()))
                db.commit()
            except db.IntegrityError:
                db.rollback()
                err = "该邮箱或 IP 已注册过账号"
        if err:
            flash(err, "error")
        else:
            session.clear()
            session.permanent = True
            session["uid"] = cur.lastrowid
            flash("注册成功！首次使用请先在本站充值", "success")
            return redirect(url_for("account"))
    return render_template("register.html", form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("account"))
    form = {"email": ""}
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        form["email"] = email
        user = db.query("SELECT * FROM users WHERE email=%s", (email,), one=True)
        if not user or not check_password_hash(user["password_hash"], pw):
            flash("邮箱或密码错误", "error")
        elif user["is_banned"]:
            flash(LOGIN_FAIL_BANNED, "banned")
        else:
            db.execute("UPDATE users SET last_login_ip=%s, last_login_at=%s WHERE id=%s",
                       (client_ip(), now(), user["id"]))
            db.commit()
            session.clear()
            session.permanent = True
            session["uid"] = user["id"]
            return redirect(_safe_next(request.args.get("next")))
    return render_template("login.html", form=form)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.get("/recharge")
def recharge():
    prefill = parse_money(request.args.get("amount", "")) if request.args.get("amount") else None
    return render_template("recharge.html", prefill=prefill)


@app.post("/recharge")
@login_required
def recharge_create():
    amount = parse_money(request.form.get("amount", ""))
    remark = request.form.get("remark", "").strip()[:200] or "为账户充值"
    if amount is None or amount < config.MIN_RECHARGE or amount > config.MAX_RECHARGE:
        flash(f"充值金额需在 {money(config.MIN_RECHARGE)} - {money(config.MAX_RECHARGE)} USDT 之间", "error")
        return redirect(url_for("recharge", amount=request.form.get("amount", "")) + "#custom")
    fee = fee_of(amount)
    order_no = new_order_no("R")
    db.execute(
        "INSERT INTO recharge_orders (order_no, user_id, amount, fee, pay_amount, remark, status, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,'unpaid',%s)",
        (order_no, g.user["id"], amount, fee, amount + fee, remark, now()))
    db.commit()
    return redirect(url_for("pay", order_no=order_no))


@app.post("/buy/<code>")
@login_required
def buy(code):
    plan = config.PLANS.get(code)
    if not plan:
        abort(404)
    price = plan["price"]
    # 原子扣款：余额不足则 rowcount=0
    cur = db.execute("UPDATE users SET balance = balance - %s WHERE id=%s AND balance >= %s",
                     (price, g.user["id"], price))
    if cur.rowcount != 1:
        db.rollback()
        short = (price - Decimal(str(g.user["balance"]))).quantize(Decimal("0.01"))
        short = max(short, config.MIN_RECHARGE)
        flash(f"账户余额不足，购买「{plan['name']}」还需充值 {money(short)} USDT（另收 {(config.FEE_RATE*100).normalize()}% 手续费）", "error")
        return redirect(url_for("recharge", amount=str(short)) + "#custom")
    db.execute(
        "INSERT INTO card_orders (order_no, user_id, plan_code, plan_name, card_type, qty, price, status, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s)",
        (new_order_no("C"), g.user["id"], code, plan["name"], plan["card_type"], plan["qty"], price, now()))
    db.commit()
    flash(f"购买成功！已从余额扣除 {money(price)} USDT，卡片发放后将显示在「我的卡片」中，如需加急请联系客服", "success")
    return redirect(url_for("account"))


@app.route("/pay/<order_no>", methods=["GET", "POST"])
@login_required
def pay(order_no):
    order = db.query("SELECT * FROM recharge_orders WHERE order_no=%s AND user_id=%s",
                     (order_no, g.user["id"]), one=True)
    if not order:
        abort(404)
    if request.method == "POST":
        txid = request.form.get("txid", "").strip()[:128]
        if order["status"] not in ("unpaid", "submitted"):
            flash("该订单已处理，无需重复提交", "info")
        elif len(txid) < 10:
            flash("请填写正确的交易哈希（TxID）", "error")
        else:
            db.execute("UPDATE recharge_orders SET txid=%s, status='submitted', updated_at=%s WHERE id=%s",
                       (txid, now(), order["id"]))
            db.commit()
            flash("已提交，客服核实到账后余额会自动增加。可点击右下角联系客服加快处理", "success")
        return redirect(url_for("pay", order_no=order_no))
    return render_template("pay.html", order=order)


@app.get("/account")
@login_required
def account():
    uid = g.user["id"]
    cards = db.query("SELECT * FROM cards WHERE user_id=%s AND status='assigned' ORDER BY assigned_at DESC", (uid,))
    for c in cards:
        c["number"] = decrypt(c["card_number_enc"])
        c["cvv"] = decrypt(c["cvv_enc"])
    recharges = db.query("SELECT * FROM recharge_orders WHERE user_id=%s ORDER BY id DESC LIMIT 50", (uid,))
    card_orders = db.query("SELECT * FROM card_orders WHERE user_id=%s ORDER BY id DESC LIMIT 50", (uid,))
    has_recharged = any(r["status"] == "confirmed" for r in recharges)
    return render_template("account.html", cards=cards, recharges=recharges,
                           card_orders=card_orders, has_recharged=has_recharged)


@app.errorhandler(400)
@app.errorhandler(404)
def _err(e):
    return render_template("error.html", code=e.code, msg=getattr(e, "description", "")), e.code
