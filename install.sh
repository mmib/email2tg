#!/usr/bin/env bash
set -euo pipefail

APP_ROOT=/opt/dahua-telegram
APP_USER=dahua-telegram
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! id -u "$APP_USER" >/dev/null 2>&1; then
  if command -v useradd >/dev/null 2>&1; then
    sudo useradd --system --home "$APP_ROOT" --shell /usr/sbin/nologin "$APP_USER" || true
  else
    APP_USER=nobody
  fi
fi

sudo mkdir -p "$APP_ROOT/logs"
sudo python3 -m venv "$APP_ROOT/venv"
sudo "$APP_ROOT/venv/bin/pip" install --upgrade pip
sudo "$APP_ROOT/venv/bin/pip" install -r "$REPO_ROOT/requirements.txt"

sudo install -m 0755 "$REPO_ROOT/forward.py" "$APP_ROOT/forward.py"
if [[ ! -f "$REPO_ROOT/config.env" ]]; then
  cp "$REPO_ROOT/config.env.example" "$REPO_ROOT/config.env"
fi

sudo chown -R "$APP_USER":"$APP_USER" "$APP_ROOT"
chmod 0600 "$REPO_ROOT/config.env" 2>/dev/null || true

cat <<'EOF'
Installation complete.

Next steps:
1. Edit config.env with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
2. Add to /etc/postfix/main.cf:
   virtual_alias_domains = mib.photo
   virtual_alias_maps = hash:/etc/postfix/virtual
3. Add to /etc/postfix/virtual:
   dahua@mib.photo  dahua-cam
4. Add to /etc/aliases:
   dahua-cam: "|/opt/dahua-telegram/forward.py"
5. Apply mail changes:
   postmap /etc/postfix/virtual
   newaliases
   systemctl reload postfix
6. Test:
   cat samples/dahua_motion.eml | /opt/dahua-telegram/forward.py

DNS records:
  mib.photo.       IN MX  10 mail.mib.photo.
  mail.mib.photo.  IN A      YOUR_VPS_IP
  mib.photo.       IN TXT    "v=spf1 ip4:YOUR_VPS_IP -all"

Telegram:
  Create a bot with @BotFather, add it to the target chat, then run:
  curl https://api.telegram.org/bot<TOKEN>/getUpdates
EOF

