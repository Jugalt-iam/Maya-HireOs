#!/usr/bin/env bash
#
# install.sh  --  put the Maya_OS demo on the VPS, behind HTTPS.
#
#   scp -r deploy maya-os.tar root@<your-vps-ip>:/root/
#   ssh root@<your-vps-ip>
#   bash /root/deploy/install.sh
#
# This is the DEMO instance. It carries an invented archive and none of your
# own history. The personal instance stays on your own machine and is never
# exposed. That separation is the point, so this script refuses to run if it
# finds a real archive already in place.

set -euo pipefail

APP_DIR=/opt/maya-os
HOSTNAME_FQDN="${MAYA_HOST:?set MAYA_HOST to your own VPS hostname, e.g. export MAYA_HOST=demo.example.com}"
SERVICE=maya-os

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# --------------------------------------------------------------- guard rails
if [ -f "$APP_DIR/MyData/conversations.json" ]; then
  size=$(stat -c%s "$APP_DIR/MyData/conversations.json")
  if [ "$size" -gt 5000000 ]; then
    echo "REFUSING TO RUN."
    echo "There is a ${size}-byte archive at $APP_DIR/MyData/conversations.json."
    echo "That is too big to be the demo archive, so it looks like a real one."
    echo "This box is public. Move it off, or delete it deliberately, then rerun."
    exit 1
  fi
fi

say "1/8  system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl ufw debian-keyring \
    debian-archive-keyring apt-transport-https

say "2/8  ollama"
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
# Loopback only. The brain reaches it; the internet never does.
mkdir -p /etc/systemd/system/ollama.service.d
cat >/etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
EOF
systemctl daemon-reload
systemctl enable --now ollama
sleep 3

say "3/8  models"
ollama pull bge-m3
ollama pull qwen2.5-coder:3b

say "4/8  application"
mkdir -p "$APP_DIR"
if [ -f /root/maya-os.tar ]; then
  tar -xf /root/maya-os.tar -C /tmp
  cp -r /tmp/ClaudeAPI/. "$APP_DIR/"
  rm -rf /tmp/ClaudeAPI
fi
cd "$APP_DIR"
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q fastapi uvicorn requests openai

say "5/8  demo archive"
mkdir -p "$APP_DIR/MyData" "$APP_DIR/logs" "$APP_DIR/queue" "$APP_DIR/.lanes"
rm -rf "$APP_DIR/.claude_index"
python3 /root/deploy/make-demo-archive.py "$APP_DIR/MyData/conversations.json"

say "6/8  secrets"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi
NEW_KEY="sk-maya-$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)"
python3 - "$APP_DIR" "$NEW_KEY" <<'PY'
import io, re, sys
app, key = sys.argv[1], sys.argv[2]
for name in ("server.py", "claude-os/agent.py"):
    p = app + "/" + name
    s = io.open(p, encoding="utf-8").read()
    s = re.sub(r'API_KEY = "sk-[^"]*"', 'API_KEY = "%s"' % key, s, count=1)
    io.open(p, "w", encoding="utf-8").write(s)
print("rotated the API key in server.py and agent.py")
PY
echo "$NEW_KEY" > "$APP_DIR/.apikey"
chmod 600 "$APP_DIR/.apikey" "$APP_DIR/.env"

say "7/8  service"
cat >/etc/systemd/system/${SERVICE}.service <<EOF
[Unit]
Description=Maya_OS demo brain
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/server.py
Restart=always
RestartSec=5
StandardOutput=append:${APP_DIR}/logs/service.log
StandardError=append:${APP_DIR}/logs/service.log

[Install]
WantedBy=multi-user.target
EOF
# The brain listens on loopback only. Caddy is the only thing facing the world.
sed -i 's/^HOST = "0.0.0.0"/HOST = "127.0.0.1"/' "$APP_DIR/server.py"
systemctl daemon-reload
systemctl enable --now ${SERVICE}

say "8/8  https"
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy
fi
sed "s/__HOST__/${HOSTNAME_FQDN}/g" /root/deploy/Caddyfile > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

ufw allow 22/tcp  >/dev/null
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

say "done"
echo
echo "  demo:    https://${HOSTNAME_FQDN}/ui"
echo "  health:  https://${HOSTNAME_FQDN}/health"
echo "  api key: ${NEW_KEY}"
echo "           also saved to ${APP_DIR}/.apikey"
echo
echo "  logs:    journalctl -u ${SERVICE} -f"
echo "           tail -f ${APP_DIR}/logs/server.log"
echo
echo "  Lane keys are NOT set. Add them to ${APP_DIR}/.env then:"
echo "           systemctl restart ${SERVICE}"
echo
echo "  Ollama is bound to loopback. Only the brain can reach it."
echo "  The brain is bound to loopback. Only Caddy can reach it."
