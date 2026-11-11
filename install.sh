#!/usr/bin/env bash
# =====================================================================
#  PayCard 一键部署脚本  ——  Rocky Linux 10（兼容 RHEL / AlmaLinux 9/10）
#
#  用法（root）：
#     git clone https://github.com/dimowan9/paycard.git && cd paycard
#     bash install.sh
#  或非交互：
#     DOMAIN=pay.example.com EMAIL=you@example.com ADMIN_ALLOW_IPS="1.2.3.4 5.6.7.8" bash install.sh
#
#  会自动完成：
#   1. 安装 nginx / MariaDB / Python / certbot / firewalld
#   2. 部署代码到 /opt/paycard，创建 Python 虚拟环境
#   3. 创建数据库 paycard、数据库用户，建表，创建后台账号 admin / a181818
#   4. 注册 systemd 服务（用户站 127.0.0.1:5000，后台 127.0.0.1:5001）
#   5. 申请 Let's Encrypt 免费证书，自动写入 nginx 配置，自动续期
#   6. nginx：443 用户站，8888 后台（HTTPS）；登录/注册接口限流
#   7. SELinux、防火墙放行 80 / 443 / 8888
#
#  重复执行 = 更新代码并重启（.env、数据库、证书都会保留）
# =====================================================================
set -euo pipefail

APP_NAME=paycard
APP_DIR=/opt/paycard
APP_USER=paycard
REPO_URL="${REPO_URL:-https://github.com/dimowan9/paycard.git}"
ADMIN_PORT="${ADMIN_PORT:-8888}"
WEB_BIND=127.0.0.1:5000
ADMIN_BIND=127.0.0.1:5001
WEBROOT=/var/www/certbot
NGINX_CONF=/etc/nginx/conf.d/paycard.conf

