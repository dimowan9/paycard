# PayCard · 虚拟卡发卡站

Flask + MariaDB 的虚拟卡发卡网站，含用户站与后台管理，一键部署到 Rocky Linux 10。

## 功能

**用户站（443 端口）**
- 邮箱 + 自定义密码注册；同一 IP 仅能注册一个账号（防恶意注册）
- 登录校验；被封禁账号登录提示「登录失败，请联系客服」
- 自定义金额充值（USDT TRC20，手续费 2%），填写 TxID 等待后台确认
- 余额购买套餐（$88 一张虚拟卡 / $158 两张虚拟卡 / $158 一张实体卡，均含开卡费）
- 「我的账户」查看余额、卡号、有效期、CVV 安全码、订单记录

**后台（8888 端口，HTTPS）**
- 默认账号 `admin / a181818`（部署后请立即修改密码）
- 查看所有注册账号、注册 IP、最近登录 IP；封禁 / 解封按钮
- 卡库：手动录入卡号 / CVV / 有效期（加密存储，卡号去重）
- 发卡：把指定卡片下发给指定用户，用户页面即时可见
- 撤回卡：用户页面对应卡片消失，卡可放回库存
- 充值订单确认到账（自动加余额）/ 驳回；购卡订单取消退款

## 一键部署（Rocky Linux 10，root）

```bash
dnf -y install git
git clone https://github.com/dimowan9/paycard.git
cd paycard
bash install.sh
```

按提示输入 **域名**（需已解析到服务器 IP）、**邮箱**、**后台允许访问的 IP**。脚本自动完成：

1. 安装 nginx / MariaDB / Python / certbot
2. 部署到 `/opt/paycard`，建库建表，创建默认管理员
3. 注册 systemd 服务 `paycard-web`、`paycard-admin`
4. 申请 **Let's Encrypt 免费证书** 并写入 nginx，配置自动续期
5. 防火墙放行 80 / 443 / 8888（8888 可按 IP 白名单放行）、SELinux 规则
6. 登录 / 注册接口 nginx 限流

非交互部署：

```bash
DOMAIN=pay.example.com EMAIL=you@example.com ADMIN_ALLOW_IPS="1.2.3.4" bash install.sh
```

重复执行 `install.sh` = 更新代码并重启，`.env`、数据库、证书均保留。

## 常用命令

```bash
journalctl -u paycard-web -f        # 用户站日志
journalctl -u paycard-admin -f      # 后台日志
systemctl restart paycard-web paycard-admin
certbot renew --dry-run             # 测试证书续期
cd /opt/paycard && runuser -u paycard -- venv/bin/python manage.py reset-admin 新密码
```

## 重要配置（/opt/paycard/.env）

| 变量 | 说明 |
|---|---|
| `USDT_ADDRESS` | 收款地址（改后重启服务） |
| `KEFU_URL` | 客服 Telegram 链接 |
| `FEE_RATE` | 充值手续费率，默认 0.02 |
| `CARD_KEY` | 卡号/CVV 加密密钥，**务必备份**，丢失无法解密已录入卡片 |

二维码图片：`app/static/img/qr.jpg`（更换收款地址时记得同步换图）。

## 本地开发

```bash
pip install -r requirements.txt
export DB_ENGINE=sqlite COOKIE_SECURE=0 \
  CARD_KEY=$(python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
python manage.py init
flask --app app.web run --port 5000      # 用户站
flask --app app.admin run --port 5001    # 后台
```
