#!/bin/sh
# Install the systemd units and the config skeleton. Run from a checkout at /opt/nestsnap: sudo deploy/install.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
command -v uv >/dev/null || { echo "uv must be on root's PATH (e.g. /usr/local/bin/uv)"; exit 1; }
install -d -m 700 /etc/nestsnap
install -d /var/cache/nestsnap
[ -e /etc/nestsnap/nestsnap.env ] || install -m 644 "$here/nestsnap.env.example" /etc/nestsnap/nestsnap.env
[ -e /etc/nestsnap/http_path ] || (umask 077; uv run --no-project python -c "import secrets; print(secrets.token_urlsafe(16))" > /etc/nestsnap/http_path)
install -m 644 "$here"/systemd/*.service "$here"/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nestsnap-mirror.timer nestsnap-timelapse.timer nestsnap-timelapse-weekly.timer
echo "Now fill in /etc/nestsnap/{nest.env,ha.env} (see README) and deploy deploy/compose.yml."