c_green='\033[1;32m'; c_yellow='\033[1;33m'; c_red='\033[1;31m'; c_off='\033[0m'
log()  { echo -e "${c_green}==>${c_off} $*"; }
warn() { echo -e "${c_yellow}[注意]${c_off} $*"; }
die()  { echo -e "${c_red}[错误]${c_off} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请使用 root 运行：sudo bash install.sh"
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  [[ "${ID:-}${ID_LIKE:-}" =~ (rocky|rhel|almalinux|centos|fedora) ]] || warn "未检测到 Rocky/RHEL 系统（当前：${PRETTY_NAME:-unknown}），继续尝试…"
fi

# ---------------------------------------------------------------- 参数
CONF_SAVE=/etc/paycard-install.conf
[[ -f $CONF_SAVE ]] && . "$CONF_SAVE"
if [[ -z "${DOMAIN:-}" ]]; then read -rp "请输入网站域名（已解析到本机 IP，如 pay.example.com）：" DOMAIN; fi
if [[ -z "${EMAIL:-}" ]];  then read -rp "请输入邮箱（用于 Let's Encrypt 证书到期提醒）：" EMAIL; fi
if [[ -z "${ADMIN_ALLOW_IPS+x}" ]]; then
  read -rp "后台 ${ADMIN_PORT} 端口允许访问的 IP（空格分隔，直接回车=暂不限制）：" ADMIN_ALLOW_IPS || true
fi
DOMAIN="${DOMAIN,,}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
[[ "$DOMAIN" =~ ^[a-z0-9.-]+\.[a-z]{2,}$ ]] || die "域名格式不正确：$DOMAIN"
[[ "$EMAIL" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || die "邮箱格式不正确：$EMAIL"
ADMIN_ALLOW_IPS="$(echo "${ADMIN_ALLOW_IPS:-}" | tr ',' ' ' | xargs || true)"
cat > "$CONF_SAVE" <<EOF
DOMAIN="$DOMAIN"
EMAIL="$EMAIL"
ADMIN_ALLOW_IPS="$ADMIN_ALLOW_IPS"
EOF
chmod 600 "$CONF_SAVE"

# ---------------------------------------------------------------- 1. 软件包
log "安装系统依赖…"
dnf -y install epel-release >/dev/null 2>&1 || warn "epel-release 安装失败，certbot 将改用 pip 安装"
dnf -y install nginx mariadb-server mariadb python3 python3-pip git rsync openssl \
               firewalld policycoreutils-python-utils curl tar >/dev/null

if ! command -v certbot >/dev/null 2>&1; then
  if ! dnf -y install certbot >/dev/null 2>&1; then
    log "通过 pip 安装 certbot…"
    python3 -m venv /opt/certbot
    /opt/certbot/bin/pip install -q --upgrade pip certbot
    ln -sf /opt/certbot/bin/certbot /usr/local/bin/certbot
  fi
fi
command -v certbot >/dev/null || die "certbot 安装失败"

# ---------------------------------------------------------------- 2. 代码
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo /nonexistent)"
if [[ ! -f "$SRC_DIR/app/web.py" ]]; then
  log "从 $REPO_URL 拉取代码…"
  TMP_SRC=$(mktemp -d)
  git clone --depth 1 "$REPO_URL" "$TMP_SRC/src"
  SRC_DIR="$TMP_SRC/src"
fi

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"
if [[ "$(realpath "$SRC_DIR")" != "$(realpath "$APP_DIR")" ]]; then
  log "部署代码到 $APP_DIR …"
  rsync -a --delete --exclude '.git' --exclude '.env' --exclude 'venv' --exclude '*.db' "$SRC_DIR/" "$APP_DIR/"
fi

# ---------------------------------------------------------------- 3. Python 环境
log "创建 Python 虚拟环境并安装依赖…"
[[ -x "$APP_DIR/venv/bin/python" ]] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# ---------------------------------------------------------------- 4. 数据库 + .env
log "配置 MariaDB…"
systemctl enable --now mariadb >/dev/null

ENV_FILE="$APP_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  log "生成 .env（数据库密码、会话密钥、卡片加密密钥）…"
  CARD_KEY=$("$APP_DIR/venv/bin/python" -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
  cat > "$ENV_FILE" <<EOF
# PayCard 配置（由 install.sh 生成）。请备份本文件：丢失 CARD_KEY 将无法解密已录入的卡片！
SITE_NAME=PayCard
KEFU_URL=https://t.me/poobwjj
USDT_ADDRESS=TCB4aq2zhy1Jt4JHKhfWRcuDB8EW2rWan7
FEE_RATE=0.02
MIN_RECHARGE=10

DB_ENGINE=mysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=paycard
DB_USER=paycard
DB_PASS=$(openssl rand -hex 16)

SECRET_KEY=$(openssl rand -hex 32)
ADMIN_SECRET_KEY=$(openssl rand -hex 32)
CARD_KEY=$CARD_KEY
COOKIE_SECURE=1
EOF
fi
chown "$APP_USER:$APP_USER" "$ENV_FILE"; chmod 600 "$ENV_FILE"

getenv() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }
DB_NAME=$(getenv DB_NAME); DB_USER=$(getenv DB_USER); DB_PASS=$(getenv DB_PASS)
[[ "$DB_NAME" =~ ^[A-Za-z0-9_]+$ && "$DB_USER" =~ ^[A-Za-z0-9_]+$ && "$DB_PASS" =~ ^[A-Za-z0-9]+$ ]] \
  || die ".env 中 DB_NAME/DB_USER/DB_PASS 只能包含字母数字下划线"

mysql -uroot <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
ALTER USER '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 755 "$APP_DIR"
log "初始化数据表 / 默认管理员…"
(cd "$APP_DIR" && runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/python" manage.py init)

# ---------------------------------------------------------------- 5. systemd
log "注册 systemd 服务…"
make_unit() { # name bind module workers
cat > "/etc/systemd/system/$1.service" <<EOF
[Unit]
Description=PayCard $1
After=network.target mariadb.service
Wants=mariadb.service

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/gunicorn --workers $4 --bind $2 --access-logfile - --error-logfile - --forwarded-allow-ips=127.0.0.1 $3
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
}
make_unit paycard-web   "$WEB_BIND"   "app.web:app"   3
make_unit paycard-admin "$ADMIN_BIND" "app.admin:app" 2
systemctl daemon-reload
systemctl enable paycard-web paycard-admin >/dev/null
systemctl restart paycard-web paycard-admin

# ---------------------------------------------------------------- 6. SELinux / 防火墙
if command -v getenforce >/dev/null && [[ "$(getenforce)" != "Disabled" ]]; then
  log "配置 SELinux（允许 nginx 反代、监听 ${ADMIN_PORT}）…"
  setsebool -P httpd_can_network_connect 1
  semanage port -a -t http_port_t -p tcp "$ADMIN_PORT" 2>/dev/null \
    || semanage port -m -t http_port_t -p tcp "$ADMIN_PORT" 2>/dev/null || true
fi

log "配置防火墙…"
systemctl enable --now firewalld >/dev/null
firewall-cmd -q --permanent --add-service=http
firewall-cmd -q --permanent --add-service=https
# 先清掉旧的 8888 规则，再按当前配置添加
firewall-cmd -q --permanent --remove-port="${ADMIN_PORT}/tcp" 2>/dev/null || true
while read -r rule; do
  [[ -n "$rule" ]] && firewall-cmd -q --permanent --remove-rich-rule="$rule" || true
done < <(firewall-cmd --permanent --list-rich-rules | grep "port=\"${ADMIN_PORT}\"" || true)
if [[ -n "$ADMIN_ALLOW_IPS" ]]; then
  for ip in $ADMIN_ALLOW_IPS; do
    fam=ipv4; [[ "$ip" == *:* ]] && fam=ipv6
    firewall-cmd -q --permanent --add-rich-rule="rule family=\"$fam\" source address=\"$ip\" port port=\"${ADMIN_PORT}\" protocol=\"tcp\" accept"
  done
else
  firewall-cmd -q --permanent --add-port="${ADMIN_PORT}/tcp"
fi
firewall-cmd -q --reload

# ---------------------------------------------------------------- 7. nginx + 证书
mkdir -p "$WEBROOT"
command -v restorecon >/dev/null && restorecon -R "$WEBROOT" 2>/dev/null || true

PROXY_HEADERS='proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;'

CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
if [[ ! -f "$CERT_DIR/fullchain.pem" ]]; then
  log "写入临时 HTTP 配置用于证书验证…"
  cat > "$NGINX_CONF" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root $WEBROOT; }
    location / {
        proxy_pass http://$WEB_BIND;
        $PROXY_HEADERS
    }
}
EOF
  nginx -t
  systemctl enable nginx >/dev/null
  systemctl restart nginx

  log "申请 Let's Encrypt 免费证书：$DOMAIN …"
  if ! certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" --email "$EMAIL" \
        --agree-tos --non-interactive --no-eff-email; then
    die "证书申请失败。请检查：1) 域名 $DOMAIN 的 A 记录是否指向本机公网 IP；2) 云服务器安全组是否放行 80/443 端口。修正后重新执行 bash install.sh"
  fi
else
  log "证书已存在，跳过申请（到期前自动续期）"
fi

log "写入正式 nginx 配置（HTTPS + 后台 ${ADMIN_PORT}）…"
ADMIN_ACL=""
if [[ -n "$ADMIN_ALLOW_IPS" ]]; then
  for ip in $ADMIN_ALLOW_IPS; do ADMIN_ACL+="    allow $ip;"$'\n'; done
  ADMIN_ACL+="    deny all;"
fi

cat > "$NGINX_CONF" <<EOF
# 由 install.sh 生成，重新执行会覆盖
limit_req_zone \$binary_remote_addr zone=pc_auth:10m rate=10r/m;
limit_req_zone \$binary_remote_addr zone=pc_admin_auth:10m rate=5r/m;
limit_req_status 429;

# HTTP -> HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root $WEBROOT; }
    location / { return 301 https://\$host\$request_uri; }
}

# 用户站
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name $DOMAIN;

    ssl_certificate     $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:pcSSL:10m;
    ssl_session_timeout 1d;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy same-origin always;
    client_max_body_size 1m;
    server_tokens off;

    # 登录 / 注册限流，防止暴力破解与恶意注册
    location ~ ^/(login|register)\$ {
        limit_req zone=pc_auth burst=5 nodelay;
        proxy_pass http://$WEB_BIND;
        $PROXY_HEADERS
    }
    location /static/ {
        proxy_pass http://$WEB_BIND;
        $PROXY_HEADERS
        expires 7d;
    }
    location / {
        proxy_pass http://$WEB_BIND;
        $PROXY_HEADERS
    }
}

