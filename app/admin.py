"""后台管理（gunicorn app.admin:app，127.0.0.1:5001，由 nginx 8888 端口 HTTPS 反代）"""
import re
from datetime import timedelta
from decimal import Decimal
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from . import config, db
from .security import (card_fingerprint, check_csrf, csrf_token, decrypt,
                       detect_brand, encrypt, format_card, money, now,
                       parse_money)

app = Flask(__name__, template_folder="templates/admin")
app.config.update(
    SECRET_KEY=config.ADMIN_SECRET_KEY,          # 与用户站不同的密钥
    SESSION_COOKIE_NAME="pc_admin",              # 与用户站不同的 cookie
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=64 * 1024,
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
db.init_app(app)
app.jinja_env.filters["money"] = money
app.jinja_env.filters["cardfmt"] = format_card

EXPIRY_RE = re.compile(r"^(0[1-9]|1[0-2])/\d{2}$")
CARD_STATUS = {"available": "库存", "assigned": "已发卡", "revoked": "已撤回"}
RECHARGE_STATUS = {"unpaid": "待付款", "submitted": "待确认", "confirmed": "已到账·入余额",
                   "paid": "已支付·发卡核销", "rejected": "已驳回", "expired": "已过期"}
RECHARGE_PILL = {"unpaid": "p-warn", "submitted": "p-pri", "confirmed": "p-ok",
                 "paid": "p-ok", "rejected": "p-err", "expired": "p-muted"}
CO_STATUS = {"pending": "待发卡", "done": "已完成", "cancelled": "已取消"}


@app.before_request
def _before():
    check_csrf()
    g.admin = None
    aid = session.get("aid")
    if aid:
        g.admin = db.query("SELECT id, username FROM admins WHERE id=%s", (aid,), one=True)
    if not g.admin and request.endpoint not in ("login", "static"):
        return redirect(url_for("login"))


@app.context_processor
def _ctx():
    pending = {}
    if g.get("admin"):
        pending["recharge"] = db.query("SELECT COUNT(*) AS n FROM recharge_orders WHERE status='submitted'", one=True)["n"]
        pending["card"] = db.query("SELECT COUNT(*) AS n FROM card_orders WHERE status='pending'", one=True)["n"]
        pending["stock"] = db.query("SELECT COUNT(*) AS n FROM cards WHERE status='available'", one=True)["n"]
    return dict(csrf_token=csrf_token, admin=g.get("admin"), cfg=config, pending=pending,
                CARD_STATUS=CARD_STATUS, RECHARGE_STATUS=RECHARGE_STATUS,
                RECHARGE_PILL=RECHARGE_PILL, CO_STATUS=CO_STATUS)


def back(default="users"):
    ref = request.form.get("back") or request.referrer
    if ref and ref.startswith(request.host_url):
        return redirect(ref)
    return redirect(url_for(default))


# ---------------- 登录 ----------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        row = db.query("SELECT * FROM admins WHERE username=%s", (u,), one=True)
        if row and check_password_hash(row["password_hash"], p):
            session.clear()
            session.permanent = True
            session["aid"] = row["id"]
            return redirect(url_for("users"))
        flash("账号或密码错误", "error")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/password", methods=["GET", "POST"])
def password():
    if request.method == "POST":
        old, new, new2 = (request.form.get(k, "") for k in ("old", "new", "new2"))
        row = db.query("SELECT * FROM admins WHERE id=%s", (g.admin["id"],), one=True)
        if not check_password_hash(row["password_hash"], old):
            flash("原密码错误", "error")
        elif len(new) < 6 or new != new2:
            flash("新密码至少 6 位且两次输入一致", "error")
        else:
            db.execute("UPDATE admins SET password_hash=%s WHERE id=%s", (generate_password_hash(new), row["id"]))
            db.commit()
            flash("密码已修改", "success")
            return redirect(url_for("users"))
    return render_template("password.html")


# ---------------- 用户管理 ----------------

@app.get("/")
def home():
    return redirect(url_for("users"))


@app.get("/users")
def users():
    q = request.args.get("q", "").strip()
    sql = ("SELECT u.*, (SELECT COUNT(*) FROM cards c WHERE c.user_id=u.id AND c.status='assigned') AS card_count, "
           "(SELECT COUNT(*) FROM card_orders o WHERE o.user_id=u.id AND o.status='pending') AS pending_orders "
           "FROM users u")
    args = ()
    if q:
        sql += " WHERE u.email LIKE %s OR u.reg_ip LIKE %s OR u.last_login_ip LIKE %s"
        like = f"%{q}%"
        args = (like, like, like)
    sql += " ORDER BY u.id DESC LIMIT 500"
    return render_template("users.html", rows=db.query(sql, args), q=q)


@app.get("/users/<int:uid>")
def user_detail(uid):
    u = db.query("SELECT * FROM users WHERE id=%s", (uid,), one=True)
    if not u:
        abort(404)
    cards = db.query("SELECT * FROM cards WHERE user_id=%s AND status='assigned' ORDER BY assigned_at DESC", (uid,))
    for c in cards:
        c["number"] = decrypt(c["card_number_enc"])
        c["cvv"] = decrypt(c["cvv_enc"])
    stock = db.query("SELECT id, last4, expiry, brand, card_type, note FROM cards WHERE status='available' ORDER BY id")
    orders = db.query("SELECT * FROM card_orders WHERE user_id=%s ORDER BY id DESC", (uid,))
    recharges = db.query("SELECT * FROM recharge_orders WHERE user_id=%s ORDER BY id DESC LIMIT 50", (uid,))
    return render_template("user_detail.html", u=u, cards=cards, stock=stock, orders=orders, recharges=recharges)


@app.post("/users/<int:uid>/ban")
def user_ban(uid):
    db.execute("UPDATE users SET is_banned=1 WHERE id=%s", (uid,))
    db.commit()
    flash("已封禁该账号，用户将无法登录（提示：登录失败，请联系客服）", "success")
    return back()


@app.post("/users/<int:uid>/unban")
def user_unban(uid):
    db.execute("UPDATE users SET is_banned=0 WHERE id=%s", (uid,))
    db.commit()
    flash("已解除封禁", "success")
    return back()


@app.post("/users/<int:uid>/balance")
def user_balance(uid):
    delta = parse_money(request.form.get("delta", ""))
    if delta is None or delta == 0:
        flash("请输入调整金额（正数增加，负数扣减）", "error")
        return back()
    cur = db.execute("UPDATE users SET balance = balance + %s WHERE id=%s AND balance + %s >= 0", (delta, uid, delta))
    if cur.rowcount != 1:
        db.rollback()
        flash("调整失败：余额不能为负", "error")
    else:
        db.commit()
        flash(f"余额已调整 {money(delta)} USDT", "success")
    return back()


# ---------------- 卡库 / 发卡 / 撤回 ----------------

@app.route("/cards", methods=["GET", "POST"])
def cards():
    if request.method == "POST":
        number = re.sub(r"[\s-]", "", request.form.get("card_number", ""))
        cvv = request.form.get("cvv", "").strip()
        expiry = request.form.get("expiry", "").strip().replace(" ", "")
        if re.fullmatch(r"\d{4}", expiry):
            expiry = expiry[:2] + "/" + expiry[2:]
        card_type = request.form.get("card_type", "virtual")
        note = request.form.get("note", "").strip()[:255] or None
        err = None
        if not re.fullmatch(r"\d{12,19}", number):
            err = "卡号应为 12-19 位数字"
        elif not re.fullmatch(r"\d{3}", cvv):
            err = "CVV 安全码必须为 3 位数字，请核对后重新录入"
        elif not EXPIRY_RE.match(expiry):
            err = "有效期格式应为 MM/YY，例如 09/28"
        elif card_type not in ("virtual", "physical"):
            err = "卡类型错误"
        if not err:
            try:
                db.execute(
                    "INSERT INTO cards (card_number_enc, card_hash, last4, cvv_enc, expiry, brand, card_type, note, status, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'available',%s)",
                    (encrypt(number), card_fingerprint(number), number[-4:], encrypt(cvv), expiry,
                     detect_brand(number), card_type, note, now()))
                db.commit()
                flash(f"已录入卡片 **** {number[-4:]}", "success")
            except db.IntegrityError:
                db.rollback()
                err = "该卡号已存在，不能重复录入"
        if err:
            flash(err, "error")
        return redirect(url_for("cards", status=request.args.get("status", "")))

    status = request.args.get("status", "")
    sql = ("SELECT c.*, u.email AS user_email, lu.email AS last_email FROM cards c "
           "LEFT JOIN users u ON u.id=c.user_id LEFT JOIN users lu ON lu.id=c.last_user_id")
    args = ()
    if status in CARD_STATUS:
        sql += " WHERE c.status=%s"
        args = (status,)
    sql += " ORDER BY c.id DESC LIMIT 1000"
    rows = db.query(sql, args)
    for c in rows:
        c["number"] = decrypt(c["card_number_enc"])
        c["cvv"] = decrypt(c["cvv_enc"])
    return render_template("cards.html", rows=rows, status=status)


@app.post("/cards/issue")
def card_issue():
    try:
        uid = int(request.form.get("user_id", ""))
        cid = int(request.form.get("card_id", ""))
    except ValueError:
        flash("请选择要发放的卡片", "error")
        return back()
    oid = request.form.get("order_id") or None
    oid = int(oid) if oid and oid.isdigit() else None
    user = db.query("SELECT id, email FROM users WHERE id=%s", (uid,), one=True)
    if not user:
        abort(404)
    cur = db.execute(
        "UPDATE cards SET user_id=%s, card_order_id=%s, status='assigned', assigned_at=%s, revoked_at=NULL "
        "WHERE id=%s AND status='available'", (uid, oid, now(), cid))
    if cur.rowcount != 1:
        db.rollback()
        flash("发卡失败：该卡已不在库存中", "error")
        return back()
    settled = None
    if oid:
        db.execute("UPDATE card_orders SET issued = issued + 1, updated_at=%s WHERE id=%s AND user_id=%s",
                   (now(), oid, uid))
        db.execute("UPDATE card_orders SET status='done' WHERE id=%s AND issued >= qty", (oid,))
    else:
        # 直发（不走余额购卡）：以发卡为准，把该用户最近一条未核销的付款记录标记为「已支付」
        # 注意：paid 不会给用户加余额（这笔钱直接换了这张卡），与「确认到账·入余额」互斥
        ro = db.query("SELECT id, order_no, pay_amount FROM recharge_orders "
                      "WHERE user_id=%s AND status IN ('unpaid','submitted') ORDER BY id DESC LIMIT 1",
                      (uid,), one=True)
        if ro:
            db.execute("UPDATE recharge_orders SET status='paid', updated_at=%s WHERE id=%s", (now(), ro["id"]))
            settled = ro
    db.commit()
    msg = f"已向 {user['email']} 发卡，用户刷新页面即可看到"
    if settled:
        msg += f"；其最近一笔付款订单 {settled['order_no']}（实付 {money(settled['pay_amount'])} USDT）已标记为「已支付」"
    flash(msg, "success")
    return back()


@app.post("/cards/<int:cid>/revoke")
def card_revoke(cid):
    card = db.query("SELECT * FROM cards WHERE id=%s", (cid,), one=True)
    if not card or card["status"] != "assigned":
        flash("该卡未发放，无需撤回", "error")
        return back()
    db.execute("UPDATE cards SET status='revoked', last_user_id=user_id, user_id=NULL, revoked_at=%s WHERE id=%s",
               (now(), cid))
    if card["card_order_id"]:
        db.execute("UPDATE card_orders SET issued = issued - 1, status='pending', updated_at=%s "
                   "WHERE id=%s AND status<>'cancelled' AND issued > 0", (now(), card["card_order_id"]))
    db.commit()
    flash(f"已撤回卡片 **** {card['last4']}，用户页面将不再显示", "success")
    return back()


@app.post("/cards/<int:cid>/restore")
def card_restore(cid):
    db.execute("UPDATE cards SET status='available', card_order_id=NULL WHERE id=%s AND status='revoked'", (cid,))
    db.commit()
    flash("已放回库存，可重新发放", "success")
    return back("cards")


@app.post("/cards/<int:cid>/delete")
def card_delete(cid):
    cur = db.execute("DELETE FROM cards WHERE id=%s AND status IN ('available','revoked')", (cid,))
    db.commit()
    flash("已删除" if cur.rowcount else "已发放的卡需先撤回才能删除", "success" if cur.rowcount else "error")
    return back("cards")


# ---------------- 订单 ----------------

@app.get("/recharges")
def recharges():
    status = request.args.get("status", "submitted")
    sql = "SELECT r.*, u.email FROM recharge_orders r JOIN users u ON u.id=r.user_id"
    args = ()
    if status in RECHARGE_STATUS:
        sql += " WHERE r.status=%s"
        args = (status,)
    sql += " ORDER BY r.id DESC LIMIT 500"
    return render_template("recharges.html", rows=db.query(sql, args), status=status)


@app.post("/recharges/<int:rid>/confirm")
def recharge_confirm(rid):
    r = db.query("SELECT * FROM recharge_orders WHERE id=%s", (rid,), one=True)
    if not r:
        abort(404)
    cur = db.execute("UPDATE recharge_orders SET status='confirmed', updated_at=%s "
                     "WHERE id=%s AND status IN ('unpaid','submitted')", (now(), rid))
    if cur.rowcount != 1:
        db.rollback()
        flash("该订单已处理过", "error")
        return back("recharges")
    db.execute("UPDATE users SET balance = balance + %s WHERE id=%s", (r["amount"], r["user_id"]))
    db.commit()
    flash(f"已确认到账，用户余额 +{money(r['amount'])} USDT", "success")
    return back("recharges")


@app.post("/recharges/<int:rid>/reject")
def recharge_reject(rid):
    db.execute("UPDATE recharge_orders SET status='rejected', updated_at=%s "
               "WHERE id=%s AND status IN ('unpaid','submitted')", (now(), rid))
    db.commit()
    flash("已驳回", "success")
    return back("recharges")


@app.get("/card-orders")
def card_orders():
    status = request.args.get("status", "pending")
    sql = "SELECT o.*, u.email FROM card_orders o JOIN users u ON u.id=o.user_id"
    args = ()
    if status in CO_STATUS:
        sql += " WHERE o.status=%s"
        args = (status,)
    sql += " ORDER BY o.id DESC LIMIT 500"
    return render_template("card_orders.html", rows=db.query(sql, args), status=status)


@app.post("/card-orders/<int:oid>/cancel")
def card_order_cancel(oid):
    """取消购卡订单并退回余额（仅限尚未发卡的订单）"""
    o = db.query("SELECT * FROM card_orders WHERE id=%s", (oid,), one=True)
    if not o:
        abort(404)
    cur = db.execute("UPDATE card_orders SET status='cancelled', updated_at=%s WHERE id=%s AND status='pending' AND issued=0",
                     (now(), oid))
    if cur.rowcount != 1:
        db.rollback()
        flash("只能取消尚未发卡的待处理订单", "error")
        return back("card_orders")
    db.execute("UPDATE users SET balance = balance + %s WHERE id=%s", (o["price"], o["user_id"]))
    db.commit()
    flash(f"订单已取消，已退回 {money(o['price'])} USDT 到用户余额", "success")
    return back("card_orders")


@app.errorhandler(400)
@app.errorhandler(404)
def _err(e):
    return render_template("error.html", code=e.code, msg=getattr(e, "description", "")), e.code
