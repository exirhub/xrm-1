#!/usr/bin/env bash

# Wait for cloud-init networking and repair DNS before the first GitHub request.
github_dns_ready() {
  getent ahostsv4 github.com >/dev/null 2>&1 &&
    getent ahostsv4 raw.githubusercontent.com >/dev/null 2>&1
}

ensure_github_dns() {
  local attempt interface

  for attempt in 1 2 3 4 5 6; do
    github_dns_ready && return 0
    echo "Waiting for DNS to become available (${attempt}/6)..."
    sleep 5
  done

  echo "DNS is unavailable; applying temporary fallback resolvers..."
  if command -v systemctl >/dev/null 2>&1 && command -v resolvectl >/dev/null 2>&1; then
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

  for attempt in 1 2 3 4 5 6; do
    github_dns_ready && return 0
    sleep 2
  done

  echo "ERROR: github.com could not be resolved. Check the server network/DNS configuration." >&2
  return 1
}

ensure_github_dns || exit 1

cd /root
wget https://github.com/exirhub/xrm-1/raw/refs/heads/main/x-ui.db
echo "n" | bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)
sudo systemctl stop x-ui
sudo chmod +x /root/x-ui.db
sudo cp /root/x-ui.db /etc/x-ui/x-ui.db
sudo systemctl start x-ui
ufw disable
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
sudo echo '/swapfile none swap sw 0 0' >> /etc/fstab

echo "Applying TCP buffer optimizations..."
sudo sysctl -w net.core.rmem_max=67108864
sudo sysctl -w net.core.wmem_max=67108864
sudo sysctl -w net.core.netdev_max_backlog=100000
echo "net.ipv4.tcp_keepalive_time = 60" >> /etc/sysctl.conf
echo "net.ipv4.tcp_keepalive_intvl = 10" >> /etc/sysctl.conf
echo "net.ipv4.tcp_keepalive_probes = 6" >> /etc/sysctl.conf
sysctl -p
# Persist changes in sysctl.conf
echo "Saving settings to /etc/sysctl.conf..."
sudo cat <<EOF >> /etc/sysctl.conf
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.core.netdev_max_backlog = 100000
EOF
# Apply settings
sysctl -p

echo "TCP buffer optimizations applied successfully!"
sudo cp /root/x-ui.db /etc/x-ui/x-ui.db
sudo systemctl restart x-ui
wget https://raw.githubusercontent.com/exirhub/exirvpn-balancer-config/refs/heads/main/receiver.sh
chmod +x receiver.sh
./receiver.sh

systemctl stop x-ui
//EDIT BY:MEHTI v3.6
while pgrep -x x-ui >/dev/null; do
  sleep 1
done

rm -f /etc/x-ui/x-ui.db-wal
rm -f /etc/x-ui/x-ui.db-shm

install \
  -o root \
  -g root \
  -m 600 \
  /root/x-ui.db \
  /etc/x-ui/x-ui.db

sync

systemctl start x-ui
sleep 5

systemctl status x-ui --no-pager
journalctl -u x-ui -n 100 --no-pager