# 后台管理（HTTPS，端口 $ADMIN_PORT）
server {
    listen $ADMIN_PORT ssl;
    listen [::]:$ADMIN_PORT ssl;
    http2 on;
    server_name $DOMAIN _;

    ssl_certificate     $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:pcSSL:10m;

    # 用 HTTP 访问 HTTPS 端口时自动跳转
    error_page 497 =301 https://\$host:$ADMIN_PORT\$request_uri;

    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Robots-Tag "noindex, nofollow" always;
    client_max_body_size 1m;
    server_tokens off;

$ADMIN_ACL

    location = /login {
        limit_req zone=pc_admin_auth burst=3 nodelay;
        proxy_pass http://$ADMIN_BIND;
        $PROXY_HEADERS
    }
    location / {
        proxy_pass http://$ADMIN_BIND;
        $PROXY_HEADERS
    }
}
EOF
nginx -t
systemctl enable nginx >/dev/null
systemctl reload nginx || systemctl restart nginx

# ---------------------------------------------------------------- 8. 证书自动续期
log "配置证书自动续期…"
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
if systemctl list-unit-files 2>/dev/null | grep -q '^certbot-renew.timer'; then
  systemctl enable --now certbot-renew.timer >/dev/null
else
  cat > /etc/systemd/system/paycard-certbot-renew.service <<'EOF'
