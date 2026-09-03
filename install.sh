#!/usr/bin/env bash

# Keep DNS working when apt/cloud-init restarts systemd-resolved during setup.
github_dns_ready() {
  getent ahostsv4 github.com >/dev/null 2>&1 &&
    getent ahostsv4 api.github.com >/dev/null 2>&1 &&
    getent ahostsv4 raw.githubusercontent.com >/dev/null 2>&1
}

configure_persistent_dns() {
  local interface

  if command -v systemctl >/dev/null 2>&1 && command -v resolvectl >/dev/null 2>&1; then
    install -d -m 755 /etc/systemd/resolved.conf.d
    cat > /etc/systemd/resolved.conf.d/99-exir-dns.conf <<'EOF'
[Resolve]
DNS=1.1.1.1 8.8.8.8
FallbackDNS=9.9.9.9 8.8.4.4
EOF
    systemctl restart systemd-resolved 2>/dev/null || true
    resolvectl flush-caches 2>/dev/null || true
    interface="$(ip route show default 2>/dev/null | awk 'NR == 1 { print $5 }')"
    if [ -n "$interface" ]; then
      resolvectl dns "$interface" 1.1.1.1 8.8.8.8 2>/dev/null || true
      resolvectl domain "$interface" '~.' 2>/dev/null || true
    fi
  elif [ -w /etc/resolv.conf ]; then
    [ -e /etc/resolv.conf.exir-backup ] || cp -L /etc/resolv.conf /etc/resolv.conf.exir-backup
    printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > /etc/resolv.conf
  fi
}

ensure_github_dns() {
  local attempt

  for attempt in 1 2 3 4 5 6; do
    github_dns_ready && return 0
    echo "Waiting for DNS to become available (${attempt}/6)..."
    sleep 5
  done

  echo "DNS is unavailable; applying persistent fallback resolvers..."
  configure_persistent_dns

  for attempt in 1 2 3 4 5 6; do
    github_dns_ready && return 0
    sleep 2
  done

  echo "ERROR: GitHub hosts could not be resolved. Check the server network/DNS configuration." >&2
  return 1
}

download_file() {
  local url="$1" destination="$2"
  ensure_github_dns || return 1
  curl --fail --location \
    --retry 10 --retry-all-errors --retry-delay 3 \
    --connect-timeout 15 --max-time 300 \
    --output "$destination" "$url"
}

# Configure DNS before apt can restart the resolver and erase per-link settings.
configure_persistent_dns
ensure_github_dns || exit 1

cd /root || exit 1

download_file \
  https://raw.githubusercontent.com/exirhub/xrm-1/main/x-ui.db \
  /root/x-ui.db || exit 1

xui_installer="$(mktemp)" || exit 1
trap 'rm -f "$xui_installer"' EXIT
download_file \
  https://raw.githubusercontent.com/MHSanaei/3x-ui/main/install.sh \
  "$xui_installer" || exit 1

if ! XUI_NONINTERACTIVE=1 bash "$xui_installer"; then
  echo "ERROR: 3x-ui installation failed; no database changes were applied." >&2
  exit 1
fi

# apt may have restarted networking services during the official installer.
ensure_github_dns || exit 1

if ! systemctl is-active --quiet x-ui; then
  echo "ERROR: x-ui.service is not running after installation." >&2
  systemctl status x-ui --no-pager 2>/dev/null || true
  exit 1
fi

systemctl stop x-ui
install -d -o root -g root -m 700 /etc/x-ui
install -o root -g root -m 600 /root/x-ui.db /etc/x-ui/x-ui.db
systemctl start x-ui
ufw disable || true

if ! swapon --show=NAME --noheadings 2>/dev/null | grep -qx '/swapfile'; then
  if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
  fi
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
fi
grep -qF '/swapfile none swap sw 0 0' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

echo "Applying TCP buffer optimizations..."
cat > /etc/sysctl.d/99-exir-network.conf <<'EOF'
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.core.netdev_max_backlog = 100000
net.ipv4.tcp_keepalive_time = 60
net.ipv4.tcp_keepalive_intvl = 10
net.ipv4.tcp_keepalive_probes = 6
EOF
sysctl --system
echo "TCP buffer optimizations applied successfully!"

download_file \
  https://raw.githubusercontent.com/exirhub/exirvpn-balancer-config/main/receiver.sh \
  /root/receiver.sh || exit 1
chmod +x /root/receiver.sh
/root/receiver.sh

systemctl stop x-ui
# EDIT BY: MEHTI v3.6
while pgrep -x x-ui >/dev/null; do
  sleep 1
done

rm -f /etc/x-ui/x-ui.db-wal /etc/x-ui/x-ui.db-shm
install -o root -g root -m 600 /root/x-ui.db /etc/x-ui/x-ui.db
sync

systemctl start x-ui
sleep 5

systemctl status x-ui --no-pager
journalctl -u x-ui -n 100 --no-pager