[Unit]
Description=Renew Let's Encrypt certificates
[Service]
Type=oneshot
ExecStart=/usr/bin/env certbot renew --quiet
EOF
  cat > /etc/systemd/system/paycard-certbot-renew.timer <<'EOF'
[Unit]
Description=Twice daily certbot renew
[Timer]
OnCalendar=*-*-* 03,15:17:00
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now paycard-certbot-renew.timer >/dev/null
fi

# ---------------------------------------------------------------- 9. 自检
sleep 2
ok=1
for s in mariadb paycard-web paycard-admin nginx; do
  if systemctl is-active --quiet "$s"; then echo "   ✓ $s 运行中"; else echo "   ✗ $s 未运行（journalctl -u $s -n 50 查看日志）"; ok=0; fi
done
code=$(curl -s -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/" || true)
echo "   用户站 HTTPS 自检：HTTP $code"

echo
echo -e "${c_green}=================== 部署完成 ===================${c_off}"
echo "  用户站：  https://$DOMAIN"
echo "  后台：    https://$DOMAIN:$ADMIN_PORT    （默认账号 admin / a181818，请登录后立即修改密码）"
if [[ -n "$ADMIN_ALLOW_IPS" ]]; then
  echo "  后台仅允许以下 IP 访问：$ADMIN_ALLOW_IPS"
else
  warn "后台 $ADMIN_PORT 端口当前对所有 IP 开放！限制 IP 请执行："
  echo "        ADMIN_ALLOW_IPS=\"你的IP\" bash $APP_DIR/install.sh"
fi
echo "  配置文件：$APP_DIR/.env   （务必备份，丢失 CARD_KEY 将无法解密卡片）"
echo "  更新代码：cd $APP_DIR && git pull 后执行 bash install.sh；或直接重新执行 install.sh"
echo "  查看日志：journalctl -u paycard-web -f   /   journalctl -u paycard-admin -f"
[[ $ok -eq 1 ]] || exit 1
